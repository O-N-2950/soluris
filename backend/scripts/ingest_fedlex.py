"""Ingest Fedlex JSON data into PostgreSQL

Takes the JSON output from the Fedlex scraper (backend/scrapers/fedlex.py)
and inserts documents + chunks into the database.

Usage:
    python -m backend.scripts.ingest_fedlex                    # Ingest all JSON in data/fedlex/
    python -m backend.scripts.ingest_fedlex --file data/fedlex/220.json  # Ingest single file
    python -m backend.scripts.ingest_fedlex --scrape           # Scrape priority codes then ingest
"""
import os
import sys
import json
import asyncio
import argparse
import logging
from pathlib import Path

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ingest_fedlex")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/soluris")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "fedlex"


async def ingest_file(conn, filepath: Path) -> tuple[int, int]:
    """Ingest a single JSON file into legal_documents + legal_chunks."""
    with open(filepath) as f:
        data = json.load(f)

    # Handle both single doc and array of docs
    docs = data if isinstance(data, list) else [data]
    total_docs = 0
    total_chunks = 0

    for doc in docs:
        rs_number = doc.get("rs_number", "")
        title = doc.get("title", "")
        uri = doc.get("uri", "")
        url = doc.get("url", "")
        content = doc.get("content", "")
        chunks = doc.get("chunks", [])
        pub_date = doc.get("publication_date")

        # Insert document
        try:
            doc_id = await conn.fetchval(
                """
                INSERT INTO legal_documents (source, external_id, doc_type, title, reference, language, content, url, metadata)
                VALUES ('fedlex', $1, 'legislation', $2, $3, 'fr', $4, $5, $6)
                ON CONFLICT (source, external_id) DO UPDATE
                    SET title = EXCLUDED.title, content = EXCLUDED.content, url = EXCLUDED.url
                RETURNING id
                """,
                rs_number, title, f"RS {rs_number}", content[:50000] if content else "",
                url or uri, json.dumps({"rs_number": rs_number, "uri": uri}),
            )
            total_docs += 1
        except Exception as e:
            log.error(f"  Failed to insert doc RS {rs_number}: {e}")
            continue

        # Insert chunks (articles)
        if chunks:
            for i, chunk in enumerate(chunks):
                chunk_text = chunk.get("text", "")
                article_ref = chunk.get("article_ref", chunk.get("reference", f"Art. {i+1}"))
                article_url = chunk.get("url", url)

                if not chunk_text.strip():
                    continue

                try:
                    await conn.execute(
                        """
                        INSERT INTO legal_chunks (document_id, chunk_index, chunk_text, source_ref, source_url)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        doc_id, i, chunk_text[:10000], article_ref, article_url,
                    )
                    total_chunks += 1
                except Exception as e:
                    log.error(f"  Failed chunk {article_ref}: {e}")
        elif content:
            # No pre-chunked data — chunk by paragraphs (simple fallback)
            paragraphs = [p.strip() for p in content.split("\n\n") if p.strip() and len(p.strip()) > 50]
            for i, para in enumerate(paragraphs[:500]):  # Max 500 chunks per doc
                try:
                    await conn.execute(
                        """
                        INSERT INTO legal_chunks (document_id, chunk_index, chunk_text, source_ref, source_url)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        doc_id, i, para[:10000], f"RS {rs_number} §{i+1}", url,
                    )
                    total_chunks += 1
                except Exception as e:
                    pass

    return total_docs, total_chunks


async def run(filepath: str = None, scrape_first: bool = False):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    if scrape_first:
        log.info("🔄 Running Fedlex scraper in priority mode first...")
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "backend.scrapers.fedlex", "--mode", "priority"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            log.error(f"Scraper failed: {result.stderr}")
        else:
            log.info(f"Scraper output: {result.stdout[-200:]}")

    total_docs = 0
    total_chunks = 0

    if filepath:
        files = [Path(filepath)]
    else:
        if not DATA_DIR.exists():
            log.error(f"Data directory not found: {DATA_DIR}")
            log.info("Run the scraper first: python -m backend.scrapers.fedlex --mode priority")
            await conn.close()
            return
        files = sorted(DATA_DIR.glob("*.json"))

    if not files:
        log.warning("No JSON files found to ingest")
        await conn.close()
        return

    log.info(f"📥 Ingesting {len(files)} files...")

    for f in files:
        try:
            docs, chunks = await ingest_file(conn, f)
            total_docs += docs
            total_chunks += chunks
            log.info(f"  ✅ {f.name}: {docs} docs, {chunks} chunks")
        except Exception as e:
            log.error(f"  ❌ {f.name}: {e}")

    log.info(f"\n🎯 Total: {total_docs} documents, {total_chunks} chunks ingested")

    # Show DB stats
    doc_count = await conn.fetchval("SELECT COUNT(*) FROM legal_documents")
    chunk_count = await conn.fetchval("SELECT COUNT(*) FROM legal_chunks")
    embedded = await conn.fetchval("SELECT COUNT(*) FROM legal_chunks WHERE embedding IS NOT NULL")
    log.info(f"📊 DB totals: {doc_count} documents, {chunk_count} chunks, {embedded} embedded")

    if chunk_count > embedded:
        log.info(f"\n💡 {chunk_count - embedded} chunks need embedding. Run:")
        log.info("   python -m backend.scripts.embed_chunks")

    await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="Single JSON file to ingest")
    parser.add_argument("--scrape", action="store_true", help="Run Fedlex scraper first")
    args = parser.parse_args()
    asyncio.run(run(filepath=args.file, scrape_first=args.scrape))
