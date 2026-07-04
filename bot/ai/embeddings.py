"""Эмбеддинги через OpenAI-совместимый endpoint Gemini (gemini-embedding-001, 768-мерные).

Клиент ленивый — импорт модуля без сети/ключей ничего не запрашивает.
"""

import math
import os

from openai import AsyncClient

_client: AsyncClient | None = None


def _get_client() -> AsyncClient:
    global _client
    if _client is None:
        _client = AsyncClient(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=os.getenv("GEMINI"),
        )
    return _client


async def embed(text: str) -> list[float]:
    """768-мерный вектор для текста."""
    response = await _get_client().embeddings.create(
        model="gemini-embedding-001",
        input=text,
        dimensions=768,
    )
    return response.data[0].embedding


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
