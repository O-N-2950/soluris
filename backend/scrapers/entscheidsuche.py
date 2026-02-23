"""
Entscheidsuche Scraper — Jurisprudence du Tribunal fédéral (TF)
================================================================
Interroge l'API Elasticsearch d'entscheidsuche.ch pour extraire
les arrêts du Tribunal fédéral suisse.

Sources de données :
  - CH_BGE / CH_BGE_999 : Arrêts publiés (ATF) — arrêts de principe (~21k)
  - CH_BGer : Tous les arrêts du TF (~175k)

Pipeline :
  1. Elasticsearch → liste des arrêts avec métadonnées
  2. Pour chaque arrêt → téléchargement HTML
  3. Parsing HTML → extraction structurée (regeste, faits, considérants)
  4. Export JSON prêt pour insertion en base

Usage :
  python -m backend.scrapers.entscheidsuche --mode count          # Compter les arrêts
  python -m backend.scrapers.entscheidsuche --mode atf             # Scraper les ATF (FR)
  python -m backend.scrapers.entscheidsuche --mode bger --lang fr  # Scraper tous les BGer (FR)
  python -m backend.scrapers.entscheidsuche --mode atf --limit 100 # Limiter à 100 arrêts
"""

import argparse
import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SEARCH_URL = "https://entscheidsuche.ch/_search.php"
REQUEST_DELAY = 0.3
REQUEST_TIMEOUT = 30
ES_PAGE_SIZE = 100
MAX_RETRIES = 3

CHAMBERS = {
    "CH_BGer_001": "Ire Cour de droit public",
    "CH_BGer_002": "IIe Cour de droit public",
    "CH_BGer_004": "Ire Cour de droit civil",
    "CH_BGer_005": "IIe Cour de droit civil",
    "CH_BGer_006": "Cour de droit pénal",
    "CH_BGer_007": "IIe Cour de droit pénal",
    "CH_BGer_008": "Ire Cour de droit social",
    "CH_BGer_009": "IIe Cour de droit social",
    "CH_BGer_016": "Tribunal pénal fédéral",
    "CH_BGE_999": "ATF (arrêts publiés)",
    "CH_BGE_012": "CEDH",
}

DOMAIN_MAP = {
    "1": "droit_public", "2": "droit_public",
    "4": "droit_civil", "5": "droit_civil",
    "6": "droit_penal", "7": "droit_penal",
    "8": "droit_social", "9": "droit_social",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("entscheidsuche")

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class CourtDecision:
    id: str
    date: str
    reference: list = field(default_factory=list)
    title_fr: str = ""
    abstract_fr: str = ""
    language: str = "fr"
    court: str = ""
    chamber: str = ""
    chamber_name: str = ""
    legal_domain: str = ""
    content_url: str = ""
    is_atf: bool = False
    doc_type: str = "jurisprudence"

@dataclass
class JurisprudenceChunk:
    chunk_id: str
    chunk_type: str
    chunk_index: int = 0
    text: str = ""
    decision_id: str = ""
    decision_ref: str = ""
    decision_date: str = ""
    abstract_fr: str = ""
    language: str = "fr"
    court: str = ""
    chamber_name: str = ""
    legal_domain: str = ""
    is_atf: bool = False
    source_url: str = ""
    doc_type: str = "jurisprudence"

# ---------------------------------------------------------------------------
# Elasticsearch
# ---------------------------------------------------------------------------

def _es_search(query: dict) -> dict:
    try:
        r = requests.post(SEARCH_URL, json=query, timeout=REQUEST_TIMEOUT,
                          headers={"Content-Type": "application/json"})
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        log.error(f"ES search failed: {e}")
        return {"hits": {"total": {"value": 0}, "hits": []}}

def count_decisions(hierarchy: str, lang: str = None) -> int:
    must = [{"term": {"hierarchy": hierarchy}}]
    if lang:
        must.append({"term": {"attachment.language": lang}})
    data = _es_search({"query": {"bool": {"must": must}}, "size": 0})
    return data.get("hits", {}).get("total", {}).get("value", 0)

def iterate_decisions(hierarchy: str, lang: str = "fr", limit: int = None):
    after_sort = None
    total = 0
    page = 0
    while True:
        page += 1
        batch_size = min(ES_PAGE_SIZE, (limit - total) if limit else ES_PAGE_SIZE)
        if batch_size <= 0:
            break
        must = [{"term": {"hierarchy": hierarchy}}]
        if lang:
            must.append({"term": {"attachment.language": lang}})
        query = {
            "query": {"bool": {"must": must}},
            "size": batch_size,
            "sort": [{"date": {"order": "desc"}}, {"_id": {"order": "asc"}}],
            "_source": ["id", "date", "title", "reference", "abstract",
                        "attachment.content_url", "attachment.language", "hierarchy"],
        }
        if after_sort:
            query["search_after"] = after_sort
        data = _es_search(query)
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break
        after_sort = hits[-1].get("sort")
        total += len(hits)
        log.info(f"  Page {page}: +{len(hits)} (total: {total})")
        yield from hits
        if len(hits) < ES_PAGE_SIZE:
            break
        if limit and total >= limit:
            break

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def _infer_domain(ref: str) -> str:
    m = re.search(r'(\d)[A-Z]_', ref)
    if m:
        return DOMAIN_MAP.get(m.group(1), "autre")
    m2 = re.search(r'BGE\s+\d+\s+(I+V?)\s', ref)
    if m2:
        return {"I": "droit_public", "II": "droit_civil", "III": "droit_civil",
                "IV": "droit_penal", "V": "droit_social"}.get(m2.group(1), "autre")
    return "autre"

def _hit_to_decision(hit: dict) -> CourtDecision:
    src = hit["_source"]
    hierarchy = src.get("hierarchy", [])
    ref = src.get("reference", [])
    ref_str = ref[0] if ref else src.get("id", "")
    chambers = [h for h in hierarchy if h.startswith(("CH_BGer_", "CH_BGE_"))]
    ch_code = chambers[-1] if chambers else ""
    is_atf = any(h.startswith("CH_BGE") for h in hierarchy)
    return CourtDecision(
        id=src["id"], date=src.get("date", ""), reference=ref,
        title_fr=src.get("title", {}).get("fr", ""),
        abstract_fr=_clean_html(src.get("abstract", {}).get("fr", "")),
        language=src.get("attachment", {}).get("language", ""),
        court="CH_BGE" if is_atf else "CH_BGer",
        chamber=ch_code, chamber_name=CHAMBERS.get(ch_code, ch_code),
        legal_domain=_infer_domain(ref_str),
        content_url=src.get("attachment", {}).get("content_url", ""),
        is_atf=is_atf,
    )

def parse_decision_html(html_content: bytes, decision: CourtDecision) -> list[JurisprudenceChunk]:
    soup = BeautifulSoup(html_content, "html.parser")
    paras = soup.find_all(["div", "p"])
    full_text = "\n".join(
        p.get_text(strip=True) for p in paras
        if p.get_text(strip=True) and len(p.get_text(strip=True)) > 3
    )
    if not full_text.strip():
        return []

    ref_str = decision.reference[0] if decision.reference else decision.id
    chunks = []
    lines = full_text.split("\n")
    current_section = "header"
    current_text = []
    chunk_index = 0

    regeste_re = re.compile(r"^(Regeste|Sachverhalt|Faits|Résumé)", re.I)
    consid_re = re.compile(
        r"^(Erwägung|Considérant|En droit|Aus den Erwägungen|Extrait des considérants|"
        r"Auszug aus den Erwägungen|Considérations en droit)", re.I)
    dispo_re = re.compile(r"^(Par ces motifs|Demnach erkennt|Dispositif)", re.I)
    num_re = re.compile(r"^(\d+\.(?:\d+\.?)*)\s")

    def flush():
        nonlocal chunk_index
        text = "\n".join(current_text).strip()
        if not text or len(text) < 20:
            return
        chunks.append(JurisprudenceChunk(
            chunk_id=f"{decision.id}__{current_section}_{chunk_index}",
            chunk_type=current_section, chunk_index=chunk_index, text=text,
            decision_id=decision.id, decision_ref=ref_str,
            decision_date=decision.date, abstract_fr=decision.abstract_fr,
            language=decision.language, court=decision.court,
            chamber_name=decision.chamber_name, legal_domain=decision.legal_domain,
            is_atf=decision.is_atf, source_url=decision.content_url,
        ))
        chunk_index += 1

    for line in lines:
        s = line.strip()
        if not s:
            continue
        if regeste_re.search(s):
            flush(); current_section = "regeste"; current_text = [s]
        elif consid_re.search(s):
            flush(); current_section = "considerant"; current_text = [s]
        elif dispo_re.search(s):
            flush(); current_section = "dispositif"; current_text = [s]
        elif current_section == "considerant" and num_re.match(s):
            if current_text and len("\n".join(current_text)) > 100:
                flush(); current_section = "considerant"
            current_text.append(s)
        else:
            current_text.append(s)
    flush()

    if not chunks:
        chunks.append(JurisprudenceChunk(
            chunk_id=f"{decision.id}__full_0", chunk_type="full_text",
            chunk_index=0, text=full_text[:10000],
            decision_id=decision.id, decision_ref=ref_str,
            decision_date=decision.date, abstract_fr=decision.abstract_fr,
            language=decision.language, court=decision.court,
            chamber_name=decision.chamber_name, legal_domain=decision.legal_domain,
            is_atf=decision.is_atf, source_url=decision.content_url,
        ))

    # Split large chunks
    MAX_CHUNK = 3000
    final = []
    for c in chunks:
        if len(c.text) > MAX_CHUNK and c.chunk_type == "considerant":
            parts = c.text.split("\n")
            buf, buf_len, sub = [], 0, 0
            for p in parts:
                if buf_len + len(p) > MAX_CHUNK and buf:
                    sc = JurisprudenceChunk(**{**asdict(c),
                        "chunk_id": f"{c.chunk_id}_s{sub}",
                        "text": "\n".join(buf),
                        "chunk_index": c.chunk_index * 100 + sub})
                    final.append(sc)
                    buf, buf_len, sub = [p], len(p), sub + 1
                else:
                    buf.append(p); buf_len += len(p)
            if buf:
                sc = JurisprudenceChunk(**{**asdict(c),
                    "chunk_id": f"{c.chunk_id}_s{sub}",
                    "text": "\n".join(buf),
                    "chunk_index": c.chunk_index * 100 + sub})
                final.append(sc)
        else:
            final.append(c)
    return final

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def scrape(hierarchy="CH_BGE_999", lang="fr", limit=None, output_dir=Path("data/jurisprudence"),
           download=True, batch_size=500):
    log.info(f"Scraping: hierarchy={hierarchy}, lang={lang}, limit={limit}")
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = {"decisions": 0, "chunks": 0, "downloaded": 0, "failed": 0, "batches": 0}
    batch_d, batch_c, batch_n = [], [], 0

    for hit in iterate_decisions(hierarchy, lang, limit):
        dec = _hit_to_decision(hit)
        stats["decisions"] += 1

        if download and dec.content_url:
            time.sleep(REQUEST_DELAY)
            for attempt in range(MAX_RETRIES):
                try:
                    r = requests.get(dec.content_url, timeout=REQUEST_TIMEOUT)
                    r.raise_for_status()
                    cks = parse_decision_html(r.content, dec)
                    stats["downloaded"] += 1
                    stats["chunks"] += len(cks)
                    batch_c.extend([asdict(c) for c in cks])
                    break
                except Exception:
                    if attempt == MAX_RETRIES - 1:
                        stats["failed"] += 1
                    else:
                        time.sleep(1)
        else:
            batch_c.append(asdict(JurisprudenceChunk(
                chunk_id=f"{dec.id}__meta_0", chunk_type="metadata_only",
                text=dec.abstract_fr or dec.title_fr,
                decision_id=dec.id, decision_ref=dec.reference[0] if dec.reference else "",
                decision_date=dec.date, abstract_fr=dec.abstract_fr,
                language=dec.language, court=dec.court,
                chamber_name=dec.chamber_name, legal_domain=dec.legal_domain,
                is_atf=dec.is_atf, source_url=dec.content_url,
            )))
            stats["chunks"] += 1

        batch_d.append(asdict(dec))

        if len(batch_d) >= batch_size:
            batch_n += 1
            fn = output_dir / f"{hierarchy.lower()}_{lang}_batch_{batch_n:04d}.json"
            with open(fn, "w", encoding="utf-8") as f:
                json.dump({"decisions": batch_d, "chunks": batch_c,
                           "stats": {"batch": batch_n, "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")}},
                          f, ensure_ascii=False, indent=2)
            log.info(f"  Batch {batch_n}: {len(batch_d)} decisions, {len(batch_c)} chunks → {fn.name}")
            stats["batches"] += 1
            batch_d, batch_c = [], []

        if stats["decisions"] % 50 == 0:
            log.info(f"  Progress: {stats['decisions']} decisions")

    if batch_d:
        batch_n += 1
        fn = output_dir / f"{hierarchy.lower()}_{lang}_batch_{batch_n:04d}.json"
        with open(fn, "w", encoding="utf-8") as f:
            json.dump({"decisions": batch_d, "chunks": batch_c,
                       "stats": {"batch": batch_n, "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")}},
                      f, ensure_ascii=False, indent=2)
        stats["batches"] += 1

    return stats

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Entscheidsuche Scraper — Jurisprudence TF")
    parser.add_argument("--mode", choices=["count", "atf", "bger"], default="atf")
    parser.add_argument("--lang", default="fr")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", default="data/jurisprudence")
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    if args.mode == "count":
        print("\n=== Statistiques Entscheidsuche.ch ===\n")
        for h, name in [("CH_BGE_999", "ATF publiés"), ("CH_BGer", "Tous BGer"),
                         ("CH_BVGE", "TAF"), ("CH_BSTG", "TPF")]:
            for lang in ["fr", "de", "it"]:
                n = count_decisions(h, lang)
                print(f"  {name} ({lang}): {n:>8}")
    elif args.mode == "atf":
        stats = scrape("CH_BGE_999", args.lang, args.limit, Path(args.output),
                       not args.no_download, args.batch_size)
        print(f"\n=== ATF scrapés ===")
        print(f"Arrêts: {stats['decisions']} | Chunks: {stats['chunks']} | "
              f"DL: {stats['downloaded']} | Échecs: {stats['failed']}")
    elif args.mode == "bger":
        stats = scrape("CH_BGer", args.lang, args.limit, Path(args.output),
                       not args.no_download, args.batch_size)
        print(f"\n=== BGer scrapés ===")
        print(f"Arrêts: {stats['decisions']} | Chunks: {stats['chunks']}")

if __name__ == "__main__":
    main()
