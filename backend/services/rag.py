# -*- coding: utf-8 -*-
"""RAG Pipeline - Retrieval Augmented Generation for Swiss law

Architecture:
  1. User question -> Cohere multilingual embedding
  2. pgvector cosine similarity search in legal_chunks
  3. Top-K chunks injected into Claude system prompt
  4. Claude generates grounded answer with verifiable citations
  5. Parse sources and confidence score
"""
import os
import json
import logging
import httpx
from typing import List, Dict, Optional

from backend.db import database

log = logging.getLogger("soluris.rag")

# -- Configuration --
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
COHERE_API_KEY = os.getenv("COHERE_API_KEY", "")
EMBEDDING_MODEL = "embed-multilingual-v3.0"
EMBEDDING_DIM = 1024
TOP_K_CHUNKS = 10
CONFIDENCE_THRESHOLD = 0.35
HIGH_CONFIDENCE = 0.55
LOW_CONFIDENCE_MSG = (
    "Je n'ai pas trouve de source juridique suffisamment pertinente "
    "pour cette question dans ma base de donnees. Voici une reponse "
    "basee sur mes connaissances generales, a verifier imperativement."
)

# -- System prompts --
SYSTEM_PROMPT_WITH_RAG = """Tu es Soluris, un assistant juridique IA specialise en droit suisse.

CONTEXTE JURIDIQUE FOURNI :
{context}

REGLES STRICTES :
1. Base tes reponses PRIORITAIREMENT sur le contexte juridique fourni ci-dessus
2. Cite TOUJOURS les articles de loi et arrets exacts entre parentheses (art. X CO, ATF X XX XX)
3. Pour chaque affirmation juridique, indique la source precise du contexte
4. Si le contexte ne couvre pas la question, complete avec tes connaissances mais SIGNALE-LE clairement
5. Tu ne donnes JAMAIS de conseil juridique personnel - tu fournis de l'information juridique
6. Tu reponds en francais, sauf si l'utilisateur ecrit dans une autre langue
7. Structure tes reponses clairement avec les references entre parentheses

FORMAT DES SOURCES :
A la fin de ta reponse, ajoute un bloc JSON avec les sources utilisees :
[SOURCES]
[{{"reference": "Art. 41 CO", "title": "Responsabilite delictuelle", "url": "https://www.fedlex.admin.ch/..."}}]
[/SOURCES]"""

SYSTEM_PROMPT_NO_RAG = """Tu es Soluris, un assistant juridique IA specialise en droit suisse.

ATTENTION : La base de donnees juridique n'est pas disponible pour cette requete.
Les reponses sont basees sur tes connaissances generales du droit suisse.
Toutes les informations fournies doivent etre verifiees par l'utilisateur.

REGLES :
1. Tu reponds UNIQUEMENT sur la base du droit suisse (federal et cantonal)
2. Tu cites les references que tu connais de memoire (articles de loi, ATF)
3. Tu indiques CLAIREMENT que ces sources n'ont pas ete verifiees dans la base
4. Tu ne donnes JAMAIS de conseil juridique personnel
5. Tu reponds en francais

FORMAT DES SOURCES :
[SOURCES]
[{{"reference": "...", "title": "...", "url": "...", "verified": false}}]
[/SOURCES]"""


async def embed_text(text: str) -> Optional[List[float]]:
    """Generate embedding for a text using Cohere multilingual-v3."""
    if not COHERE_API_KEY:
        log.warning("COHERE_API_KEY not set - skipping embedding")
        return None

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.cohere.ai/v1/embed",
                headers={
                    "Authorization": f"Bearer {COHERE_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": EMBEDDING_MODEL,
                    "texts": [text],
                    "input_type": "search_query",
                    "truncate": "END",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["embeddings"][0]
    except Exception as e:
        log.error(f"Embedding failed: {e}")
        return None


async def search_legal_chunks(
    question_embedding: List[float],
    top_k: int = TOP_K_CHUNKS,
    jurisdiction: Optional[str] = None,
    legal_domain: Optional[str] = None,
    doc_type: Optional[str] = None,
) -> List[Dict]:
    """Search legal_chunks by cosine similarity using pgvector."""
    if not database.pool:
        return []

    try:
        async with database.pool.acquire() as conn:
            has_vector = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')"
            )
            if not has_vector:
                log.warning("pgvector not installed - RAG search unavailable")
                return []

            embedding_str = "[" + ",".join(str(x) for x in question_embedding) + "]"
            where_parts = ["lc.embedding IS NOT NULL"]
            params = [embedding_str, top_k]
            param_idx = 3

            if jurisdiction:
                where_parts.append(f"ld.jurisdiction = ${param_idx}")
                params.append(jurisdiction)
                param_idx += 1

            if legal_domain:
                where_parts.append(f"ld.metadata->>'legal_domain' = ${param_idx}")
                params.append(legal_domain)
                param_idx += 1

            if doc_type:
                where_parts.append(f"ld.doc_type = ${param_idx}")
                params.append(doc_type)
                param_idx += 1

            where_clause = " AND ".join(where_parts)

            rows = await conn.fetch(
                f"""
                SELECT
                    lc.chunk_text, lc.source_ref, lc.source_url,
                    ld.title AS doc_title, ld.reference AS doc_reference,
                    ld.doc_type, ld.jurisdiction, ld.url AS doc_url, ld.metadata,
                    1 - (lc.embedding <=> $1::vector) AS similarity
                FROM legal_chunks lc
                JOIN legal_documents ld ON lc.document_id = ld.id
                WHERE {where_clause}
                ORDER BY lc.embedding <=> $1::vector
                LIMIT $2
                """,
                *params,
            )

            results = []
            for row in rows:
                sim = float(row["similarity"])
                if sim >= CONFIDENCE_THRESHOLD:
                    meta = json.loads(row["metadata"]) if row["metadata"] else {}
                    results.append({
                        "chunk_text": row["chunk_text"],
                        "source_ref": row["source_ref"],
                        "source_url": row["source_url"],
                        "doc_title": row["doc_title"],
                        "doc_reference": row["doc_reference"],
                        "doc_type": row["doc_type"],
                        "doc_url": row["doc_url"],
                        "jurisdiction": row["jurisdiction"],
                        "legal_domain": meta.get("legal_domain", ""),
                        "similarity": sim,
                    })
            return results

    except Exception as e:
        log.error(f"Vector search failed: {e}")
        return []


def format_chunks_as_context(chunks: List[Dict]) -> str:
    """Format retrieved chunks into context string for system prompt."""
    if not chunks:
        return ""

    legislation = [c for c in chunks if c.get("doc_type") == "legislation"]
    jurisprudence = [c for c in chunks if c.get("doc_type") == "jurisprudence"]
    other = [c for c in chunks if c.get("doc_type") not in ("legislation", "jurisprudence")]

    parts = []

    if legislation:
        parts.append("=== LEGISLATION ===")
        for i, chunk in enumerate(legislation, 1):
            ref = chunk.get("source_ref") or chunk.get("doc_reference", "Ref.")
            url = chunk.get("source_url") or chunk.get("doc_url", "")
            sim = chunk.get("similarity", 0)
            parts.append(f"[LOI-{i}] {ref} (pertinence: {sim:.0%})\nURL: {url}\n{chunk['chunk_text']}\n")

    if jurisprudence:
        parts.append("=== JURISPRUDENCE ===")
        for i, chunk in enumerate(jurisprudence, 1):
            ref = chunk.get("source_ref") or chunk.get("doc_reference", "Ref.")
            url = chunk.get("source_url") or chunk.get("doc_url", "")
            sim = chunk.get("similarity", 0)
            parts.append(f"[ATF-{i}] {ref} (pertinence: {sim:.0%})\nURL: {url}\n{chunk['chunk_text']}\n")

    for chunk in other:
        ref = chunk.get("source_ref") or "Ref."
        parts.append(f"[SRC] {ref}\n{chunk['chunk_text']}\n")

    return "\n".join(parts)


async def generate_answer(
    question: str,
    history: List[Dict],
    jurisdiction: Optional[str] = None,
    legal_domain: Optional[str] = None,
) -> Dict:
    """Generate a legal answer using Claude API with RAG context."""

    # Step 1-2: RAG retrieval
    chunks = []
    rag_available = False

    question_embedding = await embed_text(question)
    if question_embedding:
        chunks = await search_legal_chunks(
            question_embedding,
            jurisdiction=jurisdiction,
            legal_domain=legal_domain,
        )
        if chunks:
            rag_available = True
            best_sim = chunks[0]["similarity"]
            log.info(f"RAG: {len(chunks)} chunks (best: {best_sim:.0%})")

    # Confidence assessment
    confidence = "none"
    if rag_available:
        best_sim = chunks[0]["similarity"]
        if best_sim >= HIGH_CONFIDENCE:
            confidence = "high"
        elif best_sim >= CONFIDENCE_THRESHOLD:
            confidence = "moderate"

    # Build system prompt
    if rag_available:
        context = format_chunks_as_context(chunks)
        system_prompt = SYSTEM_PROMPT_WITH_RAG.format(context=context)
    else:
        system_prompt = SYSTEM_PROMPT_NO_RAG

    # Build messages
    messages = []
    for msg in history[-8:]:
        if msg.get("role") in ("user", "assistant"):
            messages.append({"role": msg["role"], "content": msg["content"]})
    if not messages or messages[-1].get("content") != question:
        messages.append({"role": "user", "content": question})

    # Call Claude API
    if not ANTHROPIC_API_KEY:
        return {
            "response": "Cle API Anthropic non configuree. Ajoutez ANTHROPIC_API_KEY dans les variables d'environnement.",
            "sources": [], "tokens": 0, "rag_chunks": 0, "confidence": "none",
        }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": ANTHROPIC_MODEL,
                    "max_tokens": 4096,
                    "system": system_prompt,
                    "messages": messages,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        full_text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                full_text += block["text"]

        # Parse sources
        sources = []
        response_text = full_text
        if "[SOURCES]" in full_text:
            parts = full_text.split("[SOURCES]")
            response_text = parts[0].strip()
            if len(parts) > 1:
                source_block = parts[1].split("[/SOURCES]")[0].strip()
                try:
                    sources = json.loads(source_block)
                except json.JSONDecodeError:
                    pass

        for source in sources:
            source["verified"] = rag_available

        tokens = data.get("usage", {}).get("input_tokens", 0) + data.get("usage", {}).get("output_tokens", 0)

        return {
            "response": response_text,
            "sources": sources,
            "tokens": tokens,
            "rag_chunks": len(chunks),
            "rag_available": rag_available,
            "confidence": confidence,
        }

    except httpx.HTTPStatusError as e:
        return {
            "response": f"Erreur API Claude ({e.response.status_code}). Veuillez reessayer.",
            "sources": [], "tokens": 0, "rag_chunks": 0, "confidence": "none",
        }
    except Exception as e:
        log.error(f"Claude API error: {e}")
        return {
            "response": f"Erreur inattendue : {str(e)}",
            "sources": [], "tokens": 0, "rag_chunks": 0, "confidence": "none",
        }
