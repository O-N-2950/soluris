"""
Admin Ingestion Endpoint — /api/admin/ingest
=============================================
Endpoint protégé par clé admin permettant de déclencher l'ingestion
des données juridiques depuis l'intérieur de Railway (accès DB interne).

Architecture : fonctions HTTP synchrones (requests) exécutées dans asyncio.to_thread(),
connectées à asyncpg (async) pour les inserts DB.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

import asyncpg
import requests
from bs4 import BeautifulSoup
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel

log = logging.getLogger("admin_ingest")
logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/admin", tags=["admin"])

ADMIN_KEY = os.getenv("ADMIN_INGEST_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_jobs: dict = {}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def check_admin_key(x_admin_key: Optional[str] = Header(None)):
    if not ADMIN_KEY:
        raise HTTPException(503, "ADMIN_INGEST_KEY not configured")
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(403, "Invalid admin key")


class IngestRequest(BaseModel):
    source: str = "fedlex"
    rs_codes: Optional[list[str]] = None
    cantons: Optional[list[str]] = None
    limit: Optional[int] = None


# ---------------------------------------------------------------------------
# Codes prioritaires
# ---------------------------------------------------------------------------

PRIORITY_RS = [
    ("220",       "CO",    "Code des obligations"),
    ("210",       "CC",    "Code civil"),
    ("311.0",     "CP",    "Code pénal"),
    ("272",       "CPC",   "Code de procédure civile"),
    ("312.0",     "CPP",   "Code de procédure pénale"),
    ("281.1",     "LP",    "Loi sur la poursuite pour dettes et la faillite"),
    ("173.110",   "LTF",   "Loi sur le Tribunal fédéral"),
    ("101",       "Cst",   "Constitution fédérale"),
    ("830.1",     "LPGA",  "Loi sur la partie générale des assurances sociales"),
    ("832.10",    "LAMal", "Loi sur l'assurance-maladie"),
    ("831.10",    "LAVS",  "Loi sur l'AVS"),
    ("642.11",    "LIFD",  "Loi fédérale sur l'impôt fédéral direct"),
    ("642.14",    "LHID",  "Loi fédérale sur l'harmonisation des impôts directs"),
    ("642.21",    "LT",    "Loi fédérale sur l'impôt anticipé"),
    ("641.10",    "LTVA",  "Loi fédérale sur la taxe sur la valeur ajoutée"),
    ("831.40",    "LPP",   "Loi sur la prévoyance professionnelle"),
    ("831.461.3", "OPP3",  "Ordonnance sur le pilier 3a"),
    ("614.0",     "OFPr",  "Ordonnance sur les frais professionnels"),
    ("291",       "LDIP",  "Loi sur le droit international privé"),
    ("700",       "LAT",   "Loi sur l'aménagement du territoire"),
    ("142.20",    "LEI",   "Loi sur les étrangers et l'intégration"),
    ("221.229.1", "LFus",  "Loi sur la fusion"),
]

CANTONAL_TAX_URLS = {
    "GE": ("https://silgeneve.ch/legis/data/rsg_D_3_70.html", "Loi sur l'imposition des personnes physiques GE"),
    "VD": ("https://prestations.vd.ch/pub/blv-publication/actes/consulter/642.11.1", "Loi sur les impôts directs cantonaux VD"),
    "NE": ("https://rsn.ne.ch/DATA/program/books/rsne/htm/64211.htm", "Loi sur les contributions directes NE"),
    "FR": ("https://bdlf.fr.ch/app/fr/texts_of_law/632.1/versions/current", "Loi sur les impôts cantonaux directs FR"),
    "JU": ("https://rsju.jura.ch/fr/viewdocument.html?idn=28029", "Loi d'impôt jurassienne"),
    "VS": ("https://lex.vs.ch/app/fr/texts_of_law/642.1/versions/current", "Loi fiscale valaisanne"),
    "BE": ("https://www.belex.sites.be.ch/app/de/texts_of_law/661.11/versions/current", "Steuergesetz BE"),
    "ZH": ("https://www.zhlex.zh.ch/Erlass.html?Open&Ordnr=631.1", "Steuergesetz ZH"),
    "ZG": ("https://bgs.zg.ch/app/de/texts_of_law/632.1/versions/current", "Steuergesetz ZG"),
    "SG": ("https://www.gesetzessammlung.sg.ch/app/de/texts_of_law/811.1/versions/current", "Steuergesetz SG"),
    "AG": ("https://gesetzessammlungen.ag.ch/app/de/texts_of_law/651/versions/current", "Steuergesetz AG"),
    "LU": ("https://gesetz.lu.ch/app/de/texts_of_law/620/versions/current", "Steuergesetz LU"),
    "BS": ("https://www.gesetzessammlung.bs.ch/app/de/texts_of_law/640.100/versions/current", "Steuergesetz BS"),
    "SO": ("https://bgs.so.ch/app/de/texts_of_law/611.1/versions/current", "Steuergesetz SO"),
    "TG": ("https://rechtsbuch.tg.ch/app/de/texts_of_law/640/versions/current", "Steuergesetz TG"),
    "GR": ("https://www.gr-lex.gr.ch/app/de/texts_of_law/720.200/versions/current", "Steuergesetz GR"),
    "GL": ("https://gesetze.gl.ch/app/de/texts_of_law/613.1/versions/current", "Steuergesetz GL"),
    "NW": ("https://gesetze.nw.ch/app/de/texts_of_law/621.1/versions/current", "Steuergesetz NW"),
    "OW": ("https://ow.codex.ch/app/de/texts_of_law/631.4/versions/current", "Steuergesetz OW"),
    "SH": ("https://www.rechtssammlung.sh.ch/app/de/texts_of_law/641.100/versions/current", "Steuergesetz SH"),
    "AR": ("https://www.ar.ch/app/de/texts_of_law/621.0/versions/current", "Steuergesetz AR"),
    # PDFs / manual - texte brut récupéré via lexfind
    "TI": ("https://www.ti.ch/fileadmin/DSC/SPED/Leggi/Legge_tributaria.pdf", "Legge tributaria TI"),
}


# ---------------------------------------------------------------------------
# HTTP helpers (synchronous — run via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _sparql_get_html_url(rs_number: str) -> Optional[str]:
    query = f"""
PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
SELECT ?url WHERE {{
  ?ca a jolux:ConsolidationAbstract ;
      jolux:isRealizedBy ?expr .
  ?expr jolux:language <http://publications.europa.eu/resource/authority/language/FRA> ;
        jolux:historicalLegalId "{rs_number}" ;
        jolux:isEmbodiedBy ?manif .
  ?manif jolux:isExemplifiedBy ?url .
  FILTER(CONTAINS(STR(?url), ".html"))
}}
ORDER BY DESC(?url) LIMIT 1
"""
    try:
        resp = requests.post(
            "https://fedlex.data.admin.ch/sparqlendpoint",
            data={"query": query},
            headers={"Accept": "application/sparql-results+json"},
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()["results"]["bindings"]
        if rows:
            return rows[0]["url"]["value"]
    except Exception as e:
        log.warning(f"SPARQL error RS {rs_number}: {e}")
    return None


def _fetch_and_parse_html(html_url: str, rs_number: str, title: str, abbrev: str) -> list[dict]:
    try:
        resp = requests.get(html_url, timeout=60)
        resp.encoding = "utf-8"
    except Exception as e:
        log.warning(f"HTTP error {html_url}: {e}")
        return []

    soup = BeautifulSoup(resp.content, "html.parser")
    articles = soup.find_all("article")
    chunks = []

    for art in articles:
        # Section path
        path_parts = []
        parent = art.parent
        while parent and parent.name:
            if parent.name == "section":
                h = parent.find(["h1", "h2", "h3", "h4", "h5", "h6"], recursive=False)
                if h:
                    path_parts.insert(0, h.get_text(strip=True)[:60])
            parent = parent.parent

        heading = art.find(["h6", "h5", "h4", "h3"])
        art_num = heading.get_text(strip=True) if heading else art.get("id", "")
        text = "\n".join(p.get_text(" ", strip=True) for p in art.find_all("p") if p.get_text(strip=True))

        if len(text.strip()) < 20:
            continue

        chunks.append({
            "article_ref": f"{art_num} {abbrev}".strip(),
            "section_path": " > ".join(path_parts),
            "text": text[:4000],
            "url": html_url,
            "rs_number": rs_number,
            "law_abbrev": abbrev,
            "law_title": title,
        })

    return chunks


def _fetch_cantonal_page(url: str) -> tuple[list[dict], str]:
    """Retourne (articles_parsed, raw_text)."""
    try:
        resp = requests.get(url, timeout=30, headers={
            "Accept-Language": "fr,de",
            "User-Agent": "Soluris/1.0 legal research"
        })
        resp.raise_for_status()
        resp.encoding = "utf-8"
    except Exception as e:
        return [], str(e)

    soup = BeautifulSoup(resp.content, "html.parser")

    # Try structured articles first
    articles = soup.find_all("article")
    if not articles:
        articles = soup.find_all(class_=lambda c: c and "article" in str(c).lower())

    parsed = []
    for idx, art in enumerate(articles):
        text = art.get_text("\n", strip=True)
        if len(text.strip()) < 30:
            continue
        h = art.find(["h1", "h2", "h3", "h4", "h5", "h6"])
        parsed.append({
            "idx": idx,
            "ref": h.get_text(strip=True)[:80] if h else f"Art. {idx+1}",
            "text": text[:4000],
        })

    # Fallback: raw text
    raw = ""
    if not parsed:
        body = soup.find("main") or soup.find(class_="content") or soup.find("body")
        if body:
            raw = body.get_text("\n", strip=True)[:50000]

    return parsed, raw


# ---------------------------------------------------------------------------
# Ingestion functions (async — use asyncio.to_thread for HTTP)
# ---------------------------------------------------------------------------

async def ingest_fedlex_codes(conn: asyncpg.Connection, rs_list: list, status: dict):
    total_chunks = 0

    for i, (rs_number, abbrev, title) in enumerate(rs_list):
        status["current"] = f"{abbrev} (RS {rs_number}) [{i+1}/{len(rs_list)}]"
        status["progress"] = f"{i+1}/{len(rs_list)}"
        log.info(f"[Fedlex] {abbrev} RS {rs_number}...")

        # SPARQL in thread
        html_url = await asyncio.to_thread(_sparql_get_html_url, rs_number)
        if not html_url:
            log.warning(f"  No HTML URL for RS {rs_number}")
            continue

        await asyncio.sleep(0.5)

        # Parse HTML in thread
        chunks = await asyncio.to_thread(_fetch_and_parse_html, html_url, rs_number, title, abbrev)
        if not chunks:
            log.warning(f"  No articles for RS {rs_number}")
            continue

        full_text = "\n\n".join(c["text"] for c in chunks)

        try:
            doc_id = await conn.fetchval(
                """
                INSERT INTO legal_documents
                  (source, external_id, doc_type, title, reference, language, content, url)
                VALUES ('fedlex', $1, 'legislation', $2, $3, 'fr', $4, $5)
                ON CONFLICT (source, external_id) DO UPDATE
                  SET title=EXCLUDED.title, content=EXCLUDED.content, url=EXCLUDED.url
                RETURNING id
                """,
                rs_number, title, f"RS {rs_number}", full_text[:100000], html_url,
            )
        except Exception as e:
            log.error(f"  Doc insert error RS {rs_number}: {e}")
            continue

        chunk_count = 0
        for idx, chunk in enumerate(chunks):
            try:
                await conn.execute(
                    """
                    INSERT INTO legal_chunks
                      (document_id, chunk_index, chunk_text, source_ref, source_url)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT DO NOTHING
                    """,
                    doc_id, idx, chunk["text"],
                    chunk["article_ref"], chunk["url"],
                )
                chunk_count += 1
            except Exception as e:
                log.debug(f"  Chunk error: {e}")

        total_chunks += chunk_count
        status["total_chunks"] = total_chunks
        log.info(f"  RS {rs_number}: {chunk_count} chunks OK")

    return total_chunks


async def ingest_atf_decisions(conn: asyncpg.Connection, limit: int, status: dict):
    total = 0
    offset = 0
    batch = 50

    while total < limit:
        def fetch_batch(off):
            payload = {
                "query": {"bool": {"must": [
                    {"term": {"Sprache": "fr"}},
                    {"prefix": {"SignaturNummer": "ATF"}},
                ]}},
                "sort": [{"Datum": {"order": "desc"}}],
                "from": off, "size": batch,
            }
            try:
                resp = requests.post(
                    "https://entscheidsuche.ch/_search.php",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json().get("hits", {}).get("hits", [])
            except Exception as e:
                log.warning(f"ATF search error: {e}")
                return []

        hits = await asyncio.to_thread(fetch_batch, offset)
        if not hits:
            break

        for hit in hits:
            src = hit.get("_source", {})
            ref = src.get("SignaturNummer", "")
            text = src.get("Text", src.get("Zusammenfassung", ""))
            if not text or len(text.strip()) < 50:
                continue

            try:
                doc_id = await conn.fetchval(
                    """
                    INSERT INTO legal_documents
                      (source, external_id, doc_type, title, reference, language, content, url)
                    VALUES ('entscheidsuche', $1, 'jurisprudence', $2, $3, 'fr', $4, $5)
                    ON CONFLICT (source, external_id) DO UPDATE
                      SET content=EXCLUDED.content
                    RETURNING id
                    """,
                    ref, src.get("Titel", ref), ref,
                    text[:100000], src.get("URL", ""),
                )
                await conn.execute(
                    """
                    INSERT INTO legal_chunks
                      (document_id, chunk_index, chunk_text, source_ref, source_url)
                    VALUES ($1, 0, $2, $3, $4)
                    ON CONFLICT DO NOTHING
                    """,
                    doc_id, text[:4000], ref, src.get("URL", ""),
                )
                total += 1
            except Exception as e:
                log.debug(f"ATF insert error {ref}: {e}")

        status["current"] = f"ATF: {total} arrêts ingérés"
        status["total_atf"] = total
        offset += batch
        if len(hits) < batch:
            break
        await asyncio.sleep(0.3)

    return total


async def ingest_cantonal(conn: asyncpg.Connection, cantons: list, status: dict):
    total = 0

    for i, canton in enumerate(cantons):
        if canton not in CANTONAL_TAX_URLS:
            continue
        url, law_title = CANTONAL_TAX_URLS[canton]

        if url.endswith(".pdf"):
            status.setdefault("skipped", []).append(f"{canton} (PDF)")
            log.info(f"  {canton}: PDF skipped")
            continue

        status["current"] = f"Canton {canton} [{i+1}/{len(cantons)}]"
        log.info(f"[Canton] {canton}: {law_title}")

        articles, raw_text = await asyncio.to_thread(_fetch_cantonal_page, url)

        if not articles and len(raw_text) < 100:
            log.warning(f"  {canton}: No content")
            status.setdefault("errors", []).append(f"{canton}: no content")
            continue

        try:
            doc_id = await conn.fetchval(
                """
                INSERT INTO legal_documents
                  (source, external_id, doc_type, title, reference, language, content, url)
                VALUES ('cantonal_tax', $1, 'legislation', $2, $3, 'fr', $4, $5)
                ON CONFLICT (source, external_id) DO UPDATE
                  SET title=EXCLUDED.title, content=EXCLUDED.content
                RETURNING id
                """,
                f"{canton}_tax_loi", law_title, f"Loi fiscale {canton}",
                (articles[0]["text"] if articles else raw_text[:100000])[:100000],
                url,
            )
        except Exception as e:
            log.error(f"  {canton} doc error: {e}")
            continue

            if articles:
                for art in articles:
                    try:
                        await conn.execute(
                            """
                            INSERT INTO legal_chunks
                              (document_id, chunk_index, chunk_text, source_ref, source_url)
                            VALUES ($1, $2, $3, $4, $5)
                            ON CONFLICT DO NOTHING
                            """,
                            doc_id, art["idx"], art["text"], art["ref"], url,
                        )
                    total += 1
                except Exception as e:
                    log.debug(f"  {canton} chunk error: {e}")
            else:
                try:
                    await conn.execute(
                        """
                        INSERT INTO legal_chunks
                          (document_id, chunk_index, chunk_text, source_ref, source_url)
                        VALUES ($1, 0, $2, $3, $4)
                        ON CONFLICT DO NOTHING
                        """,
                        doc_id, raw_text[:4000], f"Loi fiscale {canton}", url,
                    )
                total += 1
            except Exception as e:
                log.debug(f"  {canton} raw chunk error: {e}")

        status["total_cantonal_chunks"] = total
        log.info(f"  {canton}: {len(articles) or 1} chunks")
        await asyncio.sleep(0.8)

    return total


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------

async def _run_job(job_id: str, request: IngestRequest):
    status = _jobs[job_id]
    status.update({"status": "running", "started_at": datetime.utcnow().isoformat()})

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            src = request.source

            if src in ("fedlex", "all"):
                status["phase"] = "Fedlex"
                codes = [(rs, ab, ti) for rs, ab, ti in PRIORITY_RS
                         if not request.rs_codes or rs in request.rs_codes]
                await ingest_fedlex_codes(conn, codes, status)

            if src in ("atf", "all"):
                status["phase"] = "ATF"
                await ingest_atf_decisions(conn, request.limit or 500, status)

            if src in ("cantonal_tax", "all"):
                status["phase"] = "Cantons fiscaux"
                cantons = request.cantons or list(CANTONAL_TAX_URLS.keys())
                await ingest_cantonal(conn, cantons, status)

        finally:
            await conn.close()

        status["status"] = "completed"
        status["completed_at"] = datetime.utcnow().isoformat()
        log.info(f"Job {job_id} completed. chunks={status.get('total_chunks',0)}")

    except Exception as e:
        log.error(f"Job {job_id} FAILED: {e}", exc_info=True)
        status["status"] = "failed"
        status["error"] = str(e)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/ingest")
async def start_ingestion(
    request: IngestRequest,
    background_tasks: BackgroundTasks,
    x_admin_key: Optional[str] = Header(None),
):
    check_admin_key(x_admin_key)
    job_id = f"job_{int(time.time())}"
    _jobs[job_id] = {
        "job_id": job_id,
        "source": request.source,
        "status": "queued",
        "created_at": datetime.utcnow().isoformat(),
        "current": "En attente...",
        "total_chunks": 0,
    }
    background_tasks.add_task(_run_job, job_id, request)
    return {"job_id": job_id, "status": "queued", "message": f"Ingestion '{request.source}' démarrée"}


@router.get("/ingest/status")
async def get_all_status(x_admin_key: Optional[str] = Header(None)):
    check_admin_key(x_admin_key)
    return {"jobs": list(_jobs.values())}


@router.get("/ingest/status/{job_id}")
async def get_job_status(job_id: str, x_admin_key: Optional[str] = Header(None)):
    check_admin_key(x_admin_key)
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    return _jobs[job_id]


@router.get("/db/stats")
async def db_stats(x_admin_key: Optional[str] = Header(None)):
    check_admin_key(x_admin_key)
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            # Run any missing migrations first
            for migration in [
                "ALTER TABLE legal_documents ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb",
                "ALTER TABLE legal_documents ADD COLUMN IF NOT EXISTS reference TEXT",
                "ALTER TABLE legal_documents ADD COLUMN IF NOT EXISTS jurisdiction TEXT DEFAULT 'CH'",
            ]:
                try:
                    await conn.execute(migration)
                except Exception:
                    pass

            docs = await conn.fetchval("SELECT COUNT(*) FROM legal_documents")
            chunks = await conn.fetchval("SELECT COUNT(*) FROM legal_chunks")

            # Check embedding column
            chunk_cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name='legal_chunks'"
            )]
            doc_cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name='legal_documents'"
            )]

            embedded = 0
            if "embedding" in chunk_cols:
                embedded = await conn.fetchval("SELECT COUNT(*) FROM legal_chunks WHERE embedding IS NOT NULL")

            by_source = await conn.fetch(
                "SELECT source, COUNT(*) as cnt FROM legal_documents GROUP BY source ORDER BY cnt DESC"
            )
            by_type = []
            if "doc_type" in doc_cols:
                by_type = await conn.fetch(
                    "SELECT doc_type, COUNT(*) as cnt FROM legal_documents GROUP BY doc_type"
                )
            users = await conn.fetchval("SELECT COUNT(*) FROM users")

        finally:
            await conn.close()

        return {
            "legal_documents": docs,
            "legal_chunks": chunks,
            "chunks_embedded": embedded,
            "chunks_to_embed": chunks - embedded,
            "by_source": [{"source": r["source"], "count": r["cnt"]} for r in by_source],
            "by_type": [{"type": r["doc_type"], "count": r["cnt"]} for r in by_type],
            "users": users,
            "legal_chunks_columns": chunk_cols,
            "legal_documents_columns": doc_cols,
        }
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")
