"""Conversations listing and message retrieval"""
import json
from fastapi import APIRouter, HTTPException, Request

from backend.db import database
from backend.routers.auth import get_current_user_id

router = APIRouter()


@router.get("/conversations")
async def list_conversations(request: Request):
    user_id = await get_current_user_id(request)
    async with database.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE user_id = $1::uuid ORDER BY updated_at DESC LIMIT 50",
            user_id,
        )
    return [dict(r) for r in rows]


@router.get("/conversations/{conv_id}/messages")
async def get_messages(conv_id: str, request: Request):
    user_id = await get_current_user_id(request)
    async with database.pool.acquire() as conn:
        conv = await conn.fetchrow(
            "SELECT id FROM conversations WHERE id = $1::uuid AND user_id = $2::uuid",
            conv_id, user_id,
        )
        if not conv:
            raise HTTPException(404, "Conversation non trouvée")
        rows = await conn.fetch(
            "SELECT role, content, sources, created_at FROM messages WHERE conversation_id = $1::uuid ORDER BY created_at ASC",
            conv_id,
        )
    result = []
    for r in rows:
        msg = dict(r)
        if isinstance(msg.get("sources"), str):
            msg["sources"] = json.loads(msg["sources"])
        result.append(msg)
    return result
