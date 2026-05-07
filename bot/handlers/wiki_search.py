import json
import math
import os
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI"))

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

    def _cosine(self, a, b) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x**2 for x in a))
        norm_b = math.sqrt(sum(x**2 for x in b))
        return dot / (norm_a * norm_b)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        result = client.models.embed_content(
            model="gemini-embedding-001",
            contents=query,
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_QUERY",
                output_dimensionality=768,
            ),
        )
        query_vec = result.embeddings[0].values

        scored = sorted(
            self.index,
            key=lambda c: self._cosine(query_vec, c["vector"]),
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