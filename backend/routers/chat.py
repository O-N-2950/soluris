"""Chat endpoint — RAG pipeline with quota enforcement and jurisdiction/domain filters"""
import json
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List

from backend.db import database
from backend.routers.auth import get_current_user_id, check_quota
from backend.services.rag import generate_answer

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    history: Optional[List[ChatMessage]] = []
    jurisdiction: Optional[str] = None   # 'CH', 'GE', 'VD', etc.
    legal_domain: Optional[str] = None   # 'droit_civil', 'droit_penal', etc.


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    user_id = await get_current_user_id(request)

    # ── Quota check (trial + plan limits) ──
    quota_info = await check_quota(user_id)

    async with database.pool.acquire() as conn:
        # Get or create conversation
        if req.conversation_id:
            conv = await conn.fetchrow(
                "SELECT id FROM conversations WHERE id = $1::uuid AND user_id = $2::uuid",
                req.conversation_id, user_id,
            )
            if not conv:
                raise HTTPException(404, "Conversation non trouvée")
            conv_id = req.conversation_id
        else:
            # Create new conversation with title from first message
            title = req.message[:80] + ("…" if len(req.message) > 80 else "")
            conv_id = await conn.fetchval(
                "INSERT INTO conversations (user_id, title) VALUES ($1::uuid, $2) RETURNING id",
                user_id, title,
            )
            conv_id = str(conv_id)

        # Save user message
        await conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES ($1::uuid, 'user', $2)",
            conv_id, req.message,
        )

        # Generate answer via RAG pipeline (with optional filters)
        result = await generate_answer(
            req.message,
            req.history or [],
            jurisdiction=req.jurisdiction,
            legal_domain=req.legal_domain,
        )

        # Save assistant message
        await conn.execute(
            """INSERT INTO messages (conversation_id, role, content, sources, tokens_used, rag_chunks)
               VALUES ($1::uuid, 'assistant', $2, $3, $4, $5)""",
            conv_id, result["response"], json.dumps(result.get("sources", [])),
            result.get("tokens", 0), result.get("rag_chunks", 0),
        )

        # Update conversation timestamp
        await conn.execute(
            "UPDATE conversations SET updated_at = NOW() WHERE id = $1::uuid", conv_id
        )

        # Increment query count
        await conn.execute(
            "UPDATE users SET queries_this_month = queries_this_month + 1 WHERE id = $1::uuid",
            user_id,
        )

    return {
        "response": result["response"],
        "sources": result.get("sources", []),
        "conversation_id": conv_id,
        "confidence": result.get("confidence", "none"),
        "rag_chunks": result.get("rag_chunks", 0),
        "quota": {
            "used": quota_info["quota_used"] + 1,
            "limit": quota_info["quota_limit"],
            "plan": quota_info["plan"],
            "trial_days_left": quota_info.get("trial_days_left"),
        },
    }
