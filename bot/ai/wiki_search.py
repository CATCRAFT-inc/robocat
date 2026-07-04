import json
from pathlib import Path

from .embeddings import embed, cosine


class WikiSearcher:
    def __init__(self):
        self._index = None
        self._index_file = Path(__file__).resolve().parents[2] / "data" / "wiki_index.json"

    @property
    def index(self):
        # Грузим один раз при первом обращении, держим в памяти
        if self._index is None:
            self._index = json.loads(self._index_file.read_text(encoding="utf-8"))
        return self._index

    async def search(self, query: str, top_k: int = 5) -> list[dict]:
        query_vec = await embed(query)
        scored = sorted(
            self.index,
            key=lambda c: cosine(query_vec, c["vector"]),
            reverse=True,
        )
        return scored[:top_k]

    def build_context(self, results: list[dict]) -> str:
        """Контекст для LLM вставляется в системный промпт или user message."""
        parts = []
        for r in results:
            parts.append(
                f"[Источник: {r['url']}]\n{r['text']}"
            )
        return "\n\n---\n\n".join(parts)


wiki = WikiSearcher()
