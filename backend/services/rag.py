"""RAG Pipeline — Retrieval Augmented Generation for Swiss law

Architecture:
  1. User question → Cohere multilingual embedding
  2. pgvector cosine similarity search in legal_chunks
  3. Top-K chunks injected into Claude system prompt
  4. Claude generates grounded answer with verifiable citations
  5. Parse sources and confidence score
"""
import os
import json
import logging
import httpx
from typing import List, Dict, Optional, Tuple

from backend.db import database

log = logging.getLogger("soluris.rag")

# ── Configuration ──
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
COHERE_API_KEY = os.getenv("COHERE_API_KEY", "")
EMBEDDING_MODEL = "embed-multilingual-v3.0"
EMBEDDING_DIM = 1024  # Cohere multilingual-v3 dimension
TOP_K_CHUNKS = 10  # Number of chunks to retrieve
CONFIDENCE_THRESHOLD = 0.35  # Minimum cosine similarity to consider relevant

# ── System prompts ──
SYSTEM_PROMPT_WITH_RAG = """Tu es Soluris, un assistant juridique IA spécialisé en droit suisse.

CONTEXTE JURIDIQUE FOURNI :
{context}

R�GLES STRICTES :
1. Base tes réponses PRIORITAIREMENT sur le contexte juridique fourni ci-dessus
2. Cite TOUJOURS les articles de loi et arrêts exacts entre parenthèses (art. X CO, ATF X XX XX)
3. Pour chaque affirmation juridique, indique la source précise du contexte
4. Si le contexte ne couvre pas la question, tu peux compléter avec tes connaissances mais SIGNALE-LE clairement : "⚠️ Cette information provient de mes connaissances générales et n'est pas vérifiée dans les sources disponibles."
5. Tu ne donnes JAMAIS de conseil juridique personnel — tu fournis de l'information juridique
6. Tu réponds en français, sauf si l'utilisateur écrit dans une autre langue
7. Structure tes réponses clairement avec les références entre parenthèses

FORMAT DES SOURCES :
À la fin de ta réponse, ajoute un bloc JSON avec les sources EFFECTIVEMENT utilisées :
[SOURCES]
[{{"reference": "Art. 41 CO", "title": "Responsabilité délictuelle", "url": "https://www.fedlex.admin.ch/..."}}]
[/SOURCES]"""

SYSTEM_PROMPT_NO_RAG = """Tu es Soluris, un assistant juridique IA spécialisé en droit suisse.

⚠️ ATTENTION : La base de données juridique n'est pas disponible pour cette requête.
Les réponses sont basées sur tes connaissances générales du droit suisse.
Toutes les informations fournies doivent être vérifiées par l'utilisateur.

R�GLES STRICTES :
1. Tu réponds UNIQUEMENT sur la base du droit suisse (fédéral et cantonal)
2. Tu cites les références que tu connais de mémoire (articles de loi, ATF)
3. Tu indiques CLAIREMENT que ces sources n'ont pas été vérifiées dans la base
4. Tu ne donnes JAMAIS de conseil juridique personnel
5. Tu réponds en français, sauf si l'utilisateur écrit dans une autre langue

FORMAT DES SOURCES :
[SOURCES]
[{{"reference": "...", "title": "...", "url": "...", "verified": false}}]
[/SOURCES]"""


async def embed_text(text: str) -> Optional[List[float]]:
    """Generate embedding for a text using Cohere multilingual-v3."""
    if not COHERE_API_KEY:
        log.warning("COHERE_API_KEY not set — skipping embedding")
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


async def embed_texts_batch(texts: List[str], input_type: str = "search_document") -> List[List[float]]:
    """Batch embed multiple texts (for document ingestion)."""
    if not COHERE_API_KEY:
        return []

    all_embeddings = []
    batch_size = 96  # Cohere max batch size

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    "https://api.cohere.ai/v1/embed",
                    headers={
                        "Authorization": f"Bearer {COHERE_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": EMBEDDING_MODEL,
                        "texts": batch,
                        "input_type": input_type,
                        "truncate": "END",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                all_embeddings.extend(data["embeddings"])
        except Exception as e:
            log.error(f"Batch embedding failed at batch {i // batch_size}: {e}")
            all_embeddings.extend([None] * len(batch))

    return all_embeddings


async def search_legal_chunks(
    question_embedding: List[float],
    top_k: int = TOP_K_CHUNKS,
    jurisdiction: Optional[str] = None,
    legal_domain: Optional[str] = None,
    doc_type: Optional[str] = None,
) -> List[Dict]:
    """Search legal_chunks by cosine similarity using pgvector.
    
    Optional filters:
      - jurisdiction: 'CH', 'GE', 'VD', etc.
      - legal_domain: 'droit_civil', 'droit_penal', etc.
      - doc_type: 'legislation', 'jurisprudence'
    """
    if not database.pool:
        return []

    try:
        async with database.pool.acquire() as conn:
            # Check if pgvector extension is active
            has_vector = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')"
            )
            if not has_vector:
                log.warning("pgvector extension not installed — RAG search unavailable")
                return []

            # Vector similarity search with optional filters
            embedding_str = "[" + ",".join(str(x) for x in question_embedding) + "]"
            
            # Build WHERE clause dynamically
            where_parts = ["lc.embedding IS NOT NULL"]
            params = [embedding_str, top_k]
            param_idx = 3
            
            if jurisdiction:
                where_parts.append(f"ld.jurisdiction = ${param_idx}")
                params.append(jurisdiction)
                param_idx += 1
            
            if legal_domain:
                where_parts.append(f"ld.legal_domain = ${param_idx}")
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
                    lc.chunk_text,
                    lc.source_ref,
                    lc.source_url,
                    lc.chunk_type,
                    ld.title AS doc_title,
                    ld.reference AS doc_reference,
                    ld.doc_type,
                    ld.jurisdiction,
                    ld.legal_domain,
                    ld.url AS doc_url,
                    ld.abstract AS doc_abstract,
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
                    results.append({
                        "chunk_text": row["chunk_text"],
                        "chunk_type": row["chunk_type"],
                        "source_ref": row["source_ref"],
                        "source_url": row["source_url"],
                        "doc_title": row["doc_title"],
                        "doc_reference": row["doc_reference"],
                        "doc_type": row["doc_type"],
                        "doc_url": row["doc_url"],
                        "doc_abstract": row["doc_abstract"],
                        "jurisdiction": row["jurisdiction"],
                        "legal_domain": row["legal_domain"],
                        "similarity": sim,
                    })

            return results

    except Exception as e:
        log.error(f"Vector search failed: {e}")
        return []


def format_chunks_as_context(chunks: List[Dict]) -> str:
    """Format retrieved chunks into a context string for the system prompt.
    Differentiates legislation from jurisprudence for better grounding.
    """
    if not chunks:
        return ""

    # Group by type for organized context
    legislation = [c for c in chunks if c.get("doc_type") == "legislation"]
    jurisprudence = [c for c in chunks if c.get("doc_type") == "jurisprudence"]
    other = [c for c in chunks if c.get("doc_type") not in ("legislation", "jurisprudence")]

    context_parts = []

    if legislation:
        context_parts.append("═══ LÉGISLATION ═══")
        for i, chunk in enumerate(legislation, 1):
            ref = chunk.get("source_ref") or chunk.get("doc_reference", "Réf. inconnue")
            url = chunk.get("source_url") or chunk.get("doc_url", "")
            sim = chunk.get("similarity", 0)
            context_parts.append(
                f"[LOI-{i}] {ref} (pertinence: {sim:.0%})\n"
                f"URL: {url}\n"
                f"{chunk['chunk_text']}\n"
            )

    if jurisprudence:
        context_parts.append("═══ JURISPRUDENCE ═══")
        for i, chunk in enumerate(jurisprudence, 1):
            ref = chunk.get("source_ref") or chunk.get("doc_reference", "Réf. inconnue")
            abstract = chunk.get("doc_abstract", "")
            url = chunk.get("source_url") or chunk.get("doc_url", "")
            sim = chunk.get("similarity", 0)
            header = f"[ATF-{i}] {ref} (pertinence: {sim:.0%})"
            if abstract:
                header += f"\nRegeste: {abstract[:300]}"
            context_parts.append(
                f"{header}\n"
                f"URL: {url}\n"
                f"{chunk['chunk_text']}\n"
            )

    for chunk in other:
        ref = chunk.get("source_ref") or "Réf. inconnue"
        context_parts.append(f"[SRC] {ref}\n{chunk['chunk_text']}\n")

    return "\n".join(context_parts)


async def generate_answer(
    question: str,
    history: List[Dict],
    jurisdiction: Optional[str] = None,
    legal_domain: Optional[str] = None,
) -> Dict:
    """Generate a legal answer using Claude API with RAG context.

    Pipeline:
    1. Embed the question via Cohere multilingual
    2. Search legal_chunks by vector similarity (pgvector) with optional filters
    3. Inject relevant chunks into system prompt
    4. Call Claude with grounded context
    5. Parse response and extract sources
    """

    # ── Step 1 & 2: RAG retrieval ──
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
            log.info(f"RAG: {len(chunks)} chunks retrieved (best similarity: {chunks[0]['similarity']:.2%})")
        else:
            log.info("RAG: no relevant chunks found above threshold")
    else:
        log.info("RAG: embedding unavailable, falling back to Claude knowledge")

    # ── Step 3: Build system prompt ──
    if rag_available:
        context = format_chunks_as_context(chunks)
        system_prompt = SYSTEM_PROMPT_WITH_RAG.format(context=context)
    else:
        system_prompt = SYSTEM_PROMPT_NO_RAG

    # ── Step 4: Build messages ──
    messages = []
    for msg in history[-8:]:
        if msg.get("role") in ("user", "assistant"):
            messages.append({"role": msg["role"], "content": msg["content"]})

    if not messages or messages[-1].get("content") != question:
        messages.append({"role": "user", "content": question})

    # ── Step 5: Call Claude API ──
    if not ANTHROPIC_API_KEY:
        return {
            "response": "⚠️ Clé API Anthropic non configurée. Ajoutez ANTHROPIC_API_KEY dans les variables d'environnement Railway.",
            "sources": [],
            "tokens": 0,
            "rag_chunks": 0,
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

        # Extract response text
        full_text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                full_text += block["text"]

        # Parse sources from response
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

        # Add RAG metadata to sources
        for source in sources:
            if rag_available:
                source["verified"] = True
            else:
                source.setdefault("verified", False)

        tokens = data.get("usage", {}).get("input_tokens", 0) + data.get("usage", {}).get("output_tokens", 0)

        return {
            "response": response_text,
            "sources": sources,
            "tokens": tokens,
            "rag_chunks": len(chunks),
            "rag_available": rag_available,
        }

    except httpx.HTTPStatusError as e:
        return {
            "response": f"Erreur API Claude ({e.response.status_code}). Veuillez réessayer.",
            "sources": [],
            "tokens": 0,
            "rag_chunks": 0,
        }
    except Exception as e:
        log.error(f"Claude API error: {e}")
        return {
            "response": f"Erreur inattendue : {str(e)}",
            "sources": [],
            "tokens": 0,
            "rag_chunks": 0,
        }
