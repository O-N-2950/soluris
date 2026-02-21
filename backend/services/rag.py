"""RAG Pipeline — Retrieval Augmented Generation for Swiss law"""
import os
import json
import httpx
from typing import List, Dict

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

SYSTEM_PROMPT = """Tu es Soluris, un assistant juridique IA spécialisé en droit suisse.

RÈGLES STRICTES :
1. Tu réponds UNIQUEMENT sur la base du droit suisse (fédéral et cantonal)
2. Tu cites TOUJOURS tes sources avec les références exactes (articles de loi, numéros ATF, etc.)
3. Tu indiques clairement quand tu n'es pas sûr d'une information
4. Tu ne donnes JAMAIS de conseil juridique personnel — tu fournis de l'information juridique
5. Tu réponds en français, sauf si l'utilisateur écrit dans une autre langue
6. Tu structures tes réponses de façon claire avec les références entre parenthèses
7. Tu mentionnes la jurisprudence pertinente quand elle existe

FORMAT DES SOURCES :
Pour chaque source citée, ajoute à la fin de ta réponse un bloc JSON comme suit :
[SOURCES]
[{"reference": "Art. 41 CO", "title": "Responsabilité délictuelle", "url": "https://www.fedlex.admin.ch/eli/cc/27/317_321_377/fr#art_41"}]
[/SOURCES]

Si tu n'as pas de sources pertinentes provenant du contexte fourni, base-toi sur tes connaissances du droit suisse
et cite les articles/ATF de mémoire avec les références appropriées."""


async def generate_answer(question: str, history: List[Dict]) -> Dict:
    """Generate a legal answer using Claude API with optional RAG context."""

    # Build messages from history
    messages = []
    for msg in history[-8:]:  # Last 8 messages for context
        if msg.get("role") in ("user", "assistant"):
            messages.append({"role": msg["role"], "content": msg["content"]})

    # Add the new question if not already in history
    if not messages or messages[-1].get("content") != question:
        messages.append({"role": "user", "content": question})

    # TODO: Add RAG retrieval here
    # 1. Embed the question using Cohere multilingual
    # 2. Search legal_chunks by vector similarity
    # 3. Prepend relevant chunks to system prompt
    # For now, we rely on Claude's knowledge of Swiss law

    # Call Claude API
    if not ANTHROPIC_API_KEY:
        return {
            "response": "⚠️ Clé API Anthropic non configurée. Ajoutez ANTHROPIC_API_KEY dans les variables d'environnement Railway.",
            "sources": [],
            "tokens": 0,
        }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": ANTHROPIC_MODEL,
                    "max_tokens": 4096,
                    "system": SYSTEM_PROMPT,
                    "messages": messages,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        # Extract response text
        full_text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                full_text += block["text"]

        # Parse sources from response
        sources = []
        response_text = full_text
        if "[SOURCES]" in full_text:
            parts = full_text.split("[SOURCES]")
            response_text = parts[0].strip()
            if len(parts) > 1:
                source_block = parts[1].split("[/SOURCES]")[0].strip()
                try:
                    sources = json.loads(source_block)
                except json.JSONDecodeError:
                    pass

        tokens = data.get("usage", {}).get("input_tokens", 0) + data.get("usage", {}).get("output_tokens", 0)

        return {
            "response": response_text,
            "sources": sources,
            "tokens": tokens,
        }

    except httpx.HTTPStatusError as e:
        return {
            "response": f"Erreur API Claude ({e.response.status_code}). Veuillez réessayer.",
            "sources": [],
            "tokens": 0,
        }
    except Exception as e:
        return {
            "response": f"Erreur inattendue : {str(e)}",
            "sources": [],
            "tokens": 0,
        }
