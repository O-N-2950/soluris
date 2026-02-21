"""Entscheidsuche scraper — Swiss court decisions Open Data"""
import httpx

BASE_URL = "https://entscheidsuche.ch"
JOBS_URL = f"{BASE_URL}/docs/jobs.json"


async def fetch_latest_decisions(limit: int = 50) -> list:
    """Fetch latest court decisions from Entscheidsuche."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(JOBS_URL)
        resp.raise_for_status()
        jobs = resp.json()

    results = []
    for job in jobs[:limit]:
        results.append({
            "court": job.get("spider", ""),
            "url": job.get("url", ""),
            "count": job.get("count", 0),
            "date": job.get("date", ""),
        })
    return results


async def search_decisions(query: str, canton: str = None, limit: int = 20) -> list:
    """Search court decisions via Entscheidsuche API."""
    params = {"q": query, "rows": limit}
    if canton:
        params["fq"] = f"canton:{canton}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{BASE_URL}/api/search", params=params)
        resp.raise_for_status()
        return resp.json().get("response", {}).get("docs", [])
