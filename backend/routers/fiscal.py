"""
Endpoint fiscal dédié tAIx — /api/fiscal-query
================================================
Route interne (clé API statique) permettant à tAIx (juraitax) d'interroger
la base Soluris en mode RAG fiscal.

Sécurité : clé `internal_key` validée contre variable d'env TAIX_INTERNAL_KEY.
Sans authentification JWT utilisateur — communication service-à-service.

Usage tAIx :
  POST /api/fiscal-query
  {
    "question": "Montant max déductible pilier 3a 2025 ?",
    "canton": "GE",
    "annee": 2025,
    "internal_key": "<TAIX_INTERNAL_KEY>"
  }

Réponse :
  {
    "reponse": "...",
    "sources": [{"reference": "Art. 82 LPP", "titre": "...", "url": "..."}],
    "confidence": 0.92,
    "canton": "GE",
    "domain": "droit_fiscal"
  }
"""

import os
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

log = logging.getLogger("fiscal")

router = APIRouter(prefix="/api", tags=["fiscal"])

# ---------------------------------------------------------------------------
# Auth interne (service-to-service)
# ---------------------------------------------------------------------------

TAIX_INTERNAL_KEY = os.getenv("TAIX_INTERNAL_KEY", "")


def verify_internal_key(internal_key: str) -> None:
    """Vérifie la clé interne tAIx. Lève 403 si invalide."""
    if not TAIX_INTERNAL_KEY:
        raise HTTPException(status_code=503, detail="TAIX_INTERNAL_KEY not configured on server")
    if internal_key != TAIX_INTERNAL_KEY:
        raise HTTPException(status_code=403, detail="Invalid internal_key")


# ---------------------------------------------------------------------------
# Modèles Pydantic
# ---------------------------------------------------------------------------

class FiscalQueryRequest(BaseModel):
    question: str
    canton: Optional[str] = None     # Code canton ISO (GE, VD, JU, ...) ou None = fédéral
    annee: Optional[int] = None      # Année fiscale (ex: 2025)
    internal_key: str
    max_sources: int = 5             # Nombre max de chunks RAG à inclure


class FiscalSource(BaseModel):
    reference: str    # ex: "Art. 82 LPP" ou "Art. 10 LIPP-GE"
    titre: str
    url: str
    jurisdiction: str = "CH"        # CH ou code canton


class FiscalQueryResponse(BaseModel):
    reponse: str
    sources: list[FiscalSource]
    confidence: float               # Score cosinus moyen des chunks retenus
    canton: Optional[str]
    domain: str = "droit_fiscal"
    model: str = "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# System prompt spécialisé fiscal
# ---------------------------------------------------------------------------

FISCAL_SYSTEM_PROMPT = """Tu es Soluris Fiscal, un assistant juridique spécialisé dans le droit fiscal suisse.

RÈGLES ABSOLUES :
1. Base ta réponse UNIQUEMENT sur les sources juridiques fournies dans le contexte.
2. Cite chaque affirmation avec la référence exacte : "Art. X LIFD", "Art. Y LPP", "Art. Z LIPP-GE", etc.
3. Si une information n'est pas dans les sources, dis-le explicitement : "Je n'ai pas trouvé de disposition applicable."
4. Ne jamais inventer des montants ou des taux. Les barèmes fiscaux changent chaque année.
5. Indique toujours l'année de référence si connue.
6. Pour le droit cantonal, précise le canton concerné.

FORMAT DE RÉPONSE :
- Réponse directe et concise (2-4 paragraphes)
- Citations inline : (Art. 82 al. 1 LPP) ou (Art. 10 LIPP-GE)
- Section "Sources utilisées" à la fin avec liste numérotée

Tu réponds en français, même pour des lois allemandes ou italiennes (traduire les extraits pertinents)."""


def build_fiscal_context(chunks: list[dict], canton: Optional[str], annee: Optional[int]) -> str:
    """Construit le contexte RAG pour la requête fiscale."""
    header = "=== SOURCES JURIDIQUES FISCALES ===\n"
    if canton:
        header += f"Canton concerné : {canton}\n"
    if annee:
        header += f"Année fiscale : {annee}\n"
    header += "\n"

    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        meta = chunk.get("metadata", {})
        ref = meta.get("article_number", f"§{i}")
        law = meta.get("law_name") or meta.get("act_title") or meta.get("act_short", "")
        jurisdiction = meta.get("jurisdiction", "CH")
        source_url = meta.get("source_url") or meta.get("fedlex_url", "")
        text = chunk.get("text", "")

        context_parts.append(
            f"[Source {i}] {ref} {law} ({jurisdiction})\n"
            f"URL : {source_url}\n"
            f"{text}\n"
        )

    return header + "\n---\n".join(context_parts)


# ---------------------------------------------------------------------------
# Route principale
# ---------------------------------------------------------------------------

@router.post("/fiscal-query", response_model=FiscalQueryResponse)
async def fiscal_query(req: FiscalQueryRequest):
    """
    Point d'entrée RAG fiscal pour tAIx.
    Authentification par clé interne (pas JWT).
    """
    verify_internal_key(req.internal_key)

    # Import ici pour éviter les imports circulaires
    from backend.db.database import get_pool
    from backend.services.rag import generate_embedding, retrieve_chunks, call_claude

    pool = await get_pool()

    # 1. Enrichir la question avec contexte canton/année
    enriched_question = req.question
    if req.canton:
        enriched_question += f" (Canton: {req.canton})"
    if req.annee:
        enriched_question += f" (Année fiscale: {req.annee})"

    # 2. Embedding de la question
    try:
        query_embedding = await generate_embedding(enriched_question)
    except Exception as e:
        log.error(f"Embedding failed: {e}")
        raise HTTPException(status_code=502, detail="Embedding service unavailable")

    # 3. Recherche vectorielle — filtrer sur droit_fiscal + canton si fourni
    filters = {"legal_domain": "droit_fiscal"}
    if req.canton:
        filters["jurisdiction"] = req.canton.upper()

    try:
        chunks = await retrieve_chunks(
            pool=pool,
            query_embedding=query_embedding,
            limit=req.max_sources,
            filters=filters,
        )
    except Exception as e:
        log.error(f"Vector retrieval failed: {e}")
        # Fallback : recherche sans filtre canton pour avoir au moins des sources fédérales
        try:
            chunks = await retrieve_chunks(
                pool=pool,
                query_embedding=query_embedding,
                limit=req.max_sources,
                filters={"legal_domain": "droit_fiscal"},
            )
        except Exception as e2:
            log.error(f"Fallback retrieval also failed: {e2}")
            raise HTTPException(status_code=502, detail="Vector search unavailable")

    # Score de confiance moyen
    distances = [c.get("distance", 1.0) for c in chunks]
    confidence = round(1 - (sum(distances) / len(distances)), 3) if distances else 0.0

    # Si aucun résultat pertinent (distance > 0.7 = peu pertinent)
    if not chunks or (distances and min(distances) > 0.7):
        return FiscalQueryResponse(
            reponse=(
                "Je n'ai pas trouvé de disposition fiscale directement applicable à votre question "
                "dans ma base de données. Consultez directement le portail Fedlex "
                "(https://www.fedlex.admin.ch) ou le service cantonal des impôts compétent."
            ),
            sources=[],
            confidence=0.0,
            canton=req.canton,
        )

    # 4. Construire le contexte RAG
    context = build_fiscal_context(chunks, req.canton, req.annee)

    # 5. Appel Claude avec prompt spécialisé fiscal
    messages = [
        {
            "role": "user",
            "content": (
                f"{context}\n\n"
                f"=== QUESTION ===\n{req.question}"
            ),
        }
    ]

    try:
        claude_response = await call_claude(
            messages=messages,
            system_prompt=FISCAL_SYSTEM_PROMPT,
            max_tokens=1500,
        )
    except Exception as e:
        log.error(f"Claude API failed: {e}")
        raise HTTPException(status_code=502, detail="AI service unavailable")

    # 6. Parser les sources depuis les chunks
    sources = []
    for chunk in chunks:
        meta = chunk.get("metadata", {})
        art_num = meta.get("article_number", "")
        law = meta.get("law_name") or meta.get("act_short") or meta.get("act_title", "")
        jurisdiction = meta.get("jurisdiction", "CH")
        url = meta.get("source_url") or meta.get("fedlex_url", "")

        reference = f"{art_num} {law}".strip()
        if jurisdiction not in ("CH", ""):
            reference += f" ({jurisdiction})"

        sources.append(FiscalSource(
            reference=reference or "Disposition fiscale",
            titre=law or "Droit fiscal suisse",
            url=url or "https://www.fedlex.admin.ch",
            jurisdiction=jurisdiction,
        ))

    # Déduplication des sources
    seen_refs = set()
    unique_sources = []
    for s in sources:
        if s.reference not in seen_refs:
            seen_refs.add(s.reference)
            unique_sources.append(s)

    return FiscalQueryResponse(
        reponse=claude_response,
        sources=unique_sources,
        confidence=confidence,
        canton=req.canton,
    )


# ---------------------------------------------------------------------------
# Route de test (sanity check sans consommer de tokens Claude)
# ---------------------------------------------------------------------------

@router.get("/fiscal-query/ping")
async def fiscal_ping(internal_key: str):
    """Vérifie que le service fiscal répond et que la clé est valide."""
    verify_internal_key(internal_key)
    return {
        "status": "ok",
        "service": "Soluris Fiscal RAG",
        "note": "Use POST /api/fiscal-query for full queries",
    }
