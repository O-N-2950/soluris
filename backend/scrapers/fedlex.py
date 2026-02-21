"""Fedlex SPARQL scraper — Swiss federal legislation"""
import httpx

SPARQL_ENDPOINT = "https://fedlex.data.admin.ch/sparqlendpoint"

QUERY_LAWS_FR = """
PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT ?act ?title ?rs_number ?date WHERE {
  ?act a jolux:ConsolidationAbstract ;
       dcterms:title ?title ;
       jolux:classifiedByTaxonomyEntry ?entry .
  ?entry skos:notation ?rs_number .
  OPTIONAL { ?act dcterms:date ?date }
  FILTER (lang(?title) = "fr")
}
ORDER BY ?rs_number
LIMIT 100
OFFSET %d
"""


async def fetch_federal_laws(offset: int = 0, limit: int = 100) -> list:
    """Fetch Swiss federal laws from Fedlex SPARQL endpoint."""
    query = QUERY_LAWS_FR % offset
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            SPARQL_ENDPOINT,
            params={"query": query},
            headers={"Accept": "application/sparql-results+json"},
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for binding in data.get("results", {}).get("bindings", []):
        results.append({
            "uri": binding.get("act", {}).get("value", ""),
            "title": binding.get("title", {}).get("value", ""),
            "rs_number": binding.get("rs_number", {}).get("value", ""),
            "date": binding.get("date", {}).get("value", ""),
        })
    return results
