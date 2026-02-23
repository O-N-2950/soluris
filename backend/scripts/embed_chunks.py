"""Batch Embedding Script — Embed all legal_chunks using Cohere multilingual-v3

Usage:
    python -m backend.scripts.embed_chunks              # Embed all un-embedded chunks
    python -m backend.scripts.embed_chunks --all        # Re-embed everything
    python -m backend.scripts.embed_chunks --stats      # Show embedding stats only
"""
import os
import sys
import asyncio
import argparse
import logging
import time

import asyncpg
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("embed_chunks")

COHERE_API_KEY = os.getenv("COHERE_API_KEY", "")
EMBEDDING_MODEL = "embed-multilingual-v3.0"
BATCH_SIZE = 96  # Cohere max
EMBEDDING_DIM = 1024

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/soluris")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


async def get_stats(conn):
    total = await conn.fetchval("SELECT COUNT(*) FROM legal_chunks")
    embedded = await conn.fetchval("SELECT COUNT(*) FROM legal_chunks WHERE embedding IS NOT NULL")
    docs = await conn.fetchval("SELECT COUNT(*) FROM legal_documents")
    log.info(f"📊 {docs} documents | {total} chunks | {embedded}/{total} embedded ({embedded/max(total,1)*100:.0f}%)")
    return total, embedded


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts via Cohere API."""
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://api.cohere.ai/v1/embed",
            headers={
                "Authorization": f"Bearer {COHERE_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": EMBEDDING_MODEL,
                "texts": texts,
                "input_type": "search_document",
                "truncate": "END",
            },
        )
        resp.raise_for_status()
        return resp.json()["embeddings"]


async def run(embed_all: bool = False, stats_only: bool = False):
    if not COHERE_API_KEY:
        log.error("COHERE_API_KEY not set. Get one at https://dashboard.cohere.com/api-keys")
        sys.exit(1)

    conn = await asyncpg.connect(DATABASE_URL)

    # Ensure pgvector
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    total, embedded = await get_stats(conn)

    if stats_only:
        await conn.close()
        return

    # Get chunks to embed
    if embed_all:
        rows = await conn.fetch("SELECT id, chunk_text FROM legal_chunks ORDER BY created_at")
        log.info(f"🔄 Re-embedding ALL {len(rows)} chunks")
    else:
        rows = await conn.fetch(
            "SELECT id, chunk_text FROM legal_chunks WHERE embedding IS NULL ORDER BY created_at"
        )
        log.info(f"🆕 Embedding {len(rows)} new chunks (skipping {embedded} already done)")

    if not rows:
        log.info("✅ Nothing to embed!")
        await conn.close()
        return

    # Process in batches
    start = time.time()
    processed = 0
    errors = 0

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        texts = [r["chunk_text"][:8000] for r in batch]  # Truncate long chunks
        ids = [r["id"] for r in batch]

        try:
            embeddings = await embed_batch(texts)

            # Store embeddings
            for chunk_id, embedding in zip(ids, embeddings):
                embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
                await conn.execute(
                    "UPDATE legal_chunks SET embedding = $1::vector WHERE id = $2",
                    embedding_str, chunk_id,
                )

            processed += len(batch)
            elapsed = time.time() - start
            rate = processed / elapsed
            remaining = (len(rows) - processed) / rate if rate > 0 else 0
            log.info(f"  ✅ Batch {i // BATCH_SIZE + 1}: {processed}/{len(rows)} ({rate:.0f} chunks/s, ~{remaining:.0f}s remaining)")

            # Rate limiting: Cohere allows ~100 calls/min for trial
            await asyncio.sleep(0.5)

        except Exception as e:
            log.error(f"  ❌ Batch {i // BATCH_SIZE + 1} failed: {e}")
            errors += 1
            if errors > 5:
                log.error("Too many errors, aborting.")
                break
            await asyncio.sleep(5)  # Back off on error

    elapsed = time.time() - start
    log.info(f"\n🎯 Done! {processed} chunks embedded in {elapsed:.0f}s ({errors} errors)")

    await get_stats(conn)
    await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="Re-embed all chunks")
    parser.add_argument("--stats", action="store_true", help="Show stats only")
    args = parser.parse_args()
    asyncio.run(run(embed_all=args.all, stats_only=args.stats))
