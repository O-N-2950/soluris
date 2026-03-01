"""
Cantonal Tax Law Scraper — Lois fiscales cantonales suisses (26 cantons)
========================================================================
Scrappe les lois fiscales cantonales pour alimenter la base Soluris.
Utilisé notamment par tAIx (juraitax) via l'endpoint /api/fiscal-query.

Pipeline :
  1. Pour chaque canton → URL officielle → téléchargement HTML (ou PDF)
  2. Parsing par article/alinéa → chunks
  3. Export JSON → insertion PostgreSQL (legal_documents + legal_chunks)

Usage :
  python -m backend.scrapers.cantonal_tax --canton GE
  python -m backend.scrapers.cantonal_tax --canton all
  python -m backend.scrapers.cantonal_tax --mode list
"""

import argparse
import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REQUEST_DELAY = 1.0        # politesse envers les serveurs cantonaux
REQUEST_TIMEOUT = 60
MAX_CHUNK_CHARS = 2000
MIN_CHUNK_CHARS = 50
OUTPUT_DIR = Path("data/cantonal_tax")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("cantonal_tax")

# ---------------------------------------------------------------------------
# Catalogue des 26 cantons — lois fiscales principales
# Note : certains cantons n'ont pas d'URL directement parsable (Angular SPA,
#        PDF non-accessible, auth requise). Dans ce cas, scrape_mode="manual".
# ---------------------------------------------------------------------------

CANTONAL_TAX_LAWS: dict[str, dict] = {
    # ── SUISSE ROMANDE ──────────────────────────────────────────────────────
    "JU": {
        "name": "Loi fiscale du Canton du Jura (LFisc)",
        "rs_cantonal": "641.11",
        "url": "https://rsju.jura.ch/en/viewdocument.html?idn=28021",
        "lang": "fr",
        "jurisdiction": "JU",
        "scrape_mode": "html",
        "selector": "div.article, div.law-text, .rs-text",
    },
    "NE": {
        "name": "Loi sur les contributions directes (LCdir) — Neuchâtel",
        "rs_cantonal": "231.0",
        "url": "https://www.rsne.ch/rsne/10011/231.0.html",
        "lang": "fr",
        "jurisdiction": "NE",
        "scrape_mode": "html",
        "selector": "div.article, article, .law-body",
    },
    "FR": {
        "name": "Loi sur les impôts cantonaux directs (LICD) — Fribourg",
        "rs_cantonal": "631.1",
        "url": "https://bdlf.fr.ch/app/fr/texts_of_law/631.1",
        "lang": "fr",
        "jurisdiction": "FR",
        "scrape_mode": "html",
        "selector": "div.article, .law-article",
    },
    "VD": {
        "name": "Loi sur les impôts directs cantonaux (LIDC) — Vaud",
        "rs_cantonal": "642.11",
        "url": "https://www.rsv.vd.ch/rsvsite/rsv_site/fr/CHtml01/page29A.xsl-5_INPUT0-642.11.html",
        "lang": "fr",
        "jurisdiction": "VD",
        "scrape_mode": "html",
        "selector": "div.text-body, .article-text, div.art",
    },
    "GE": {
        "name": "Loi sur l'imposition des personnes physiques (LIPP) — Genève",
        "rs_cantonal": "D 3 08",
        "url": "https://www.ge.ch/legislation/rsg/f/s/rsg_D3_08.html",
        "lang": "fr",
        "jurisdiction": "GE",
        "scrape_mode": "html",
        "selector": "div.law-text, .legis-article, td.article",
    },
    "VS": {
        "name": "Loi fiscale (LF) — Valais",
        "rs_cantonal": "642.1",
        "url": "https://lex.vs.ch/app/fr/texts_of_law/642.1",
        "lang": "fr",
        "jurisdiction": "VS",
        "scrape_mode": "html",
        "selector": "div.article, .law-article",
    },
    # ── TESSIN ───────────────────────────────────────────────────────────────
    "TI": {
        "name": "Legge tributaria cantonale (LT) — Ticino",
        "rs_cantonal": "629",
        "url": "https://m3.ti.ch/CAN/RLeggi/public/index.php/raccolta-leggi/legge/num/629",
        "lang": "it",
        "jurisdiction": "TI",
        "scrape_mode": "html",
        "selector": "div.testo-legge, .article",
    },
    # ── SUISSE ALÉMANIQUE ────────────────────────────────────────────────────
    "BE": {
        "name": "Steuergesetz (StG) — Bern",
        "rs_cantonal": "661.11",
        "url": "https://www.belex.sites.be.ch/app/de/texts_of_law/661.11",
        "lang": "de",
        "jurisdiction": "BE",
        "scrape_mode": "html",
        "selector": "div.article, .law-article",
    },
    "ZH": {
        "name": "Steuergesetz (StG) — Zürich",
        "rs_cantonal": "631.1",
        "url": "https://www.zhlex.zh.ch/Erlass.html?Open&Ordnr=631.1",
        "lang": "de",
        "jurisdiction": "ZH",
        "scrape_mode": "html",
        "selector": "div.gesetzestext, .paragraph",
    },
    "BS": {
        "name": "Steuergesetz — Basel-Stadt",
        "rs_cantonal": "640.100",
        "url": "https://www.gesetzessammlung.bs.ch/app/de/texts_of_law/640.100",
        "lang": "de",
        "jurisdiction": "BS",
        "scrape_mode": "html",
        "selector": "div.article, .law-article",
    },
    "BL": {
        "name": "Steuer- und Finanzgesetz (StFG) — Basel-Landschaft",
        "rs_cantonal": "331",
        "url": "https://bl.clex.ch/app/de/texts_of_law/331",
        "lang": "de",
        "jurisdiction": "BL",
        "scrape_mode": "html",
        "selector": "div.article, .law-article",
    },
    "SO": {
        "name": "Steuergesetz — Solothurn",
        "rs_cantonal": "614.11",
        "url": "https://bgs.so.ch/app/de/texts_of_law/614.11",
        "lang": "de",
        "jurisdiction": "SO",
        "scrape_mode": "html",
        "selector": "div.article, .law-article",
    },
    "AG": {
        "name": "Steuergesetz (StG) — Aargau",
        "rs_cantonal": "651",
        "url": "https://gesetzessammlungen.ag.ch/app/de/texts_of_law/651",
        "lang": "de",
        "jurisdiction": "AG",
        "scrape_mode": "html",
        "selector": "div.article, .law-article",
    },
    "LU": {
        "name": "Steuergesetz — Luzern",
        "rs_cantonal": "620",
        "url": "https://srl.lu.ch/app/de/texts_of_law/620",
        "lang": "de",
        "jurisdiction": "LU",
        "scrape_mode": "html",
        "selector": "div.article, .law-article",
    },
    "SZ": {
        "name": "Steuergesetz — Schwyz",
        "rs_cantonal": "621.110",
        "url": "https://www.sz.ch/public/upload/assets/40843/621.110.pdf",
        "lang": "de",
        "jurisdiction": "SZ",
        "scrape_mode": "pdf",
    },
    "ZG": {
        "name": "Steuergesetz — Zug",
        "rs_cantonal": "632.1",
        "url": "https://bgs.zg.ch/app/de/texts_of_law/632.1",
        "lang": "de",
        "jurisdiction": "ZG",
        "scrape_mode": "html",
        "selector": "div.article, .law-article",
    },
    "SG": {
        "name": "Steuergesetz — St. Gallen",
        "rs_cantonal": "811.1",
        "url": "https://www.gesetzessammlung.sg.ch/app/de/texts_of_law/811.1",
        "lang": "de",
        "jurisdiction": "SG",
        "scrape_mode": "html",
        "selector": "div.article, .law-article",
    },
    "TG": {
        "name": "Gesetz über die Staats- und Gemeindesteuern (StG) — Thurgau",
        "rs_cantonal": "640",
        "url": "https://www.rechtsbuch.tg.ch/app/de/texts_of_law/640",
        "lang": "de",
        "jurisdiction": "TG",
        "scrape_mode": "html",
        "selector": "div.article, .law-article",
    },
    "GR": {
        "name": "Steuergesetz — Graubünden",
        "rs_cantonal": "720.200",
        "url": "https://www.gr-lex.gr.ch/app/de/texts_of_law/720.200",
        "lang": "de",
        "jurisdiction": "GR",
        "scrape_mode": "html",
        "selector": "div.article, .law-article",
    },
    "GL": {
        "name": "Steuergesetz — Glarus",
        "rs_cantonal": "613.1",
        "url": "https://gesetze.gl.ch/app/de/texts_of_law/613.1",
        "lang": "de",
        "jurisdiction": "GL",
        "scrape_mode": "html",
        "selector": "div.article, .law-article",
    },
    "SH": {
        "name": "Steuergesetz — Schaffhausen",
        "rs_cantonal": "641.100",
        # SH ne publie pas de HTML direct ; utiliser l'URL de base pour
        # récupérer un PDF ou un lien vers le texte intégral
        "url": "https://www.sh.ch/CMS/Webseite/Kanton-Schaffhausen/Beh-rde/Verwaltung/Finanzdepartement/Steuerverwaltung-3540788-DE.html",
        "lang": "de",
        "jurisdiction": "SH",
        "scrape_mode": "manual",  # lien vers PDF, nécessite exploration manuelle
    },
    "NW": {
        "name": "Steuergesetz — Nidwalden",
        "rs_cantonal": "521.1",
        "url": "https://www.nw.ch/steueramt/686",
        "lang": "de",
        "jurisdiction": "NW",
        "scrape_mode": "manual",
    },
    "OW": {
        "name": "Steuergesetz — Obwalden",
        "rs_cantonal": "641.4",
        "url": "https://www.ow.ch/de/verwaltung/finanzdepartement/kantonale-steuerverwaltung/",
        "lang": "de",
        "jurisdiction": "OW",
        "scrape_mode": "manual",
    },
    "UR": {
        "name": "Steuergesetz — Uri",
        "rs_cantonal": "631",
        "url": "https://www.ur.ch/justiz-und-sicherheit/steuern",
        "lang": "de",
        "jurisdiction": "UR",
        "scrape_mode": "manual",
    },
    "AI": {
        "name": "Steuergesetz — Appenzell Innerrhoden",
        "rs_cantonal": "621.100",
        "url": "https://www.ai.ch/themen/finanzen-steuern-und-versicherungen/steuern",
        "lang": "de",
        "jurisdiction": "AI",
        "scrape_mode": "manual",
    },
    "AR": {
        "name": "Steuergesetz — Appenzell Ausserrhoden",
        "rs_cantonal": "621",
        "url": "https://www.ar.ch/verwaltung/departement-finanzen/kantonales-steueramt/",
        "lang": "de",
        "jurisdiction": "AR",
        "scrape_mode": "manual",
    },
}

# ---------------------------------------------------------------------------
# Circulaires AFC (Administration fédérale des contributions)
# ---------------------------------------------------------------------------

AFC_CIRCULAIRES = [
    {
        "id": "afc_ks1",
        "title": "Circulaire AFC n°1 — Déductions des frais professionnels",
        "url": "https://www.estv.admin.ch/dam/estv/fr/dokumente/dbst/kreisschreiben/2016/1-025-D-2016-f.pdf",
        "lang": "fr",
        "legal_domain": "droit_fiscal",
    },
    {
        "id": "afc_ks8",
        "title": "Circulaire AFC n°8 — Prévoyance professionnelle et IFD",
        "url": "https://www.estv.admin.ch/dam/estv/fr/dokumente/dbst/kreisschreiben/2016/8-025-D-2016-f.pdf",
        "lang": "fr",
        "legal_domain": "droit_fiscal",
    },
    {
        "id": "afc_ks18",
        "title": "Circulaire AFC n°18 — Pilier 3a (OPP3) — montants déductibles",
        "url": "https://www.estv.admin.ch/dam/estv/fr/dokumente/dbst/kreisschreiben/2016/18-025-D-2016-f.pdf",
        "lang": "fr",
        "legal_domain": "droit_fiscal",
    },
    {
        "id": "afc_ks25",
        "title": "Circulaire AFC n°25 — Imposition à la source",
        "url": "https://www.estv.admin.ch/dam/estv/fr/dokumente/dbst/kreisschreiben/2016/25-025-D-2016-f.pdf",
        "lang": "fr",
        "legal_domain": "droit_fiscal",
    },
    {
        "id": "afc_ks31",
        "title": "Circulaire AFC n°31 — Sociétés de personnes — Impôt sur le revenu et la fortune",
        "url": "https://www.estv.admin.ch/dam/estv/fr/dokumente/dbst/kreisschreiben/2016/31-025-D-2016-f.pdf",
        "lang": "fr",
        "legal_domain": "droit_fiscal",
    },
]

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class CantonalChunk:
    """Un chunk de droit fiscal cantonal (article ou alinéa)."""
    article_id: str          # e.g. "ge_lipp_art_10"
    article_number: str      # e.g. "Art. 10"
    text: str
    section_path: list = field(default_factory=list)
    jurisdiction: str = ""   # e.g. "GE"
    law_name: str = ""
    rs_cantonal: str = ""
    source_url: str = ""
    language: str = "fr"
    doc_type: str = "legislation_cantonale"
    legal_domain: str = "droit_fiscal"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": "Soluris Legal Research Bot/1.0 (https://soluris.ch; contact@soluris.ch)",
    "Accept-Language": "fr-CH,fr;q=0.9,de-CH;q=0.8",
}


def fetch_html(url: str) -> Optional[str]:
    """Télécharge une page HTML avec retry."""
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except requests.RequestException as e:
            log.warning(f"Attempt {attempt+1}/3 failed for {url}: {e}")
            time.sleep(2 ** attempt)
    return None


def fetch_pdf_text(url: str) -> Optional[str]:
    """Télécharge un PDF et extrait le texte avec pdfplumber."""
    try:
        import io
        import pdfplumber
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        return text
    except Exception as e:
        log.warning(f"PDF extraction failed for {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_html_generic(html: str, selector: str, law_meta: dict) -> list[CantonalChunk]:
    """
    Parser générique HTML : essaie plusieurs sélecteurs CSS courants
    sur les portails de législation cantonaux suisses.
    Retourne une liste de chunks par article.
    """
    soup = BeautifulSoup(html, "html.parser")
    chunks: list[CantonalChunk] = []
    canton = law_meta["jurisdiction"]
    law_name = law_meta["name"]
    rs = law_meta.get("rs_cantonal", "")
    lang = law_meta.get("lang", "fr")
    url = law_meta.get("url", "")

    # Sélecteurs à essayer dans l'ordre
    selectors = [s.strip() for s in selector.split(",")]
    selectors += [
        "div[id^='art']", "div[class*='article']", "p[id^='art']",
        "section", "article", ".legis-text p", "td.article",
    ]

    elements = []
    for sel in selectors:
        try:
            found = soup.select(sel)
            if found:
                elements = found
                log.info(f"[{canton}] Found {len(found)} elements with selector '{sel}'")
                break
        except Exception:
            continue

    if not elements:
        # Fallback : prendre tout le body et chunker par paragraphe
        log.warning(f"[{canton}] No structured elements found, falling back to paragraph split")
        body = soup.find("body")
        if body:
            raw_text = body.get_text(separator="\n", strip=True)
            chunks = _chunk_raw_text(raw_text, canton, law_name, rs, url, lang)
        return chunks

    for i, el in enumerate(elements):
        text = el.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        if len(text) < MIN_CHUNK_CHARS:
            continue

        # Tenter d'extraire le numéro d'article
        art_match = re.search(r"Art\.?\s*(\d+[a-z]?)", text)
        art_number = f"Art. {art_match.group(1)}" if art_match else f"§{i+1}"
        art_id = f"{canton.lower()}_{re.sub(r'[^a-z0-9]', '', law_name.lower()[:15])}_art_{i+1}"

        # Chunker si texte trop long
        sub_chunks = _split_text(text, MAX_CHUNK_CHARS)
        for j, chunk_text in enumerate(sub_chunks):
            chunks.append(CantonalChunk(
                article_id=f"{art_id}_{j}" if j > 0 else art_id,
                article_number=art_number,
                text=chunk_text,
                jurisdiction=canton,
                law_name=law_name,
                rs_cantonal=rs,
                source_url=url,
                language=lang,
            ))

    log.info(f"[{canton}] Parsed {len(chunks)} chunks from {law_name}")
    return chunks


def parse_pdf_text(raw_text: str, law_meta: dict) -> list[CantonalChunk]:
    """
    Chunke un texte PDF brut en articles.
    Cherche des patterns "Art. X" ou "§ X" comme délimiteurs.
    """
    canton = law_meta["jurisdiction"]
    law_name = law_meta["name"]
    rs = law_meta.get("rs_cantonal", "")
    lang = law_meta.get("lang", "de")
    url = law_meta.get("url", "")

    # Découper par "Art." ou "§"
    articles = re.split(r"(?=\b(?:Art\.|§)\s*\d+)", raw_text)
    chunks: list[CantonalChunk] = []

    for i, art_text in enumerate(articles):
        art_text = art_text.strip()
        if len(art_text) < MIN_CHUNK_CHARS:
            continue
        art_match = re.match(r"(Art\.|§)\s*(\d+[a-z]?)", art_text)
        art_number = f"Art. {art_match.group(2)}" if art_match else f"§{i+1}"
        art_id = f"{canton.lower()}_stg_art_{i+1}"

        sub_chunks = _split_text(art_text, MAX_CHUNK_CHARS)
        for j, chunk_text in enumerate(sub_chunks):
            chunks.append(CantonalChunk(
                article_id=f"{art_id}_{j}" if j > 0 else art_id,
                article_number=art_number,
                text=chunk_text,
                jurisdiction=canton,
                law_name=law_name,
                rs_cantonal=rs,
                source_url=url,
                language=lang,
            ))

    log.info(f"[{canton}] Parsed {len(chunks)} chunks from PDF")
    return chunks


def _chunk_raw_text(text: str, canton: str, law_name: str, rs: str, url: str, lang: str) -> list[CantonalChunk]:
    """Fallback : chunker du texte brut par paragraphes."""
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if len(p.strip()) > MIN_CHUNK_CHARS]
    chunks = []
    for i, para in enumerate(paragraphs):
        sub = _split_text(para, MAX_CHUNK_CHARS)
        for j, chunk_text in enumerate(sub):
            chunks.append(CantonalChunk(
                article_id=f"{canton.lower()}_raw_{i}_{j}",
                article_number=f"§{i+1}",
                text=chunk_text,
                jurisdiction=canton,
                law_name=law_name,
                rs_cantonal=rs,
                source_url=url,
                language=lang,
            ))
    return chunks


def _split_text(text: str, max_chars: int) -> list[str]:
    """Découpe un texte en sous-chunks de max_chars caractères, sur des frontières de phrases."""
    if len(text) <= max_chars:
        return [text]
    chunks = []
    while len(text) > max_chars:
        split_pos = text.rfind(". ", 0, max_chars)
        if split_pos == -1:
            split_pos = max_chars
        chunks.append(text[:split_pos + 1].strip())
        text = text[split_pos + 1:].strip()
    if text:
        chunks.append(text)
    return chunks


# ---------------------------------------------------------------------------
# Scraper principal
# ---------------------------------------------------------------------------

def scrape_canton(canton_code: str) -> list[CantonalChunk]:
    """Scrappe la loi fiscale d'un canton et retourne les chunks."""
    meta = CANTONAL_TAX_LAWS.get(canton_code.upper())
    if not meta:
        log.error(f"Canton {canton_code} not found in catalogue")
        return []

    mode = meta.get("scrape_mode", "html")
    url = meta["url"]
    log.info(f"Scraping [{canton_code}] {meta['name']} — mode={mode}")

    if mode == "manual":
        log.warning(f"[{canton_code}] Mode manual — URL needs exploration: {url}")
        return []

    if mode == "pdf":
        raw = fetch_pdf_text(url)
        if not raw:
            return []
        return parse_pdf_text(raw, meta)

    # HTML
    html = fetch_html(url)
    if not html:
        log.error(f"[{canton_code}] Failed to fetch HTML from {url}")
        return []

    selector = meta.get("selector", "div.article")
    return parse_html_generic(html, selector, meta)


def scrape_all_cantons(cantons: Optional[list[str]] = None) -> dict[str, list[CantonalChunk]]:
    """Scrappe tous les cantons (ou une liste filtrée)."""
    target = cantons if cantons else list(CANTONAL_TAX_LAWS.keys())
    results = {}
    for canton in target:
        chunks = scrape_canton(canton)
        results[canton] = chunks
        if chunks:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            out_file = OUTPUT_DIR / f"{canton.lower()}_tax.json"
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump([asdict(c) for c in chunks], f, ensure_ascii=False, indent=2)
            log.info(f"[{canton}] Saved {len(chunks)} chunks → {out_file}")
        time.sleep(REQUEST_DELAY)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Cantonal Tax Law Scraper — Soluris")
    parser.add_argument("--canton", default="all",
                        help="Code canton (GE, VD, ...) ou 'all'")
    parser.add_argument("--mode", default="scrape",
                        choices=["scrape", "list"],
                        help="Mode d'exécution")
    args = parser.parse_args()

    if args.mode == "list":
        print("\n== Catalogue des lois fiscales cantonales ==\n")
        for code, meta in CANTONAL_TAX_LAWS.items():
            mode = meta.get("scrape_mode", "html")
            flag = "✅" if mode in ("html", "pdf") else "⚠️ manual"
            print(f"  [{code}] {meta['name'][:60]:<62} {flag}")
        print(f"\nTotal : {len(CANTONAL_TAX_LAWS)} cantons")
        return

    if args.canton.upper() == "ALL":
        scrape_all_cantons()
    else:
        cantons = [c.strip().upper() for c in args.canton.split(",")]
        scrape_all_cantons(cantons)


if __name__ == "__main__":
    main()
