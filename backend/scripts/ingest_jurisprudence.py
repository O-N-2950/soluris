"""Ingest Entscheidsuche JSON data into PostgreSQL

Takes the JSON output from the Entscheidsuche scraper (backend/scrapers/entscheidsuche.py)
and inserts court decisions + chunks into the database.

Usage:
    python -m backend.scripts.ingest_jurisprudence                     # Ingest all batches
    python -m backend.scripts.ingest_jurisprudence --file data/jurisprudence/ch_bge_999_fr_batch_0001.json
    python -m backend.scripts.ingest_jurisprudence --scrape --limit 500  # Scrape then ingest
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
log = logging.getLogger("ingest_jurisprudence")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/soluris")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "jurisprudence"


async def ingest_batch(conn, filepath: Path) -> tuple[int, int]:
    """Ingest a single batch JSON file into legal_documents + legal_chunks."""
    with open(filepath) as f:
        data = json.load(f)

    decisions = data.get("decisions", [])
    chunks = data.get("chunks", [])
    total_docs = 0
    total_chunks = 0

    # Build a map of decision_id → doc_id for chunk insertion
    doc_id_map = {}

    for dec in decisions:
        ref = dec.get("reference", [])
        ref_str = ref[0] if ref else dec["id"]
        external_id = dec["id"]

        # Determine jurisdiction from hierarchy
        jurisdiction = "CH"  # All TF decisions are federal

        metadata = {
            "chamber": dec.get("chamber", ""),
            "chamber_name": dec.get("chamber_name", ""),
            "legal_domain": dec.get("legal_domain", ""),
            "is_atf": dec.get("is_atf", False),
            "content_url": dec.get("content_url", ""),
        }

        try:
            doc_id = await conn.fetchval(
                """
                INSERT INTO legal_documents
                    (source, external_id, doc_type, title, reference, jurisdiction, language, content, url, metadata, publication_date)
                VALUES ('entscheidsuche', $1, 'jurisprudence', $2, $3, $4, $5, $6, $7, $8, $9::date)
                ON CONFLICT (source, external_id) DO UPDATE
                    SET title = EXCLUDED.title, content = EXCLUDED.content,
                        reference = EXCLUDED.reference, metadata = EXCLUDED.metadata
                RETURNING id
                """,
                external_id,
                dec.get("title_fr", "") or ref_str,
                ref_str,
                jurisdiction,
                dec.get("language", "fr"),
                dec.get("abstract_fr", "")[:50000],
                dec.get("content_url", ""),
                json.dumps(metadata),
                dec.get("date") if dec.get("date") else None,
            )
            doc_id_map[external_id] = doc_id
            total_docs += 1
        except Exception as e:
            log.error(f"  Failed to insert decision {ref_str}: {e}")
            continue

    # Insert chunks
    for chunk in chunks:
        decision_id = chunk.get("decision_id", "")
        doc_id = doc_id_map.get(decision_id)
        if not doc_id:
            continue

        chunk_text = chunk.get("text", "")
        if not chunk_text.strip() or len(chunk_text.strip()) < 20:
            continue

        # Build source reference: "ATF 151 III 160 — Considérant 3"
        ref = chunk.get("decision_ref", "")
        chunk_type = chunk.get("chunk_type", "")
        source_ref = ref
        if chunk_type and chunk_type not in ("full_text", "metadata_only", "header"):
            source_ref = f"{ref} — {chunk_type.replace('_', ' ').title()}"

        try:
            await conn.execute(
                """
                INSERT INTO legal_chunks (document_id, chunk_index, chunk_text, source_ref, source_url)
                VALUES ($1, $2, $3, $4, $5)
                """,
                doc_id,
                chunk.get("chunk_index", 0),
                chunk_text[:10000],
                source_ref,
                chunk.get("source_url", ""),
            )
            total_chunks += 1
        except Exception as e:
            log.error(f"  Failed chunk {source_ref}: {e}")

    return total_docs, total_chunks


async def run(filepath: str = None, scrape_first: bool = False, limit: int = None):
    conn = await asyncpg.connect(DATABASE_URL)

    # Ensure extensions
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    except Exception:
        log.warning("pgvector not available — ingestion will work but embeddings won't")

    if scrape_first:
        log.info("🔄 Running Entscheidsuche scraper...")
        import subprocess
        cmd = [sys.executable, "-m", "backend.scrapers.entscheidsuche", "--mode", "atf", "--lang", "fr"]
        if limit:
            cmd.extend(["--limit", str(limit)])
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.error(f"Scraper failed: {result.stderr}")
        else:
            log.info(f"Scraper: {result.stdout[-200:]}")

    total_docs = 0
    total_chunks = 0

    if filepath:
        files = [Path(filepath)]
    else:
        if not DATA_DIR.exists():
            log.error(f"Data directory not found: {DATA_DIR}")
            log.info("Run the scraper first: python -m backend.scrapers.entscheidsuche --mode atf")
            await conn.close()
            return
        files = sorted(DATA_DIR.glob("*.json"))

    if not files:
        log.warning("No JSON files found to ingest")
        await conn.close()
        return

    log.info(f"📥 Ingesting {len(files)} batch files...")

    for f in files:
        try:
            docs, chunks = await ingest_batch(conn, f)
            total_docs += docs
            total_chunks += chunks
            log.info(f"  ✅ {f.name}: {docs} decisions, {chunks} chunks")
        except Exception as e:
            log.error(f"  ❌ {f.name}: {e}")

    log.info(f"\n🎯 Total: {total_docs} decisions, {total_chunks} chunks ingested")

    # Show DB stats
    doc_count = await conn.fetchval("SELECT COUNT(*) FROM legal_documents")
    chunk_count = await conn.fetchval("SELECT COUNT(*) FROM legal_chunks")
    juris = await conn.fetchval("SELECT COUNT(*) FROM legal_documents WHERE doc_type = 'jurisprudence'")
    log.info(f"📊 DB totals: {doc_count} documents ({juris} jurisprudence), {chunk_count} chunks")

    try:
        embedded = await conn.fetchval("SELECT COUNT(*) FROM legal_chunks WHERE embedding IS NOT NULL")
        if chunk_count > embedded:
            log.info(f"\n💡 {chunk_count - embedded} chunks need embedding. Run:")
            log.info("   python -m backend.scripts.embed_chunks")
    except Exception:
        pass

    await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="Single JSON batch file to ingest")
    parser.add_argument("--scrape", action="store_true", help="Run scraper first")
    parser.add_argument("--limit", type=int, help="Limit scraper to N decisions")
    args = parser.parse_args()
    asyncio.run(run(filepath=args.file, scrape_first=args.scrape, limit=args.limit))
