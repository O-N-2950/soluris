"""
Admin Ingestion Endpoint — /api/admin/ingest
=============================================
Endpoint protégé par clé admin permettant de déclencher l'ingestion
des données juridiques depuis l'intérieur de Railway (accès DB interne).

Usage :
  POST /api/admin/ingest
  Headers: X-Admin-Key: <ADMIN_INGEST_KEY>
  Body: {"source": "fedlex" | "atf" | "cantonal_tax" | "all"}

  GET /api/admin/ingest/status
  Headers: X-Admin-Key: <ADMIN_INGEST_KEY>
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

router = APIRouter(prefix="/api/admin", tags=["admin"])

ADMIN_KEY = os.getenv("ADMIN_INGEST_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# In-memory job status (reset on redeploy)
_jobs: dict = {}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def check_admin_key(x_admin_key: Optional[str] = Header(None)):
    if not ADMIN_KEY:
        raise HTTPException(503, "ADMIN_INGEST_KEY not configured")
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(403, "Invalid admin key")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    source: str = "all"  # fedlex | atf | cantonal_tax | all
    rs_codes: Optional[list[str]] = None  # Pour fedlex: codes RS spécifiques
    cantons: Optional[list[str]] = None   # Pour cantonal_tax: cantons spécifiques
    limit: Optional[int] = None           # Limiter le nombre d'entrées (test)


# ---------------------------------------------------------------------------
# Ingestion Fedlex (codes prioritaires)
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

SPARQL_ENDPOINT = "https://fedlex.data.admin.ch/sparqlendpoint"
REQUEST_DELAY = 0.8


def _sparql_query(query: str) -> list:
    """Exécute une requête SPARQL et retourne les bindings."""
    resp = requests.post(
        SPARQL_ENDPOINT,
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["results"]["bindings"]


def _get_html_url(rs_number: str) -> Optional[str]:
    """Trouve l'URL HTML de la consolidation la plus récente."""
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
        rows = _sparql_query(query)
        if rows:
            return rows[0]["url"]["value"]
    except Exception as e:
        log.warning(f"SPARQL error for RS {rs_number}: {e}")
    return None


def _parse_html_articles(html_url: str, rs_number: str, title: str, abbrev: str) -> list[dict]:
    """Parse le HTML Fedlex et extrait les articles."""
    try:
        resp = requests.get(html_url, timeout=60)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.content, "html.parser")
    except Exception as e:
        log.warning(f"HTTP error for {html_url}: {e}")
        return []

    articles = soup.find_all("article")
    chunks = []

    for art in articles:
        art_id = art.get("id", "")
        # Heading
        heading = art.find(["h6", "h5", "h4", "h3"])
        art_num = heading.get_text(strip=True) if heading else art_id

        # Section path
        path_parts = []
        parent = art.parent
        while parent and parent.name:
            if parent.name == "section":
                h = parent.find(["h1", "h2", "h3", "h4", "h5", "h6"], recursive=False)
                if h:
                    path_parts.insert(0, h.get_text(strip=True)[:60])
            parent = parent.parent
        section_path = " > ".join(path_parts)

        # Text
        paragraphs = art.find_all("p")
        text_parts = [p.get_text(" ", strip=True) for p in paragraphs]
        text = "\n".join(t for t in text_parts if t)

        if len(text.strip()) < 20:
            continue

        chunks.append({
            "article_ref": f"{art_num} {abbrev}".strip(),
            "section_path": section_path,
            "text": text[:4000],
            "url": html_url,
            "rs_number": rs_number,
            "law_title": title,
            "law_abbrev": abbrev,
        })

    return chunks


async def ingest_fedlex(conn: asyncpg.Connection, rs_list: list = None, job_id: str = None, status: dict = None):
    """Ingère les codes RS prioritaires dans legal_documents + legal_chunks."""
    codes = rs_list or [(rs, abbr, title) for rs, abbr, title in PRIORITY_RS]
    total_chunks = 0

    for i, item in enumerate(codes):
        if isinstance(item, (list, tuple)) and len(item) == 3:
            rs_number, abbrev, title = item
        else:
            rs_number = item
            abbrev = rs_number
            title = f"RS {rs_number}"

        if status:
            status["current"] = f"Fedlex RS {rs_number} ({abbrev}) [{i+1}/{len(codes)}]"
            status["progress"] = f"{i+1}/{len(codes)}"

        log.info(f"Scraping RS {rs_number} ({abbrev})...")

        # Get HTML URL
        html_url = _get_html_url(rs_number)
        if not html_url:
            log.warning(f"  No HTML URL for RS {rs_number}")
            continue

        time.sleep(REQUEST_DELAY)

        # Parse articles
        chunks = _parse_html_articles(html_url, rs_number, title, abbrev)
        if not chunks:
            log.warning(f"  No articles parsed for RS {rs_number}")
            continue

        # Full text for document
        full_text = "\n\n".join(c["text"] for c in chunks)

        # Insert document
        try:
            doc_id = await conn.fetchval(
                """
                INSERT INTO legal_documents
                  (source, external_id, doc_type, title, reference, language, content, url, metadata)
                VALUES ('fedlex', $1, 'legislation', $2, $3, 'fr', $4, $5, $6)
                ON CONFLICT (source, external_id) DO UPDATE
                  SET title=EXCLUDED.title, content=EXCLUDED.content, url=EXCLUDED.url,
                      updated_at=NOW()
                RETURNING id
                """,
                rs_number, title, f"RS {rs_number}",
                full_text[:100000],
                html_url,
                json.dumps({"rs_number": rs_number, "abbrev": abbrev, "source_url": html_url}),
            )
        except Exception as e:
            log.error(f"  DB error inserting doc RS {rs_number}: {e}")
            continue

        # Insert chunks
        chunk_count = 0
        for idx, chunk in enumerate(chunks):
            try:
                await conn.execute(
                    """
                    INSERT INTO legal_chunks
                      (document_id, chunk_index, content, chunk_type, reference, section_path, url, metadata)
                    VALUES ($1, $2, $3, 'article', $4, $5, $6, $7)
                    ON CONFLICT DO NOTHING
                    """,
                    doc_id, idx,
                    chunk["text"],
                    chunk["article_ref"],
                    chunk["section_path"],
                    chunk["url"],
                    json.dumps({
                        "rs_number": chunk["rs_number"],
                        "law_title": chunk["law_title"],
                        "law_abbrev": chunk["law_abbrev"],
                        "jurisdiction": "CH",
                        "legal_domain": "federal",
                    }),
                )
                chunk_count += 1
            except Exception as e:
                log.debug(f"  Chunk insert error: {e}")

        total_chunks += chunk_count
        log.info(f"  RS {rs_number}: {chunk_count} chunks ingérés")

        if status:
            status["total_chunks"] = total_chunks

    return total_chunks


# ---------------------------------------------------------------------------
# Ingestion ATF (Entscheidsuche)
# ---------------------------------------------------------------------------

ATF_SEARCH_URL = "https://entscheidsuche.ch/_search.php"


def _search_atf(limit: int = 1000, offset: int = 0) -> list[dict]:
    """Recherche les ATF publiés en français via Elasticsearch."""
    payload = {
        "query": {
            "bool": {
                "must": [
                    {"term": {"Sprache": "fr"}},
                    {"prefix": {"SignaturNummer": "ATF"}},
                ]
            }
        },
        "sort": [{"Datum": {"order": "desc"}}],
        "from": offset,
        "size": min(limit, 100),
    }
    try:
        resp = requests.post(
            ATF_SEARCH_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("hits", {}).get("hits", [])
    except Exception as e:
        log.warning(f"ATF search error: {e}")
        return []


async def ingest_atf(conn: asyncpg.Connection, limit: int = 500, status: dict = None):
    """Ingère les arrêts ATF depuis Entscheidsuche."""
    total = 0
    offset = 0
    batch = 100

    while total < limit:
        hits = _search_atf(batch, offset)
        if not hits:
            break

        for hit in hits:
            src = hit.get("_source", {})
            ref = src.get("SignaturNummer", "")
            date = src.get("Datum", "")
            title = src.get("Titel", ref)
            text = src.get("Text", src.get("Zusammenfassung", ""))
            url = src.get("URL", "")
            domain = src.get("Bereich", "")

            if not text or len(text.strip()) < 50:
                continue

            try:
                doc_id = await conn.fetchval(
                    """
                    INSERT INTO legal_documents
                      (source, external_id, doc_type, title, reference, language, content, url, metadata)
                    VALUES ('entscheidsuche', $1, 'jurisprudence', $2, $3, 'fr', $4, $5, $6)
                    ON CONFLICT (source, external_id) DO UPDATE
                      SET title=EXCLUDED.title, content=EXCLUDED.content, updated_at=NOW()
                    RETURNING id
                    """,
                    ref, title, ref,
                    text[:100000], url,
                    json.dumps({"date": date, "domain": domain, "court": "TF", "jurisdiction": "CH"}),
                )

                # Each ATF as one chunk (regeste + considérants)
                await conn.execute(
                    """
                    INSERT INTO legal_chunks
                      (document_id, chunk_index, content, chunk_type, reference, url, metadata)
                    VALUES ($1, 0, $2, 'decision', $3, $4, $5)
                    ON CONFLICT DO NOTHING
                    """,
                    doc_id, text[:4000], ref, url,
                    json.dumps({"court": "TF", "date": date, "legal_domain": domain, "jurisdiction": "CH"}),
                )
                total += 1
            except Exception as e:
                log.debug(f"ATF insert error {ref}: {e}")

        if status:
            status["current"] = f"ATF: {total} arrêts ingérés"
            status["total_atf"] = total

        offset += batch
        if len(hits) < batch:
            break

        time.sleep(0.3)

    return total


# ---------------------------------------------------------------------------
# Ingestion lois fiscales cantonales
# ---------------------------------------------------------------------------

CANTONAL_TAX_URLS = {
    "GE": ("https://www.ge.ch/legislation/rsg/f/rsg_D_3_70.html", "Loi sur l'imposition des personnes physiques GE"),
    "VD": ("https://prestations.vd.ch/pub/blv-publication/actes/consulter/642.11.1", "Loi sur les impôts directs cantonaux VD"),
    "NE": ("https://rsn.ne.ch/DATA/program/books/rsne/htm/64211.htm", "Loi sur les contributions directes NE"),
    "FR": ("https://bdlf.fr.ch/app/fr/texts_of_law/632.1", "Loi sur les impôts cantonaux directs FR"),
    "JU": ("https://rsju.jura.ch/fr/viewdocument.html?idn=28029", "Loi d'impôt jurassienne"),
    "VS": ("https://lex.vs.ch/app/fr/texts_of_law/642.1", "Loi fiscale valaisanne"),
    "BE": ("https://www.belex.sites.be.ch/app/fr/texts_of_law/661.11", "Loi sur les impôts BE"),
    "ZH": ("https://www.zhlex.zh.ch/Erlass.html?Open&Ordnr=631.1", "Steuergesetz ZH"),
    "ZG": ("https://bgs.zg.ch/app/de/texts_of_law/632.1", "Steuergesetz ZG"),
    "SG": ("https://www.gesetzessammlung.sg.ch/app/de/texts_of_law/811.1", "Steuergesetz SG"),
    "AG": ("https://gesetzessammlungen.ag.ch/app/de/texts_of_law/651", "Steuergesetz AG"),
    "LU": ("https://gesetz.lu.ch/app/de/texts_of_law/620", "Steuergesetz LU"),
    "BS": ("https://www.gesetzessammlung.bs.ch/app/de/texts_of_law/640.100", "Steuergesetz BS"),
    "BL": ("https://www.bl.ch/fileadmin/user_upload/bl/pdf/Steuergesetz.pdf", "Steuergesetz BL"),
    "SO": ("https://bgs.so.ch/app/de/texts_of_law/611.1", "Steuergesetz SO"),
    "TG": ("https://rechtsbuch.tg.ch/app/de/texts_of_law/640", "Steuergesetz TG"),
    "TI": ("https://m3.ti.ch/CAN/RLeggi/public/index.php/component/rescontent/detail?id=16956", "Legge tributaria TI"),
    "GR": ("https://www.gr-lex.gr.ch/app/de/texts_of_law/720.200", "Steuergesetz GR"),
    "GL": ("https://gesetze.gl.ch/app/de/texts_of_law/613.1", "Steuergesetz GL"),
    "NW": ("https://gesetze.nw.ch/app/de/texts_of_law/621.1", "Steuergesetz NW"),
    "OW": ("https://ow.codex.ch/app/de/texts_of_law/631.4", "Steuergesetz OW"),
    "SZ": ("https://www.sz.ch/public/upload/assets/38568/612.110.pdf", "Steuergesetz SZ (PDF)"),
    "SH": ("https://www.rechtssammlung.sh.ch/app/de/texts_of_law/641.100", "Steuergesetz SH"),
    "UR": ("https://www.ur.ch/legistik/rechtssammlung/recht/recht.php?aid=1706", "Steuergesetz UR"),
    "AI": ("https://lex.appenzell-innerrhoden.ch/app/de/texts_of_law/620", "Steuergesetz AI"),
    "AR": ("https://www.ar.ch/app/de/texts_of_law/621.0", "Steuergesetz AR"),
}


async def ingest_cantonal_tax(conn: asyncpg.Connection, cantons: list[str] = None, status: dict = None):
    """Ingère les lois fiscales cantonales."""
    targets = cantons or list(CANTONAL_TAX_URLS.keys())
    total_chunks = 0

    for i, canton in enumerate(targets):
        if canton not in CANTONAL_TAX_URLS:
            log.warning(f"Canton {canton} not in catalog")
            continue

        url, law_title = CANTONAL_TAX_URLS[canton]

        if status:
            status["current"] = f"Canton {canton}: {law_title} [{i+1}/{len(targets)}]"

        # Skip PDFs for now (would need pdfminer)
        if url.endswith(".pdf"):
            log.info(f"  {canton}: PDF — skipped (need pdfminer)")
            if status:
                status.setdefault("skipped", []).append(f"{canton} (PDF)")
            continue

        try:
            resp = requests.get(url, timeout=30, headers={"Accept-Language": "fr"})
            resp.raise_for_status()
            resp.encoding = "utf-8"
        except Exception as e:
            log.warning(f"  {canton}: HTTP error: {e}")
            if status:
                status.setdefault("errors", []).append(f"{canton}: {str(e)[:80]}")
            continue

        soup = BeautifulSoup(resp.content, "html.parser")

        # Extract articles
        articles = soup.find_all("article") or soup.find_all(class_="article")
        if not articles:
            # Fallback: find all sections with headings
            articles = soup.find_all(["section", "div"], class_=lambda c: c and "art" in c.lower())

        if not articles:
            # Last resort: grab all paragraphs
            body = soup.find("main") or soup.find("body")
            text = body.get_text("\n", strip=True)[:50000] if body else ""
            if len(text) < 100:
                log.warning(f"  {canton}: No content found")
                continue

            # Store as single document
            try:
                doc_id = await conn.fetchval(
                    """
                    INSERT INTO legal_documents
                      (source, external_id, doc_type, title, reference, language, content, url, metadata)
                    VALUES ('cantonal_tax', $1, 'legislation', $2, $3, 'fr', $4, $5, $6)
                    ON CONFLICT (source, external_id) DO UPDATE
                      SET content=EXCLUDED.content, updated_at=NOW()
                    RETURNING id
                    """,
                    f"{canton}_tax", law_title, f"Loi fiscale {canton}",
                    text, url,
                    json.dumps({"canton": canton, "jurisdiction": canton, "legal_domain": "fiscal"}),
                )
                await conn.execute(
                    """
                    INSERT INTO legal_chunks
                      (document_id, chunk_index, content, chunk_type, reference, url, metadata)
                    VALUES ($1, 0, $2, 'law_text', $3, $4, $5)
                    ON CONFLICT DO NOTHING
                    """,
                    doc_id, text[:4000], f"Loi fiscale {canton}", url,
                    json.dumps({"canton": canton, "jurisdiction": canton, "legal_domain": "fiscal"}),
                )
                total_chunks += 1
                log.info(f"  {canton}: ingéré comme bloc unique ({len(text)} chars)")
            except Exception as e:
                log.error(f"  {canton}: DB error: {e}")
            continue

        # Parse article by article
        doc_id = await conn.fetchval(
            """
            INSERT INTO legal_documents
              (source, external_id, doc_type, title, reference, language, content, url, metadata)
            VALUES ('cantonal_tax', $1, 'legislation', $2, $3, 'fr', $4, $5, $6)
            ON CONFLICT (source, external_id) DO UPDATE
              SET title=EXCLUDED.title, updated_at=NOW()
            RETURNING id
            """,
            f"{canton}_tax", law_title, f"Loi fiscale {canton}",
            f"Loi fiscale cantonale — {canton}", url,
            json.dumps({"canton": canton, "jurisdiction": canton, "legal_domain": "fiscal"}),
        )

        canton_chunks = 0
        for idx, art in enumerate(articles):
            text = art.get_text("\n", strip=True)
            if len(text.strip()) < 30:
                continue

            heading = art.find(["h1", "h2", "h3", "h4", "h5", "h6"])
            art_ref = heading.get_text(strip=True)[:60] if heading else f"Art. {idx+1}"

            try:
                await conn.execute(
                    """
                    INSERT INTO legal_chunks
                      (document_id, chunk_index, content, chunk_type, reference, url, metadata)
                    VALUES ($1, $2, $3, 'article', $4, $5, $6)
                    ON CONFLICT DO NOTHING
                    """,
                    doc_id, idx, text[:4000], art_ref, url,
                    json.dumps({"canton": canton, "jurisdiction": canton, "legal_domain": "fiscal"}),
                )
                canton_chunks += 1
            except Exception as e:
                log.debug(f"  {canton} chunk error: {e}")

        total_chunks += canton_chunks
        log.info(f"  {canton}: {canton_chunks} articles ingérés")
        if status:
            status["total_chunks"] = total_chunks

        time.sleep(REQUEST_DELAY)

    return total_chunks


# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------

async def _run_ingestion_job(job_id: str, request: IngestRequest):
    """Tâche de fond qui execute l'ingestion complète."""
    status = _jobs[job_id]
    status["status"] = "running"
    status["started_at"] = datetime.utcnow().isoformat()
    status["total_chunks"] = 0
    status["total_atf"] = 0

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            src = request.source

            if src in ("fedlex", "all"):
                status["phase"] = "Fedlex — législation fédérale"
                rs_list = [(rs, abbr, t) for rs, abbr, t in PRIORITY_RS
                          if not request.rs_codes or rs in request.rs_codes]
                n = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: asyncio.run(ingest_fedlex_sync(conn, rs_list, job_id, status))
                )
                status["fedlex_chunks"] = status.get("total_chunks", 0)

            if src in ("atf", "all"):
                status["phase"] = "ATF — jurisprudence Tribunal fédéral"
                limit = request.limit or 1000
                n = await ingest_atf(conn, limit, status)
                status["atf_count"] = n

            if src in ("cantonal_tax", "all"):
                status["phase"] = "Lois fiscales cantonales"
                cantons = request.cantons
                n = await ingest_cantonal_tax(conn, cantons, status)
                status["cantonal_chunks"] = n

        finally:
            await conn.close()

        status["status"] = "completed"
        status["completed_at"] = datetime.utcnow().isoformat()

    except Exception as e:
        log.error(f"Job {job_id} failed: {e}", exc_info=True)
        status["status"] = "failed"
        status["error"] = str(e)


async def ingest_fedlex_sync(conn, rs_list, job_id, status):
    """Wrapper pour ingest_fedlex dans executor."""
    return await ingest_fedlex(conn, rs_list, job_id, status)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/ingest")
async def start_ingestion(
    request: IngestRequest,
    background_tasks: BackgroundTasks,
    x_admin_key: Optional[str] = Header(None),
):
    """Démarre l'ingestion en arrière-plan. Retourne un job_id."""
    check_admin_key(x_admin_key)

    job_id = f"job_{int(time.time())}"
    _jobs[job_id] = {
        "job_id": job_id,
        "source": request.source,
        "status": "queued",
        "created_at": datetime.utcnow().isoformat(),
        "current": "En attente...",
        "progress": "0/0",
    }

    background_tasks.add_task(_run_ingestion_job, job_id, request)

    return {"job_id": job_id, "status": "queued", "message": f"Ingestion '{request.source}' démarrée"}


@router.get("/ingest/status")
async def get_status(x_admin_key: Optional[str] = Header(None)):
    """Retourne le statut de tous les jobs d'ingestion."""
    check_admin_key(x_admin_key)
    return {"jobs": list(_jobs.values())}


@router.get("/ingest/status/{job_id}")
async def get_job_status(job_id: str, x_admin_key: Optional[str] = Header(None)):
    """Retourne le statut d'un job spécifique."""
    check_admin_key(x_admin_key)
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    return _jobs[job_id]


@router.get("/db/stats")
async def db_stats(x_admin_key: Optional[str] = Header(None)):
    """Compte les entrées dans les tables principales."""
    check_admin_key(x_admin_key)
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            docs = await conn.fetchval("SELECT COUNT(*) FROM legal_documents")
            chunks = await conn.fetchval("SELECT COUNT(*) FROM legal_chunks")
            embedded = await conn.fetchval(
                "SELECT COUNT(*) FROM legal_chunks WHERE embedding IS NOT NULL"
            )
            by_source = await conn.fetch(
                "SELECT source, COUNT(*) as cnt FROM legal_documents GROUP BY source ORDER BY cnt DESC"
            )
            by_canton = await conn.fetch(
                "SELECT metadata->>'canton' as canton, COUNT(*) FROM legal_chunks WHERE metadata->>'canton' IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 30"
            )
            users = await conn.fetchval("SELECT COUNT(*) FROM users")
        finally:
            await conn.close()

        return {
            "legal_documents": docs,
            "legal_chunks": chunks,
            "chunks_embedded": embedded,
            "chunks_not_embedded": chunks - embedded,
            "by_source": [{"source": r["source"], "count": r["cnt"]} for r in by_source],
            "by_canton": [{"canton": r["canton"], "count": r[1]} for r in by_canton],
            "users": users,
        }
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")
