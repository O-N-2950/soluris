"""
Entscheidsuche Scraper v2 — Jurisprudence suisse complète
===========================================================
Couvre l'intégralité de la jurisprudence francophone disponible sur
entscheidsuche.ch (~280k décisions) :

  FÉDÉRAL :
    - CH_BGE  : Arrêts publiés ATF/BGE (~5 700 FR)
    - CH_BGer : Tous les arrêts TF (~57 800 FR)
    - CH_BVGE : Tribunal administratif fédéral (~25 500 FR)
    - CH_BSTG : Tribunal pénal fédéral (~3 700 FR)

  CANTONAL ROMAND :
    - GE : Genève — Cour de justice (~81 500 FR, depuis 1995)
    - VD : Vaud — Tribunal cantonal (~81 800 FR, depuis 1989)
    - FR : Fribourg — Tribunal cantonal (~11 600 FR)
    - NE : Neuchâtel — Tribunal cantonal (~7 400 FR)
    - VS : Valais — Tribunaux (~3 000 FR)
    - JU : Jura — Tribunal cantonal (~1 000 FR)

  TOTAL : ~282 700 décisions francophones — 0 CHF de licence.

Modes :
  --mode count         Statistiques complètes
  --mode atf           ATF publiés uniquement (5 700, rapide)
  --mode federal       Tout le fédéral FR (87 000)
  --mode canton GE     Un canton spécifique
  --mode romand        Les 6 cantons romands (186 000)
  --mode all           TOUT le corpus FR (282 000)
  --mode veille        Nouveaux arrêts des 7 derniers jours
  --mode crossref      Cross-ref législation ↔ jurisprudence

Coût estimé pour tout ingérer : 0 CHF (API ouverte)
Temps estimé : ~24h pour le corpus complet (rate-limited à 3 req/s)
"""

import argparse
import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Generator

import io

import pdfplumber
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SEARCH_URL = "https://entscheidsuche.ch/_search.php"
REQUEST_DELAY = 0.3        # 3 req/s — respectueux du serveur
REQUEST_TIMEOUT = 30
ES_PAGE_SIZE = 200         # max par page ES
MAX_RETRIES = 3
MAX_CHUNK_CHARS = 2500     # Taille idéale pour embedding (≈600 tokens)
MIN_CHUNK_CHARS = 50

# Cantons romands
CANTONS_ROMANDS = ["GE", "VD", "NE", "FR", "VS", "JU"]

# Courts hierarchy → human-readable names
COURTS = {
    # Fédéral
    "CH_BGE_999": ("ATF publiés", "federal"),
    "CH_BGE_012": ("CEDH", "federal"),
    "CH_BGer_001": ("TF — Ire Cour de droit public", "federal"),
    "CH_BGer_002": ("TF — IIe Cour de droit public", "federal"),
    "CH_BGer_004": ("TF — Ire Cour de droit civil", "federal"),
    "CH_BGer_005": ("TF — IIe Cour de droit civil", "federal"),
    "CH_BGer_006": ("TF — Cour de droit pénal", "federal"),
    "CH_BGer_007": ("TF — IIe Cour de droit pénal", "federal"),
    "CH_BGer_008": ("TF — Ire Cour de droit social", "federal"),
    "CH_BGer_009": ("TF — IIe Cour de droit social", "federal"),
    "CH_BGer_016": ("TF — Tribunal pénal fédéral", "federal"),
    "CH_BVGE_001": ("TAF — Tribunal administratif fédéral", "federal"),
    "CH_BSTG_001": ("TPF — Tribunal pénal fédéral", "federal"),
    # Genève
    "GE_CJ_001": ("GE — Chambre pénale d'appel et de révision", "GE"),
    "GE_CJ_002": ("GE — Chambre pénale de recours", "GE"),
    "GE_CJ_007": ("GE — Chambre des assurances sociales", "GE"),
    "GE_CJ_011": ("GE — Chambre administrative", "GE"),
    "GE_CJ_013": ("GE — Chambre civile", "GE"),
    "GE_CJ_014": ("GE — Chambre des baux et loyers", "GE"),
    # Vaud
    "VD_TC_002": ("VD — Cour d'appel pénale", "VD"),
    "VD_TC_004": ("VD — Cour d'appel civile", "VD"),
    "VD_TC_009": ("VD — Cour de droit administratif et public", "VD"),
    "VD_TC_010": ("VD — Chambre des recours pénale", "VD"),
    "VD_TC_013": ("VD — Cour des assurances sociales", "VD"),
    "VD_TC_031": ("VD — Chambre des recours civile", "VD"),
}

# Legal domain inference from case number prefix
DOMAIN_FROM_PREFIX = {
    "1": "droit_public", "2": "droit_public",
    "4": "droit_civil",  "5": "droit_civil",
    "6": "droit_penal",  "7": "droit_penal",
    "8": "droit_social", "9": "droit_social",
}
DOMAIN_FROM_BGE_VOL = {
    "I": "droit_public", "II": "droit_civil", "III": "droit_civil",
    "IV": "droit_penal", "V": "droit_social",
}
# Chamber-based domain inference for cantonal courts
DOMAIN_FROM_COURT = {
    "GE_CJ_001": "droit_penal", "GE_CJ_002": "droit_penal",
    "GE_CJ_007": "droit_social", "GE_CJ_011": "droit_administratif",
    "GE_CJ_013": "droit_civil", "GE_CJ_014": "droit_bail",
    "VD_TC_002": "droit_penal", "VD_TC_004": "droit_civil",
    "VD_TC_009": "droit_administratif", "VD_TC_010": "droit_penal",
    "VD_TC_013": "droit_social", "VD_TC_031": "droit_civil",
}

# Regex for detecting article references in decision text
ART_REF_PATTERN = re.compile(
    r'(?:art\.?\s*(\d+[a-z]?(?:\s*(?:al|let|ch|ss)\.?\s*\d*[a-z]?)*)\s+'
    r'(CO|CC|CP|CPC|CPP|LP|LTF|LDIP|LEI|LAT|Cst|LFus|LPGA|LAVS|LAMal|CEDH|LACI|'
    r'LPP|LCA|LDPJ|OBLF|LLCA|LPD|LCD|LBI|LDA|OJ|LaCC|LaCP|LIFD|LHID))',
    re.IGNORECASE
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("entscheidsuche")

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Decision:
    id: str
    date: str
    reference: list = field(default_factory=list)
    title_fr: str = ""
    abstract_fr: str = ""
    language: str = "fr"
    canton: str = ""             # "CH", "GE", "VD", etc.
    jurisdiction: str = ""       # "federal", "GE", "VD", etc.
    court: str = ""              # "CH_BGer", "CH_BGE", "GE_CJ"
    court_name: str = ""
    chamber: str = ""            # "CH_BGer_005", "GE_CJ_014"
    chamber_name: str = ""
    legal_domain: str = ""
    content_url: str = ""
    is_atf: bool = False
    article_refs: list = field(default_factory=list)  # Cross-ref: ["art. 271 CO", ...]
    doc_type: str = "jurisprudence"

@dataclass
class Chunk:
    chunk_id: str
    chunk_type: str              # "regeste", "faits", "considerant", "dispositif", "full_text"
    chunk_index: int = 0
    text: str = ""
    decision_id: str = ""
    decision_ref: str = ""
    decision_date: str = ""
    abstract_fr: str = ""
    language: str = "fr"
    canton: str = ""
    jurisdiction: str = ""
    court_name: str = ""
    chamber_name: str = ""
    legal_domain: str = ""
    is_atf: bool = False
    source_url: str = ""
    article_refs: list = field(default_factory=list)
    doc_type: str = "jurisprudence"

# ---------------------------------------------------------------------------
# Elasticsearch
# ---------------------------------------------------------------------------

_session = requests.Session()
_session.headers.update({"Content-Type": "application/json"})

def _es(query: dict) -> dict:
    for attempt in range(MAX_RETRIES):
        try:
            r = _session.post(SEARCH_URL, json=query, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                log.error(f"ES failed after {MAX_RETRIES} attempts: {e}")
                return {"hits": {"total": {"value": 0}, "hits": []}}
            time.sleep(1 * (attempt + 1))

def count(canton: str = None, hierarchy: str = None, lang: str = "fr") -> int:
    must = []
    if canton:
        must.append({"term": {"canton": canton}})
    if hierarchy:
        must.append({"term": {"hierarchy": hierarchy}})
    if lang:
        must.append({"term": {"attachment.language": lang}})
    q = {"query": {"bool": {"must": must}} if must else {"match_all": {}},
         "size": 0, "track_total_hits": True}
    return _es(q).get("hits", {}).get("total", {}).get("value", 0)

def search(canton: str = None, hierarchy: str = None, lang: str = "fr",
           date_from: str = None, date_to: str = None,
           limit: int = None) -> Generator:
    """Yield all ES hits matching filters using search_after pagination."""
    must = []
    if canton:
        must.append({"term": {"canton": canton}})
    if hierarchy:
        must.append({"term": {"hierarchy": hierarchy}})
    if lang:
        must.append({"term": {"attachment.language": lang}})
    if date_from or date_to:
        date_range = {}
        if date_from:
            date_range["gte"] = date_from
        if date_to:
            date_range["lte"] = date_to
        must.append({"range": {"date": date_range}})

    after = None
    total = 0
    page = 0
    while True:
        page += 1
        batch = min(ES_PAGE_SIZE, (limit - total) if limit else ES_PAGE_SIZE)
        if batch <= 0:
            break
        q = {
            "query": {"bool": {"must": must}} if must else {"match_all": {}},
            "size": batch,
            "sort": [{"date": "desc"}, {"_id": "asc"}],
            "_source": ["id", "date", "title", "reference", "abstract",
                        "attachment.content_url", "attachment.language",
                        "hierarchy", "canton"],
        }
        if after:
            q["search_after"] = after
        data = _es(q)
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break
        after = hits[-1].get("sort")
        total += len(hits)
        if page % 5 == 0 or page == 1:
            log.info(f"  ES page {page}: {total} hits so far")
        yield from hits
        if len(hits) < ES_PAGE_SIZE or (limit and total >= limit):
            break

# ---------------------------------------------------------------------------
# HTML parsing & chunking
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def _extract_article_refs(text: str) -> list[str]:
    """Extract references to legislation articles (art. X CO, etc.)."""
    refs = set()
    for m in ART_REF_PATTERN.finditer(text):
        ref = f"art. {m.group(1)} {m.group(2)}".strip()
        refs.add(ref)
    return sorted(refs)

def _infer_domain(ref: str, hierarchy: list) -> str:
    # From case number prefix (BGer)
    m = re.search(r'(\d)[A-Z]_', ref)
    if m and m.group(1) in DOMAIN_FROM_PREFIX:
        return DOMAIN_FROM_PREFIX[m.group(1)]
    # From BGE volume number
    m2 = re.search(r'BGE\s+\d+\s+(I+V?)\s', ref)
    if m2 and m2.group(1) in DOMAIN_FROM_BGE_VOL:
        return DOMAIN_FROM_BGE_VOL[m2.group(1)]
    # From court chamber (cantonal)
    for h in hierarchy:
        if h in DOMAIN_FROM_COURT:
            return DOMAIN_FROM_COURT[h]
    return "autre"

def _hit_to_decision(hit: dict) -> Decision:
    src = hit["_source"]
    hierarchy = src.get("hierarchy", [])
    ref = src.get("reference", [])
    ref_str = ref[0] if ref else src.get("id", "")
    canton = src.get("canton", "CH")

    # Find most specific court/chamber
    chambers = [h for h in hierarchy if "_" in h and len(h) > 3]
    chamber = chambers[-1] if chambers else ""
    court = chambers[0] if chambers else ""

    court_info = COURTS.get(chamber, COURTS.get(court, ("", "")))
    is_atf = any(h.startswith("CH_BGE") for h in hierarchy)
    jurisdiction = "federal" if canton == "CH" else canton

    return Decision(
        id=src["id"], date=src.get("date", ""), reference=ref,
        title_fr=src.get("title", {}).get("fr", ""),
        abstract_fr=_clean(src.get("abstract", {}).get("fr", "")),
        language=src.get("attachment", {}).get("language", ""),
        canton=canton, jurisdiction=jurisdiction,
        court=court, court_name=court_info[0] if court_info else court,
        chamber=chamber, chamber_name=COURTS.get(chamber, ("", ""))[0] if chamber in COURTS else chamber,
        legal_domain=_infer_domain(ref_str, hierarchy),
        content_url=src.get("attachment", {}).get("content_url", ""),
        is_atf=is_atf,
    )

def parse_content(full_text: str, dec: Decision) -> list[Chunk]:
    """Parse decision text (from HTML or PDF) → structured chunks optimized for RAG."""
    if not full_text.strip():
        return []

    ref_str = dec.reference[0] if dec.reference else dec.id
    art_refs = _extract_article_refs(full_text)
    dec.article_refs = art_refs

    lines = full_text.split("\n")
    sections = []  # [(type, [lines])]
    sec_type = "header"
    sec_lines = []

    regeste_re = re.compile(r"^(Regeste|Sachverhalt|Faits|Résumé)", re.I)
    consid_re = re.compile(
        r"^(Erwägung|Considérant|En droit|Aus den Erwägungen|"
        r"Extrait des considérants|Considérations en droit)", re.I)
    dispo_re = re.compile(r"^(Par ces motifs|Demnach erkennt|Dispositif)", re.I)

    for line in lines:
        s = line.strip()
        if not s:
            continue
        if regeste_re.search(s):
            if sec_lines: sections.append((sec_type, sec_lines))
            sec_type, sec_lines = "regeste", [s]
        elif consid_re.search(s):
            if sec_lines: sections.append((sec_type, sec_lines))
            sec_type, sec_lines = "considerant", [s]
        elif dispo_re.search(s):
            if sec_lines: sections.append((sec_type, sec_lines))
            sec_type, sec_lines = "dispositif", [s]
        else:
            sec_lines.append(s)
    if sec_lines:
        sections.append((sec_type, sec_lines))

    # Build chunks from sections, splitting large ones
    chunks = []
    idx = 0

    def _make_chunk(ctype, text, cidx):
        return Chunk(
            chunk_id=f"{dec.id}__{ctype}_{cidx}",
            chunk_type=ctype, chunk_index=cidx, text=text,
            decision_id=dec.id, decision_ref=ref_str,
            decision_date=dec.date, abstract_fr=dec.abstract_fr,
            language=dec.language, canton=dec.canton,
            jurisdiction=dec.jurisdiction, court_name=dec.court_name,
            chamber_name=dec.chamber_name, legal_domain=dec.legal_domain,
            is_atf=dec.is_atf, source_url=dec.content_url,
            article_refs=_extract_article_refs(text),
        )

    for sec_type, sec_lines in sections:
        text = "\n".join(sec_lines).strip()
        if len(text) < MIN_CHUNK_CHARS:
            continue

        if sec_type == "header" and len(text) > MAX_CHUNK_CHARS * 2:
            # Skip oversized headers (tribunal boilerplate)
            # Keep only first 500 chars as context
            chunks.append(_make_chunk("header", text[:500], idx))
            idx += 1
            continue

        if len(text) <= MAX_CHUNK_CHARS:
            chunks.append(_make_chunk(sec_type, text, idx))
            idx += 1
        else:
            # Split by numbered paragraphs or by size
            num_re = re.compile(r'^(\d+\.(?:\d+\.?)*)\s')
            sub_parts = []
            current = []
            current_len = 0

            for line in sec_lines:
                if num_re.match(line.strip()) and current_len > 200:
                    sub_parts.append("\n".join(current))
                    current, current_len = [line], len(line)
                elif current_len + len(line) > MAX_CHUNK_CHARS and current:
                    sub_parts.append("\n".join(current))
                    current, current_len = [line], len(line)
                else:
                    current.append(line)
                    current_len += len(line)
            if current:
                sub_parts.append("\n".join(current))

            for part in sub_parts:
                part = part.strip()
                if len(part) >= MIN_CHUNK_CHARS:
                    chunks.append(_make_chunk(sec_type, part, idx))
                    idx += 1

    # Fallback: single chunk if nothing was parsed
    if not chunks:
        text = full_text[:MAX_CHUNK_CHARS * 3]
        chunks.append(_make_chunk("full_text", text, 0))

    return chunks

# ---------------------------------------------------------------------------
# Download with thread pool (respectful parallelism)
# ---------------------------------------------------------------------------

def _download(url: str) -> tuple[bytes | None, str]:
    """Download content and return (bytes, content_type)."""
    for attempt in range(MAX_RETRIES):
        try:
            r = _session.get(url, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            ct = r.headers.get("Content-Type", "")
            return r.content, ct
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(0.5 * (attempt + 1))
    return None, ""

def _extract_pdf_text(content: bytes) -> str:
    """Extract text from a PDF using pdfplumber."""
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages_text = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)
            return "\n".join(pages_text)
    except Exception as e:
        log.warning(f"PDF extraction failed: {e}")
        return ""

def _content_to_text(content: bytes, content_type: str, url: str) -> str:
    """Convert downloaded content (HTML or PDF) to plain text."""
    if "pdf" in content_type.lower() or url.endswith(".pdf"):
        return _extract_pdf_text(content)
    else:
        # HTML
        soup = BeautifulSoup(content, "html.parser")
        paras = soup.find_all(["div", "p"])
        return "\n".join(
            p.get_text(strip=True) for p in paras
            if len(p.get_text(strip=True)) > 3
        )

# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def scrape(canton: str = None, hierarchy: str = None, lang: str = "fr",
           date_from: str = None, date_to: str = None,
           limit: int = None, output_dir: Path = Path("data/jurisprudence"),
           download: bool = True, batch_size: int = 500, label: str = "scrape") -> dict:
    """Main scraping pipeline with batch output."""
    tag = f"{canton or 'all'}_{hierarchy or 'all'}_{lang}"
    log.info(f"[{label}] Starting: canton={canton}, hierarchy={hierarchy}, "
             f"lang={lang}, limit={limit}")
    output_dir.mkdir(parents=True, exist_ok=True)

    stats = {"decisions": 0, "chunks": 0, "downloaded": 0, "failed": 0,
             "batches": 0, "article_refs_found": 0}
    batch_d, batch_c, batch_n = [], [], 0
    all_article_refs = {}  # ref → count (for cross-ref stats)

    for hit in search(canton=canton, hierarchy=hierarchy, lang=lang,
                      date_from=date_from, date_to=date_to, limit=limit):
        dec = _hit_to_decision(hit)
        stats["decisions"] += 1

        if download and dec.content_url:
            time.sleep(REQUEST_DELAY)
            content, ct = _download(dec.content_url)
            if content:
                text = _content_to_text(content, ct, dec.content_url)
                if text:
                    cks = parse_content(text, dec)
                    stats["downloaded"] += 1
                    stats["chunks"] += len(cks)
                    batch_c.extend([asdict(c) for c in cks])
                    for ref in dec.article_refs:
                        all_article_refs[ref] = all_article_refs.get(ref, 0) + 1
                        stats["article_refs_found"] += 1
                else:
                    stats["failed"] += 1
            else:
                stats["failed"] += 1
        else:
            # Metadata-only chunk
            batch_c.append(asdict(Chunk(
                chunk_id=f"{dec.id}__meta_0", chunk_type="metadata_only",
                text=dec.abstract_fr or dec.title_fr,
                decision_id=dec.id, decision_ref=dec.reference[0] if dec.reference else "",
                decision_date=dec.date, abstract_fr=dec.abstract_fr,
                language=dec.language, canton=dec.canton,
                jurisdiction=dec.jurisdiction, court_name=dec.court_name,
                chamber_name=dec.chamber_name, legal_domain=dec.legal_domain,
                is_atf=dec.is_atf, source_url=dec.content_url,
            )))
            stats["chunks"] += 1

        batch_d.append(asdict(dec))

        # Flush batch
        if len(batch_d) >= batch_size:
            batch_n += 1
            _save_batch(output_dir, tag, batch_n, batch_d, batch_c)
            log.info(f"  [{label}] Batch {batch_n}: {len(batch_d)} dec, {len(batch_c)} chunks "
                     f"(total: {stats['decisions']})")
            stats["batches"] += 1
            batch_d, batch_c = [], []

    # Final batch
    if batch_d:
        batch_n += 1
        _save_batch(output_dir, tag, batch_n, batch_d, batch_c)
        stats["batches"] += 1

    # Save cross-ref stats
    if all_article_refs:
        ref_file = output_dir / f"crossref_{tag}.json"
        sorted_refs = sorted(all_article_refs.items(), key=lambda x: -x[1])
        with open(ref_file, "w", encoding="utf-8") as f:
            json.dump({"article_references": dict(sorted_refs[:500]),
                        "total_refs": sum(all_article_refs.values()),
                        "unique_refs": len(all_article_refs)},
                      f, ensure_ascii=False, indent=2)
        log.info(f"  [{label}] Cross-ref saved: {len(all_article_refs)} unique refs → {ref_file.name}")

    log.info(f"  [{label}] DONE: {stats['decisions']} decisions, {stats['chunks']} chunks, "
             f"{stats['failed']} failed, {stats['article_refs_found']} article refs")
    return stats

def _save_batch(output_dir, tag, batch_n, decisions, chunks):
    fn = output_dir / f"{tag}_batch_{batch_n:04d}.json"
    with open(fn, "w", encoding="utf-8") as f:
        json.dump({"decisions": decisions, "chunks": chunks,
                   "meta": {"batch": batch_n, "count": len(decisions),
                            "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")}},
                  f, ensure_ascii=False, indent=2)

# ---------------------------------------------------------------------------
# High-level modes
# ---------------------------------------------------------------------------

def mode_count():
    """Print comprehensive statistics."""
    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║   ENTSCHEIDSUCHE.CH — Statistiques corpus francophone  ║")
    print("╚══════════════════════════════════════════════════════════╝\n")

    federal = [
        ("CH_BGE_999", "ATF publiés (BGE)"),
        ("CH_BGer", "Tous arrêts TF"),
        ("CH_BVGE", "Tribunal admin. fédéral (TAF)"),
        ("CH_BSTG", "Tribunal pénal fédéral (TPF)"),
    ]
    cantonal = [
        ("GE", "Genève"),
        ("VD", "Vaud"),
        ("FR", "Fribourg"),
        ("NE", "Neuchâtel"),
        ("VS", "Valais"),
        ("JU", "Jura"),
    ]

    total = 0
    print("FÉDÉRAL :")
    for h, name in federal:
        n = count(hierarchy=h, lang="fr")
        total += n
        print(f"  {name:40s} {n:>8} arrêts FR")

    print(f"\nCANTONAL ROMAND :")
    for c, name in cantonal:
        n = count(canton=c, lang="fr")
        total += n
        print(f"  {name:40s} {n:>8} arrêts FR")

    print(f"\n{'─'*55}")
    print(f"  {'TOTAL CORPUS FRANCOPHONE':40s} {total:>8} arrêts")
    print(f"\n  Coût de licence : 0 CHF ✓")
    print()

def mode_veille(output_dir: Path, days: int = 7):
    """Scrape new decisions from last N days."""
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    log.info(f"Veille juridique: arrêts depuis {date_from}")

    total_stats = {"decisions": 0, "chunks": 0}

    # Federal
    for hierarchy in ["CH_BGE_999", "CH_BGer"]:
        stats = scrape(hierarchy=hierarchy, lang="fr", date_from=date_from,
                       output_dir=output_dir / "veille", label=f"veille_{hierarchy}")
        total_stats["decisions"] += stats["decisions"]
        total_stats["chunks"] += stats["chunks"]

    # Cantons romands
    for canton in CANTONS_ROMANDS:
        stats = scrape(canton=canton, lang="fr", date_from=date_from,
                       output_dir=output_dir / "veille", label=f"veille_{canton}")
        total_stats["decisions"] += stats["decisions"]
        total_stats["chunks"] += stats["chunks"]

    print(f"\n=== Veille juridique ({days} derniers jours) ===")
    print(f"Nouveaux arrêts : {total_stats['decisions']}")
    print(f"Chunks générés  : {total_stats['chunks']}")

def mode_crossref(output_dir: Path):
    """Analyze cross-references between jurisprudence and legislation."""
    log.info("Analyzing cross-references from existing scraped data...")
    all_refs = {}

    for f in output_dir.glob("**/*.json"):
        if "crossref" in f.name:
            continue
        try:
            with open(f) as fh:
                data = json.load(fh)
            for dec in data.get("decisions", []):
                for ref in dec.get("article_refs", []):
                    all_refs[ref] = all_refs.get(ref, 0) + 1
        except Exception:
            continue

    if not all_refs:
        print("Pas de données de cross-ref trouvées. Lancez un scraping d'abord.")
        return

    sorted_refs = sorted(all_refs.items(), key=lambda x: -x[1])

    print(f"\n=== Cross-références législation ↔ jurisprudence ===")
    print(f"Articles de loi cités: {len(all_refs)} uniques")
    print(f"\nTop 30 articles les plus cités :")
    for ref, n in sorted_refs[:30]:
        print(f"  {ref:30s} → {n:>5} arrêts")

    # Save full report
    report = output_dir / "crossref_report.json"
    with open(report, "w", encoding="utf-8") as f:
        json.dump({
            "total_unique_refs": len(all_refs),
            "total_citations": sum(all_refs.values()),
            "top_100": dict(sorted_refs[:100]),
            "all_refs": dict(sorted_refs),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }, f, ensure_ascii=False, indent=2)
    print(f"\nRapport complet → {report}")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Entscheidsuche Scraper v2 — Jurisprudence suisse")
    p.add_argument("--mode", choices=["count", "atf", "federal", "canton", "romand", "all", "veille", "crossref"],
                   default="atf")
    p.add_argument("--canton", type=str, default=None, help="Canton code (GE, VD, etc.)")
    p.add_argument("--lang", default="fr")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--output", default="data/jurisprudence")
    p.add_argument("--no-download", action="store_true")
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--days", type=int, default=7, help="Jours pour mode veille")
    args = p.parse_args()
    out = Path(args.output)

    if args.mode == "count":
        mode_count()

    elif args.mode == "atf":
        stats = scrape(hierarchy="CH_BGE_999", lang=args.lang, limit=args.limit,
                       output_dir=out, download=not args.no_download,
                       batch_size=args.batch_size, label="ATF")
        print(f"\nATF: {stats['decisions']} arrêts, {stats['chunks']} chunks, "
              f"{stats['failed']} échecs, {stats['article_refs_found']} refs loi")

    elif args.mode == "federal":
        for h, name in [("CH_BGE_999", "ATF"), ("CH_BGer", "BGer"),
                         ("CH_BVGE", "TAF"), ("CH_BSTG", "TPF")]:
            stats = scrape(hierarchy=h, lang=args.lang, limit=args.limit,
                           output_dir=out, download=not args.no_download,
                           batch_size=args.batch_size, label=name)
            print(f"{name}: {stats['decisions']} arrêts, {stats['chunks']} chunks")

    elif args.mode == "canton":
        canton = args.canton
        if not canton:
            print("Spécifiez --canton (GE, VD, NE, FR, VS, JU)")
            return
        stats = scrape(canton=canton, lang=args.lang, limit=args.limit,
                       output_dir=out, download=not args.no_download,
                       batch_size=args.batch_size, label=canton)
        print(f"{canton}: {stats['decisions']} arrêts, {stats['chunks']} chunks")

    elif args.mode == "romand":
        for canton in CANTONS_ROMANDS:
            stats = scrape(canton=canton, lang=args.lang, limit=args.limit,
                           output_dir=out, download=not args.no_download,
                           batch_size=args.batch_size, label=canton)
            print(f"{canton}: {stats['decisions']} arrêts, {stats['chunks']} chunks")

    elif args.mode == "all":
        # Federal first
        for h in ["CH_BGE_999", "CH_BGer", "CH_BVGE", "CH_BSTG"]:
            scrape(hierarchy=h, lang=args.lang, limit=args.limit,
                   output_dir=out, download=not args.no_download,
                   batch_size=args.batch_size, label=h)
        # Then cantonal
        for canton in CANTONS_ROMANDS:
            scrape(canton=canton, lang=args.lang, limit=args.limit,
                   output_dir=out, download=not args.no_download,
                   batch_size=args.batch_size, label=canton)
        mode_crossref(out)

    elif args.mode == "veille":
        mode_veille(out, args.days)

    elif args.mode == "crossref":
        mode_crossref(out)

if __name__ == "__main__":
    main()
