"""
Admin Bulk Insert — /api/admin/bulk-insert
==========================================
Reçoit des chunks JSON depuis Claude (qui scrape Fedlex/ATF/cantons)
et les insère directement en DB. Pas de réseau externe requis.
"""
import logging
import os
from typing import Optional, List
import asyncpg
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

log = logging.getLogger("admin_bulk")
router = APIRouter(prefix="/api/admin", tags=["admin"])

ADMIN_KEY = os.getenv("ADMIN_INGEST_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

MIGRATIONS = [
    "ALTER TABLE legal_documents ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb",
    "ALTER TABLE legal_documents ADD COLUMN IF NOT EXISTS reference TEXT",
    "ALTER TABLE legal_documents ADD COLUMN IF NOT EXISTS jurisdiction TEXT DEFAULT 'CH'",
    "ALTER TABLE legal_chunks ADD COLUMN IF NOT EXISTS source_ref TEXT",
    "ALTER TABLE legal_chunks ADD COLUMN IF NOT EXISTS source_url TEXT",
]

def check_key(x_admin_key: Optional[str] = Header(None)):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(403, "Invalid admin key")


class ChunkItem(BaseModel):
    article_ref: str
    text: str
    url: str = ""

class BulkInsertRequest(BaseModel):
    source: str         # "fedlex", "entscheidsuche", "cantonal_tax"
    external_id: str    # RS number, ATF ref, canton code
    doc_type: str       # "legislation", "jurisprudence"
    title: str
    reference: str
    language: str = "fr"
    url: str = ""
    chunks: List[ChunkItem]


@router.post("/bulk-insert")
async def bulk_insert(
    req: BulkInsertRequest,
    x_admin_key: Optional[str] = Header(None),
):
    check_key(x_admin_key)
    if not req.chunks:
        raise HTTPException(400, "No chunks provided")

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            # Migrations
            for sql in MIGRATIONS:
                try:
                    await conn.execute(sql)
                except Exception:
                    pass

            full_text = "\n\n".join(c.text for c in req.chunks)

            doc_id = await conn.fetchval(
                """
                INSERT INTO legal_documents
                  (source, external_id, doc_type, title, reference, language, content, url)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (source, external_id) DO UPDATE
                  SET title=EXCLUDED.title, content=EXCLUDED.content, url=EXCLUDED.url
                RETURNING id
                """,
                req.source, req.external_id, req.doc_type,
                req.title, req.reference, req.language,
                full_text[:100000], req.url,
            )

            inserted = 0
            for idx, chunk in enumerate(req.chunks):
                try:
                    await conn.execute(
                        """
                        INSERT INTO legal_chunks
                          (document_id, chunk_index, chunk_text, source_ref, source_url)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT DO NOTHING
                        """,
                        doc_id, idx, chunk.text[:10000],
                        chunk.article_ref, chunk.url or req.url,
                    )
                    inserted += 1
                except Exception as e:
                    log.debug(f"Chunk {idx} error: {e}")

            # Stats
            total_docs = await conn.fetchval("SELECT COUNT(*) FROM legal_documents")
            total_chunks = await conn.fetchval("SELECT COUNT(*) FROM legal_chunks")

        finally:
            await conn.close()

        log.info(f"[BULK] {req.source}/{req.external_id}: {inserted} chunks inserted. DB: {total_docs} docs, {total_chunks} chunks")
        return {
            "ok": True,
            "doc_id": str(doc_id),
            "chunks_inserted": inserted,
            "db_total_docs": total_docs,
            "db_total_chunks": total_chunks,
        }

    except Exception as e:
        log.error(f"[BULK] ❌ {req.source}/{req.external_id}: {e}")
        raise HTTPException(500, str(e))


@router.get("/stats")
async def stats(x_admin_key: Optional[str] = Header(None)):
    check_key(x_admin_key)
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            for sql in MIGRATIONS:
                try:
                    await conn.execute(sql)
                except Exception:
                    pass
            docs = await conn.fetchval("SELECT COUNT(*) FROM legal_documents")
            chunks = await conn.fetchval("SELECT COUNT(*) FROM legal_chunks")
            by_source = await conn.fetch(
                "SELECT source, COUNT(*) as cnt FROM legal_documents GROUP BY source ORDER BY cnt DESC"
            )
            users = await conn.fetchval("SELECT COUNT(*) FROM users")
        finally:
            await conn.close()
        return {
            "legal_documents": docs,
            "legal_chunks": chunks,
            "by_source": [{"source": r["source"], "count": r["cnt"]} for r in by_source],
            "users": users,
        }
    except Exception as e:
        raise HTTPException(500, str(e))
