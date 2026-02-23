"""
Pipeline d'Ingestion — Données juridiques → PostgreSQL + pgvector
=================================================================
Lit les JSON produits par les scrapers (fedlex, entscheidsuche) et :
  1. Insert les documents dans `legal_documents`
  2. Insert les chunks dans `legal_chunks`
  3. Génère et stocke les embeddings vectoriels

Usage :
  python -m backend.services.ingestion --source fedlex     # Ingérer la législation
  python -m backend.services.ingestion --source juris      # Ingérer la jurisprudence
  python -m backend.services.ingestion --source all        # Tout ingérer
  python -m backend.services.ingestion --embed-only        # Recalculer les embeddings seulement
"""

import argparse
import asyncio
import glob
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("soluris.ingestion")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DATABASE_URL = os.getenv("DATABASE_URL", "")

# ---------------------------------------------------------------------------
# Schema DDL (pgvector)
# ---------------------------------------------------------------------------

SCHEMA_DDL = """
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Legal documents (lois, arrêts)
CREATE TABLE IF NOT EXISTS legal_documents (
    id              SERIAL PRIMARY KEY,
    source          TEXT NOT NULL,           -- 'fedlex', 'entscheidsuche'
    external_id     TEXT NOT NULL UNIQUE,    -- RS number or decision ID
    doc_type        TEXT NOT NULL,           -- 'legislation', 'jurisprudence'
    title           TEXT,
    reference       TEXT,                    -- e.g. "CO", "BGE 151 III 160"
    jurisdiction    TEXT DEFAULT 'CH',       -- 'CH', 'GE', 'VD', etc.
    legal_domain    TEXT,                    -- 'droit_civil', 'droit_penal', etc.
    language        TEXT DEFAULT 'fr',
    abstract        TEXT,
    publication_date DATE,
    url             TEXT,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Legal chunks (article-level or considérant-level)
CREATE TABLE IF NOT EXISTS legal_chunks (
    id              SERIAL PRIMARY KEY,
    document_id     INTEGER REFERENCES legal_documents(id) ON DELETE CASCADE,
    chunk_index     INTEGER DEFAULT 0,
    chunk_type      TEXT,                   -- 'article', 'regeste', 'considerant', 'dispositif'
    chunk_text      TEXT NOT NULL,
    source_ref      TEXT,                   -- e.g. "art. 97 CO", "consid. 3.1"
    source_url      TEXT,
    embedding       VECTOR(1024),           -- Cohere multilingual-v3: 1024 dims
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for fast retrieval
CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON legal_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_chunk_type ON legal_chunks(chunk_type);
CREATE INDEX IF NOT EXISTS idx_documents_doc_type ON legal_documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_documents_jurisdiction ON legal_documents(jurisdiction);
CREATE INDEX IF NOT EXISTS idx_documents_legal_domain ON legal_documents(legal_domain);
CREATE INDEX IF NOT EXISTS idx_documents_external_id ON legal_documents(external_id);

-- HNSW index for vector similarity search (faster than IVFFlat for < 1M vectors)
-- CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON legal_chunks
--     USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
-- Note: Create HNSW index AFTER inserting data (faster build)
"""

CREATE_HNSW_INDEX = """
CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON legal_chunks
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
"""


# ---------------------------------------------------------------------------
# Database operations (asyncpg)
# ---------------------------------------------------------------------------

async def init_schema(pool):
    """Create tables and enable pgvector."""
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_DDL)
    log.info("Schema initialized with pgvector")


async def upsert_document(conn, doc: dict) -> int:
    """Insert or update a legal document. Returns document ID."""
    row = await conn.fetchrow("""
        INSERT INTO legal_documents (source, external_id, doc_type, title, reference,
                                     jurisdiction, legal_domain, language, abstract,
                                     publication_date, url, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (external_id) DO UPDATE SET
            title = EXCLUDED.title,
            abstract = EXCLUDED.abstract,
            url = EXCLUDED.url,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
        RETURNING id
    """,
        doc["source"], doc["external_id"], doc["doc_type"],
        doc.get("title"), doc.get("reference"),
        doc.get("jurisdiction", "CH"), doc.get("legal_domain"),
        doc.get("language", "fr"), doc.get("abstract"),
        doc.get("publication_date"), doc.get("url"),
        json.dumps(doc.get("metadata", {})),
    )
    return row["id"]


async def insert_chunks(conn, document_id: int, chunks: list[dict]):
    """Batch insert chunks for a document."""
    # First delete existing chunks for this document (re-ingestion)
    await conn.execute("DELETE FROM legal_chunks WHERE document_id = $1", document_id)

    if not chunks:
        return

    # Batch insert
    records = [
        (document_id, c.get("chunk_index", i), c.get("chunk_type", ""),
         c["chunk_text"], c.get("source_ref", ""), c.get("source_url", ""),
         json.dumps(c.get("metadata", {})))
        for i, c in enumerate(chunks)
    ]

    await conn.executemany("""
        INSERT INTO legal_chunks (document_id, chunk_index, chunk_type,
                                  chunk_text, source_ref, source_url, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
    """, records)


async def update_embeddings(conn, chunk_id: int, embedding: list[float]):
    """Update the embedding vector for a chunk."""
    # pgvector expects a string format: '[0.1, 0.2, ...]'
    vec_str = "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"
    await conn.execute(
        "UPDATE legal_chunks SET embedding = $1::vector WHERE id = $2",
        vec_str, chunk_id
    )


async def get_chunks_without_embeddings(pool, batch_size: int = 500) -> list[dict]:
    """Get chunks that don't have embeddings yet."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, chunk_text FROM legal_chunks
            WHERE embedding IS NULL
            ORDER BY id
            LIMIT $1
        """, batch_size)
        return [{"id": row["id"], "chunk_text": row["chunk_text"]} for row in rows]


# ---------------------------------------------------------------------------
# Fedlex ingestion
# ---------------------------------------------------------------------------

def _fedlex_json_to_documents(json_path: str) -> list[tuple[dict, list[dict]]]:
    """Convert a Fedlex JSON file to (document, chunks) pairs."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    act = data.get("act", {})
    chunks_raw = data.get("chunks", [])

    doc = {
        "source": "fedlex",
        "external_id": f"fedlex_{act.get('rs_number', 'unknown')}",
        "doc_type": "legislation",
        "title": act.get("title", ""),
        "reference": act.get("rs_number", ""),
        "jurisdiction": "CH",
        "legal_domain": _infer_domain_from_rs(act.get("rs_number", "")),
        "language": "fr",
        "abstract": act.get("title_short", ""),
        "publication_date": act.get("consolidation_date"),
        "url": act.get("fedlex_url", act.get("uri", "")),
        "metadata": {
            "rs_number": act.get("rs_number"),
            "title_short": act.get("title_short"),
            "in_force": act.get("in_force"),
            "total_articles": len(chunks_raw),
        },
    }

    chunks = []
    for c in chunks_raw:
        chunks.append({
            "chunk_index": c.get("chunk_index", 0),
            "chunk_type": "article",
            "chunk_text": c.get("text", ""),
            "source_ref": f"art. {c.get('article_number', '?')} {act.get('title_short', act.get('rs_number', ''))}",
            "source_url": c.get("fedlex_url", ""),
            "metadata": {
                "article_id": c.get("article_id"),
                "article_number": c.get("article_number"),
                "section_path": c.get("section_path"),
            }
        })

    return [(doc, chunks)]


def _infer_domain_from_rs(rs: str) -> str:
    """Infer legal domain from RS number."""
    if not rs:
        return "autre"
    try:
        main = int(rs.split(".")[0])
    except ValueError:
        return "autre"
    # Swiss RS classification
    if main < 200:
        return "droit_constitutionnel"
    elif main < 300:
        return "droit_civil"
    elif main < 400:
        return "droit_penal"
    elif main < 500:
        return "droit_education"
    elif main < 600:
        return "droit_defense"
    elif main < 700:
        return "droit_finances"
    elif main < 800:
        return "droit_amenagement"
    elif main < 900:
        return "droit_social"
    elif main < 1000:
        return "droit_economie"
    return "autre"


# ---------------------------------------------------------------------------
# Jurisprudence ingestion
# ---------------------------------------------------------------------------

def _juris_json_to_documents(json_path: str) -> list[tuple[dict, list[dict]]]:
    """Convert an entscheidsuche JSON batch file to (document, chunks) pairs."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    decisions = data.get("decisions", [])
    chunks_raw = data.get("chunks", [])

    # Group chunks by decision_id
    chunks_by_dec = {}
    for c in chunks_raw:
        dec_id = c.get("decision_id", "")
        if dec_id not in chunks_by_dec:
            chunks_by_dec[dec_id] = []
        chunks_by_dec[dec_id].append(c)

    results = []
    for dec in decisions:
        ref = dec.get("reference", [])
        ref_str = ref[0] if ref else dec.get("id", "")

        doc = {
            "source": "entscheidsuche",
            "external_id": f"es_{dec['id']}",
            "doc_type": "jurisprudence",
            "title": dec.get("title_fr", ""),
            "reference": ref_str,
            "jurisdiction": "CH",
            "legal_domain": dec.get("legal_domain", "autre"),
            "language": dec.get("language", "fr"),
            "abstract": dec.get("abstract_fr", ""),
            "publication_date": dec.get("date"),
            "url": dec.get("content_url", ""),
            "metadata": {
                "court": dec.get("court"),
                "chamber": dec.get("chamber"),
                "chamber_name": dec.get("chamber_name"),
                "is_atf": dec.get("is_atf", False),
                "all_references": ref,
            },
        }

        dec_chunks = chunks_by_dec.get(dec["id"], [])
        chunks = []
        for c in dec_chunks:
            chunks.append({
                "chunk_index": c.get("chunk_index", 0),
                "chunk_type": c.get("chunk_type", "full_text"),
                "chunk_text": c.get("text", ""),
                "source_ref": ref_str,
                "source_url": c.get("source_url", ""),
                "metadata": {
                    "decision_ref": c.get("decision_ref"),
                    "abstract_fr": c.get("abstract_fr", "")[:200],
                    "chamber_name": c.get("chamber_name"),
                    "is_atf": c.get("is_atf", False),
                },
            })

        results.append((doc, chunks))

    return results


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def ingest_fedlex(pool, data_dir: str = "data/fedlex"):
    """Ingest Fedlex legislation data into PostgreSQL."""
    files = sorted(glob.glob(os.path.join(data_dir, "*.json")))
    if not files:
        log.warning(f"No Fedlex JSON files found in {data_dir}")
        return

    log.info(f"Ingesting {len(files)} Fedlex files...")
    total_docs = 0
    total_chunks = 0

    async with pool.acquire() as conn:
        for f in files:
            for doc, chunks in _fedlex_json_to_documents(f):
                doc_id = await upsert_document(conn, doc)
                await insert_chunks(conn, doc_id, chunks)
                total_docs += 1
                total_chunks += len(chunks)

    log.info(f"Fedlex ingested: {total_docs} documents, {total_chunks} chunks")


async def ingest_jurisprudence(pool, data_dir: str = "data/jurisprudence"):
    """Ingest entscheidsuche jurisprudence data into PostgreSQL."""
    files = sorted(glob.glob(os.path.join(data_dir, "*.json")))
    if not files:
        log.warning(f"No jurisprudence JSON files found in {data_dir}")
        return

    log.info(f"Ingesting {len(files)} jurisprudence batch files...")
    total_docs = 0
    total_chunks = 0

    async with pool.acquire() as conn:
        for f in files:
            pairs = _juris_json_to_documents(f)
            for doc, chunks in pairs:
                doc_id = await upsert_document(conn, doc)
                await insert_chunks(conn, doc_id, chunks)
                total_docs += 1
                total_chunks += len(chunks)
            log.info(f"  {os.path.basename(f)}: {len(pairs)} decisions")

    log.info(f"Jurisprudence ingested: {total_docs} documents, {total_chunks} chunks")


async def generate_embeddings(pool, embedding_service=None):
    """Generate embeddings for all chunks that don't have one yet."""
    if embedding_service is None:
        from backend.services.embeddings import get_embedding_service
        embedding_service = get_embedding_service()

    batch_size = 200
    total_embedded = 0

    while True:
        chunks = await get_chunks_without_embeddings(pool, batch_size)
        if not chunks:
            break

        texts = [c["chunk_text"] for c in chunks]
        embeddings = embedding_service.embed_documents(texts)

        async with pool.acquire() as conn:
            for chunk, emb in zip(chunks, embeddings):
                await update_embeddings(conn, chunk["id"], emb)

        total_embedded += len(chunks)
        log.info(f"  Embedded {total_embedded} chunks so far...")

    log.info(f"Total embeddings generated: {total_embedded}")

    # Create HNSW index if not exists
    if total_embedded > 0:
        async with pool.acquire() as conn:
            log.info("Creating HNSW index on embeddings...")
            await conn.execute(CREATE_HNSW_INDEX)
            log.info("HNSW index created")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def main_async(args):
    import asyncpg
    pool = await asyncpg.create_pool(args.database_url, min_size=2, max_size=5)

    try:
        await init_schema(pool)

        if args.source in ("fedlex", "all"):
            await ingest_fedlex(pool, args.fedlex_dir)

        if args.source in ("juris", "all"):
            await ingest_jurisprudence(pool, args.juris_dir)

        if args.embed or args.embed_only:
            await generate_embeddings(pool)

    finally:
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description="Ingestion pipeline — données juridiques → PostgreSQL")
    parser.add_argument("--source", choices=["fedlex", "juris", "all"], default="all")
    parser.add_argument("--fedlex-dir", default="data/fedlex")
    parser.add_argument("--juris-dir", default="data/jurisprudence")
    parser.add_argument("--embed", action="store_true", help="Générer les embeddings après ingestion")
    parser.add_argument("--embed-only", action="store_true", help="Seulement les embeddings")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""),
                        help="PostgreSQL connection string")
    args = parser.parse_args()

    if not args.database_url:
        log.error("DATABASE_URL required. Set via env or --database-url")
        return

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
