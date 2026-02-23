"""
Fedlex SPARQL Scraper — Ingestion du Recueil Systématique (RS)
==============================================================
Interroge le endpoint SPARQL de Fedlex (fedlex.data.admin.ch) pour extraire
la législation fédérale suisse consolidée.

Pipeline:
  1. SPARQL → liste des actes en vigueur avec métadonnées
  2. Pour chaque acte → dernière consolidation → URL HTML
  3. Téléchargement HTML → parsing par article (BeautifulSoup)
  4. Export JSON prêt pour insertion en base (legal_documents + legal_chunks)

Usage:
  python -m backend.scrapers.fedlex --mode list       # Lister les actes
  python -m backend.scrapers.fedlex --mode scrape      # Scraper tous les actes
  python -m backend.scrapers.fedlex --mode scrape --rs 220  # Scraper un acte (CO)
  python -m backend.scrapers.fedlex --mode priority    # Scraper les codes prioritaires
"""

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup, Tag
from SPARQLWrapper import SPARQLWrapper, JSON

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPARQL_ENDPOINT = "https://fedlex.data.admin.ch/sparqlendpoint"
FEDLEX_LANG_URI = "http://publications.europa.eu/resource/authority/language/FRA"
REQUEST_DELAY = 0.5  # seconds between HTTP requests (rate limiting)
REQUEST_TIMEOUT = 60  # seconds

# Codes prioritaires pour la Phase 1 (ordre d'importance pour un avocat suisse)
PRIORITY_RS = [
    "220",      # CO  — Code des obligations
    "210",      # CC  — Code civil
    "311.0",    # CP  — Code pénal
    "272",      # CPC — Code de procédure civile
    "312.0",    # CPP — Code de procédure pénale
    "281.1",    # LP  — Loi sur la poursuite pour dettes et la faillite
    "173.110",  # LTF — Loi sur le Tribunal fédéral
    "291",      # LDIP — Loi sur le droit international privé
    "700",      # LAT — Loi sur l'aménagement du territoire
    "142.20",   # LEI — Loi sur les étrangers et l'intégration
    "101",      # Cst — Constitution fédérale
    "221.301",  # LACI — Loi sur l'assurance-chômage (sic, vérifier)
    "830.1",    # LPGA — Loi sur la partie générale du droit des assurances sociales
    "832.10",   # LAMal — Loi sur l'assurance-maladie
    "831.10",   # LAVS — Loi sur l'AVS
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fedlex")

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class FedlexAct:
    """Métadonnées d'un acte législatif du Recueil Systématique."""
    uri: str                         # e.g. https://fedlex.data.admin.ch/eli/cc/27/317_321_377
    rs_number: str                   # e.g. "220"
    title: str                       # Titre complet en français
    title_short: str = ""            # Abréviation (CO, CC, CP, etc.)
    in_force: bool = True            # En vigueur ?
    latest_consolidation_uri: str = ""
    latest_consolidation_date: str = ""
    html_download_url: str = ""

@dataclass
class LegalChunk:
    """Un chunk juridique (= 1 article ou groupe d'alinéas cohérent)."""
    article_id: str                  # e.g. "art_1"
    article_number: str              # e.g. "Art. 1"
    text: str                        # Contenu textuel de l'article
    section_path: list = field(default_factory=list)  # Hiérarchie (Partie > Titre > Chapitre)
    rs_number: str = ""
    act_uri: str = ""
    act_title: str = ""
    act_short: str = ""
    fedlex_url: str = ""             # URL vers Fedlex pour cet article
    language: str = "fr"
    doc_type: str = "legislation"

# ---------------------------------------------------------------------------
# SPARQL queries
# ---------------------------------------------------------------------------

def _sparql_query(query: str) -> list[dict]:
    """Exécute une requête SPARQL sur le endpoint Fedlex."""
    sparql = SPARQLWrapper(SPARQL_ENDPOINT)
    sparql.setQuery(query)
    sparql.setReturnFormat(JSON)
    sparql.setTimeout(REQUEST_TIMEOUT)
    try:
        results = sparql.query().convert()
        return results["results"]["bindings"]
    except Exception as e:
        log.error(f"SPARQL query failed: {e}")
        return []


def list_all_acts(in_force_only: bool = True) -> list[FedlexAct]:
    """Liste tous les actes du RS avec métadonnées (SPARQL)."""
    force_filter = ""
    if in_force_only:
        # Status 0 = en vigueur, 1 = partiellement en vigueur
        force_filter = """
        FILTER(?inForceStatus IN (
            <https://fedlex.data.admin.ch/vocabulary/enforcement-status/0>,
            <https://fedlex.data.admin.ch/vocabulary/enforcement-status/1>
        ))
        """

    query = f"""
    PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
    
    SELECT DISTINCT ?ca ?rsId ?title ?titleShort ?inForceStatus WHERE {{
        ?ca a jolux:ConsolidationAbstract ;
            jolux:isRealizedBy ?expr ;
            jolux:inForceStatus ?inForceStatus .
        ?expr jolux:language <{FEDLEX_LANG_URI}> ;
              jolux:historicalLegalId ?rsId ;
              jolux:title ?title .
        OPTIONAL {{ ?expr jolux:titleShort ?titleShort . }}
        FILTER(STRSTARTS(STR(?ca), "https://fedlex.data.admin.ch/eli/cc/"))
        {force_filter}
    }}
    ORDER BY ?rsId
    """
    
    log.info("Fetching act list from Fedlex SPARQL...")
    bindings = _sparql_query(query)
    
    acts = []
    seen_uris = set()
    for b in bindings:
        uri = b["ca"]["value"]
        if uri in seen_uris:
            continue
        seen_uris.add(uri)
        
        status = b["inForceStatus"]["value"].split("/")[-1]
        acts.append(FedlexAct(
            uri=uri,
            rs_number=b["rsId"]["value"],
            title=_clean_html(b["title"]["value"]),
            title_short=b.get("titleShort", {}).get("value", ""),
            in_force=(status in ("0", "1")),
        ))
    
    log.info(f"Found {len(acts)} acts in the RS" + (" (in force)" if in_force_only else ""))
    return acts


def get_latest_consolidation(act_uri: str) -> tuple[str, str]:
    """Récupère la dernière consolidation (version) d'un acte, en excluant les futures."""
    today = time.strftime("%Y-%m-%d")
    query = f"""
    PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
    PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
    
    SELECT ?consolidation ?dateAppl WHERE {{
        ?consolidation jolux:isMemberOf <{act_uri}> ;
                       jolux:dateApplicability ?dateAppl .
        FILTER(?dateAppl <= "{today}"^^xsd:date)
    }}
    ORDER BY DESC(?dateAppl)
    LIMIT 1
    """
    bindings = _sparql_query(query)
    if not bindings:
        return "", ""
    return bindings[0]["consolidation"]["value"], bindings[0]["dateAppl"]["value"]


def get_html_download_url(consolidation_uri: str) -> str:
    """Récupère l'URL de téléchargement HTML pour une consolidation."""
    query = f"""
    PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
    
    SELECT ?url WHERE {{
        <{consolidation_uri}> jolux:isRealizedBy ?expr .
        ?expr jolux:language <{FEDLEX_LANG_URI}> ;
              jolux:isEmbodiedBy ?manif .
        ?manif jolux:userFormat <https://fedlex.data.admin.ch/vocabulary/user-format/html> ;
               jolux:isExemplifiedBy ?url .
    }}
    LIMIT 1
    """
    bindings = _sparql_query(query)
    if not bindings:
        return ""
    return bindings[0]["url"]["value"]


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

def _clean_html(text: str) -> str:
    """Supprime les balises HTML résiduelles et normalise les espaces."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _get_section_path(element: Tag) -> list[str]:
    """Remonte la hiérarchie des sections parentes d'un article."""
    path = []
    parent = element.parent
    while parent:
        if parent.name == "section":
            heading = parent.find(
                ["h1", "h2", "h3", "h4", "h5", "h6", "div"],
                class_="heading",
                recursive=False,
            )
            if heading:
                text = heading.get_text(strip=True)
                # Nettoyer les icônes display/external-link
                text = re.sub(r"^\s*$", "", text).strip()
                if text:
                    path.insert(0, text)
        parent = parent.parent
    return path


def parse_html_articles(html_content: bytes, act: FedlexAct) -> list[LegalChunk]:
    """Parse le HTML Fedlex et extrait les articles individuels."""
    soup = BeautifulSoup(html_content, "html.parser")
    articles = soup.find_all("article")
    
    chunks = []
    for article_tag in articles:
        art_id = article_tag.get("id", "")
        
        # Extraire le numéro d'article
        heading = article_tag.find(["h6", "h5", "h4", "h3"])
        art_number = ""
        if heading:
            art_number = _clean_html(heading.get_text(strip=True))
        
        # Extraire le texte complet de l'article
        paragraphs = article_tag.find_all("p")
        text_parts = []
        for p in paragraphs:
            # Skip footnotes
            if p.get("id", "").startswith("fn-"):
                continue
            txt = p.get_text(" ", strip=True)
            if txt:
                text_parts.append(txt)
        
        full_text = "\n".join(text_parts)
        if not full_text.strip():
            continue
        
        # Hiérarchie des sections
        section_path = _get_section_path(article_tag)
        
        # URL Fedlex pour cet article
        # Format: https://www.fedlex.admin.ch/eli/cc/27/317_321_377/fr#{art_id}
        eli_path = act.uri.replace("https://fedlex.data.admin.ch", "")
        fedlex_url = f"https://www.fedlex.admin.ch{eli_path}/fr#{art_id}"
        
        chunks.append(LegalChunk(
            article_id=art_id,
            article_number=art_number,
            text=full_text,
            section_path=section_path,
            rs_number=act.rs_number,
            act_uri=act.uri,
            act_title=act.title,
            act_short=act.title_short,
            fedlex_url=fedlex_url,
            language="fr",
            doc_type="legislation",
        ))
    
    return chunks


# ---------------------------------------------------------------------------
# Scraping pipeline
# ---------------------------------------------------------------------------

def scrape_act(act: FedlexAct) -> list[LegalChunk]:
    """Pipeline complet pour un acte : métadonnées → HTML → chunks."""
    log.info(f"Scraping RS {act.rs_number} ({act.title_short or '?'}) — {act.title[:60]}...")
    
    # 1. Get latest consolidation
    cons_uri, cons_date = get_latest_consolidation(act.uri)
    if not cons_uri:
        log.warning(f"  No consolidation found for {act.uri}")
        return []
    
    act.latest_consolidation_uri = cons_uri
    act.latest_consolidation_date = cons_date
    log.info(f"  Latest consolidation: {cons_date}")
    
    # 2. Get HTML download URL
    html_url = get_html_download_url(cons_uri)
    if not html_url:
        log.warning(f"  No HTML URL found for {cons_uri}")
        return []
    
    act.html_download_url = html_url
    
    # 3. Download HTML
    time.sleep(REQUEST_DELAY)
    try:
        resp = requests.get(html_url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"  Download failed: {e}")
        return []
    
    log.info(f"  Downloaded {len(resp.content)} bytes")
    
    # 4. Parse articles
    chunks = parse_html_articles(resp.content, act)
    log.info(f"  Parsed {len(chunks)} articles")
    
    return chunks


def scrape_by_rs(rs_numbers: list[str], output_dir: Path) -> dict:
    """Scrape une liste d'actes par numéro RS."""
    all_acts = list_all_acts(in_force_only=False)
    
    # Filtrer par RS demandés
    target_acts = [a for a in all_acts if a.rs_number in rs_numbers and a.in_force]
    
    # Si un RS n'est pas trouvé en vigueur, essayer sans filtre
    found_rs = {a.rs_number for a in target_acts}
    missing_rs = set(rs_numbers) - found_rs
    if missing_rs:
        for a in all_acts:
            if a.rs_number in missing_rs:
                target_acts.append(a)
                missing_rs.discard(a.rs_number)
    
    if missing_rs:
        log.warning(f"RS numbers not found: {missing_rs}")
    
    log.info(f"Scraping {len(target_acts)} acts...")
    
    stats = {"acts_scraped": 0, "total_chunks": 0, "errors": []}
    output_dir.mkdir(parents=True, exist_ok=True)
    
    for act in target_acts:
        chunks = scrape_act(act)
        if chunks:
            # Save act + chunks to JSON
            output_file = output_dir / f"rs_{act.rs_number.replace('.', '_')}.json"
            data = {
                "act": asdict(act),
                "chunks": [asdict(c) for c in chunks],
                "stats": {
                    "total_articles": len(chunks),
                    "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            }
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            stats["acts_scraped"] += 1
            stats["total_chunks"] += len(chunks)
            log.info(f"  → Saved to {output_file}")
        else:
            stats["errors"].append(act.rs_number)
    
    return stats


def scrape_all(output_dir: Path, in_force_only: bool = True) -> dict:
    """Scrape tous les actes du RS."""
    acts = list_all_acts(in_force_only=in_force_only)
    rs_numbers = [a.rs_number for a in acts]
    return scrape_by_rs(rs_numbers, output_dir)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fedlex SPARQL Scraper — Législation fédérale suisse")
    parser.add_argument("--mode", choices=["list", "scrape", "priority"], default="priority",
                        help="list: lister les actes | scrape: tout scraper | priority: codes prioritaires")
    parser.add_argument("--rs", type=str, help="Numéro RS spécifique à scraper (e.g. 220)")
    parser.add_argument("--output", type=str, default="data/fedlex",
                        help="Répertoire de sortie pour les JSON")
    parser.add_argument("--include-abrogated", action="store_true",
                        help="Inclure les actes abrogés")
    args = parser.parse_args()
    
    output_dir = Path(args.output)
    
    if args.mode == "list":
        acts = list_all_acts(in_force_only=not args.include_abrogated)
        print(f"\n{'RS':<15} {'Abrév.':<10} {'Titre':<70}")
        print("-" * 95)
        for a in acts:
            print(f"{a.rs_number:<15} {a.title_short:<10} {a.title[:70]}")
        print(f"\nTotal: {len(acts)} actes")
    
    elif args.mode == "scrape":
        if args.rs:
            rs_list = [r.strip() for r in args.rs.split(",")]
        else:
            rs_list = None
        
        if rs_list:
            stats = scrape_by_rs(rs_list, output_dir)
        else:
            stats = scrape_all(output_dir, in_force_only=not args.include_abrogated)
        
        print(f"\n=== Scraping terminé ===")
        print(f"Actes scrapés : {stats['acts_scraped']}")
        print(f"Total chunks  : {stats['total_chunks']}")
        if stats["errors"]:
            print(f"Erreurs       : {stats['errors']}")
    
    elif args.mode == "priority":
        stats = scrape_by_rs(PRIORITY_RS, output_dir)
        print(f"\n=== Scraping prioritaire terminé ===")
        print(f"Actes scrapés : {stats['acts_scraped']}")
        print(f"Total chunks  : {stats['total_chunks']}")
        if stats["errors"]:
            print(f"Erreurs       : {stats['errors']}")


if __name__ == "__main__":
    main()
