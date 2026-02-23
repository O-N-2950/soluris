"""
Service d'Embeddings pour Soluris
==================================
Abstraction pour générer des embeddings via Cohere ou OpenAI.
Supporte le batching, le retry, et le fallback.

Modèles supportés :
  - Cohere embed-multilingual-v3.0 (1024 dim, $0.10/M tokens) — recommandé pour multilingual FR/DE
  - OpenAI text-embedding-3-small (1536 dim, $0.02/M tokens) — alternatif
"""

import logging
import os
import time
from dataclasses import dataclass

import requests

log = logging.getLogger("soluris.embeddings")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class EmbeddingConfig:
    provider: str = "cohere"  # "cohere" or "openai"
    cohere_model: str = "embed-multilingual-v3.0"
    openai_model: str = "text-embedding-3-small"
    cohere_api_key: str = ""
    openai_api_key: str = ""
    batch_size: int = 96       # Cohere max = 96 per request
    max_retries: int = 3
    retry_delay: float = 1.0
    dimensions: int = 0        # 0 = use model default

    def __post_init__(self):
        if not self.cohere_api_key:
            self.cohere_api_key = os.getenv("COHERE_API_KEY", "")
        if not self.openai_api_key:
            self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        if self.dimensions == 0:
            self.dimensions = 1024 if self.provider == "cohere" else 1536


# ---------------------------------------------------------------------------
# Embedding providers
# ---------------------------------------------------------------------------

def _embed_cohere(
    texts: list[str],
    config: EmbeddingConfig,
    input_type: str = "search_document",
) -> list[list[float]]:
    """Generate embeddings via Cohere API.
    
    input_type: "search_document" for indexing, "search_query" for queries
    """
    url = "https://api.cohere.com/v2/embed"
    headers = {
        "Authorization": f"Bearer {config.cohere_api_key}",
        "Content-Type": "application/json",
    }

    all_embeddings = []
    for i in range(0, len(texts), config.batch_size):
        batch = texts[i : i + config.batch_size]

        payload = {
            "model": config.cohere_model,
            "texts": batch,
            "input_type": input_type,
            "embedding_types": ["float"],
        }

        for attempt in range(config.max_retries):
            try:
                r = requests.post(url, json=payload, headers=headers, timeout=60)
                if r.status_code == 429:
                    wait = config.retry_delay * (2 ** attempt)
                    log.warning(f"Cohere rate limit, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                data = r.json()
                embeddings = data.get("embeddings", {}).get("float", [])
                all_embeddings.extend(embeddings)
                break
            except Exception as e:
                if attempt == config.max_retries - 1:
                    log.error(f"Cohere embed failed after {config.max_retries} attempts: {e}")
                    raise
                time.sleep(config.retry_delay * (attempt + 1))

        if i + config.batch_size < len(texts):
            time.sleep(0.1)  # Rate limit courtesy

    return all_embeddings


def _embed_openai(
    texts: list[str],
    config: EmbeddingConfig,
) -> list[list[float]]:
    """Generate embeddings via OpenAI API."""
    url = "https://api.openai.com/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {config.openai_api_key}",
        "Content-Type": "application/json",
    }

    all_embeddings = []
    batch_size = 2048  # OpenAI supports larger batches

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]

        payload = {
            "model": config.openai_model,
            "input": batch,
        }
        if config.dimensions and config.dimensions != 1536:
            payload["dimensions"] = config.dimensions

        for attempt in range(config.max_retries):
            try:
                r = requests.post(url, json=payload, headers=headers, timeout=60)
                if r.status_code == 429:
                    wait = config.retry_delay * (2 ** attempt)
                    log.warning(f"OpenAI rate limit, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                data = r.json()
                batch_embeddings = [item["embedding"] for item in data["data"]]
                all_embeddings.extend(batch_embeddings)
                break
            except Exception as e:
                if attempt == config.max_retries - 1:
                    log.error(f"OpenAI embed failed: {e}")
                    raise
                time.sleep(config.retry_delay * (attempt + 1))

    return all_embeddings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class EmbeddingService:
    """Unified embedding service with provider abstraction."""

    def __init__(self, config: EmbeddingConfig = None):
        self.config = config or EmbeddingConfig()

    @property
    def dimensions(self) -> int:
        return self.config.dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for documents (indexing)."""
        if not texts:
            return []

        log.info(f"Embedding {len(texts)} documents via {self.config.provider}...")
        start = time.time()

        if self.config.provider == "cohere":
            result = _embed_cohere(texts, self.config, input_type="search_document")
        elif self.config.provider == "openai":
            result = _embed_openai(texts, self.config)
        else:
            raise ValueError(f"Unknown provider: {self.config.provider}")

        elapsed = time.time() - start
        log.info(f"Embedded {len(texts)} documents in {elapsed:.1f}s")
        return result

    def embed_query(self, text: str) -> list[float]:
        """Generate embedding for a search query."""
        if self.config.provider == "cohere":
            result = _embed_cohere([text], self.config, input_type="search_query")
        elif self.config.provider == "openai":
            result = _embed_openai([text], self.config)
        else:
            raise ValueError(f"Unknown provider: {self.config.provider}")
        return result[0]


def get_embedding_service() -> EmbeddingService:
    """Factory function — reads config from environment."""
    provider = os.getenv("EMBEDDING_PROVIDER", "cohere")
    return EmbeddingService(EmbeddingConfig(provider=provider))
