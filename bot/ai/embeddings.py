"""Эмбеддинги через OpenAI-совместимый endpoint Gemini (768-мерные).

Клиент ленивый — импорт модуля без сети/ключей ничего не запрашивает.

🛑 СМЕНА МОДЕЛИ = ПЕРЕСБОРКА ВСЕХ ВЕКТОРОВ. Пространства моделей несовместимы:
косинус между вектором от одной и вектором от другой — не «похуже», а мусор,
и мусор МОЛЧАЛИВЫЙ (ошибки нет, выдача просто перестаёт попадать в тему).
Потребителей два, и переезжать они обязаны вместе с этой строкой:
`data/wiki_index.json` (`scripts/build_wiki_index.py`) и `data/bug_index.json`
(дедуп баг-репортов, живёт только на проде, мигрируется
`scripts/migrate_bug_index.py`).

Квоты free-tier считаются НА МОДЕЛЬ (`...PerProjectPerModel-FreeTier`), поэтому
переезд заодно даёт нетронутый суточный лимит.
"""

import math
import os

from openai import AsyncClient

# 3072 родных, режется до 1536/768 без потери качества; 768 — рекомендованный
# компромисс по объёму хранения, на нём собраны оба индекса.
MODEL = "gemini-embedding-2"
DIMENSIONS = 768

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
        model=MODEL,
        input=text,
        dimensions=DIMENSIONS,
    )
    return response.data[0].embedding


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
