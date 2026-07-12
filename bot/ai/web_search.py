"""Бесплатный веб-поиск для ИИ-тулзы — по образцу keyless-бэкендов Hermes Agent.

Два бэкенда, оба без платных ключей:
- SearXNG (self-hosted метапоиск): используется первым, если задан env SEARXNG_URL;
- ddgs (метапоиск DuckDuckGo/Google/Bing/…): fallback без всякой инфраструктуры.

Инварианты домена: импорт модуля не ходит в сеть и не требует зависимостей
(ddgs импортируется лениво — бот живёт и без установленного пакета).
Результаты кэшируются на 15 минут, рейтлимит ddgs уводит бэкенд в кулдаун.
"""

import asyncio
import logging
import os
import time

logger = logging.getLogger("robocat.websearch")

SEARCH_TIMEOUT = 10          # секунд на весь поиск одним бэкендом
MAX_RESULTS = 5
CACHE_TTL = 15 * 60          # повторные вопросы «а какая версия X» очень часты
_CACHE_MAX = 128
DDGS_RATELIMIT_COOLDOWN = 5 * 60


class WebSearcher:
    def __init__(self):
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._ddgs_cooldown_until = 0.0

    # -------- публичное API --------

    async def search(self, query: str) -> list[dict]:
        """Топ результатов вида {title, url, snippet}. Пустой список = не нашли/всё лежит."""
        query = (query or "").strip()
        if not query:
            return []

        cached = self._cache.get(query.lower())
        if cached and time.time() - cached[0] < CACHE_TTL:
            return cached[1]

        results: list[dict] = []
        for backend in self._backends():
            try:
                results = await asyncio.wait_for(backend(query), timeout=SEARCH_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("Веб-поиск: таймаут бэкенда %s на запросе (len=%d)",
                               backend.__name__, len(query))
                continue
            except Exception:
                logger.exception("Веб-поиск: бэкенд %s упал", backend.__name__)
                continue
            if results:
                break

        if results:
            self._cache[query.lower()] = (time.time(), results)
            if len(self._cache) > _CACHE_MAX:
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                self._cache.pop(oldest, None)
        return results

    def build_context(self, results: list[dict]) -> str:
        """Контекст для LLM — тем же форматом, что wiki_search.build_context."""
        parts = []
        for r in results:
            parts.append(
                f"[Источник: {r['url']}]\n{r['title']}\n{r['snippet']}"
            )
        return "\n\n---\n\n".join(parts)

    # -------- бэкенды --------

    def _backends(self):
        chain = []
        if os.getenv("SEARXNG_URL"):
            chain.append(self._search_searxng)
        if time.time() >= self._ddgs_cooldown_until:
            chain.append(self._search_ddgs)
        return chain

    async def _search_searxng(self, query: str) -> list[dict]:
        """Свой SearXNG-инстанс: GET /search?format=json (в settings.yml нужен formats: [html, json])."""
        import aiohttp  # транзитивная зависимость disnake, но держим импорт ленивым

        base = os.getenv("SEARXNG_URL", "").rstrip("/")
        params = {"q": query, "format": "json", "language": "ru", "safesearch": "1"}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base}/search", params=params,
                                   timeout=aiohttp.ClientTimeout(total=SEARCH_TIMEOUT)) as resp:
                if resp.status != 200:
                    logger.warning("SearXNG ответил %s (json-формат включён в settings.yml?)", resp.status)
                    return []
                data = await resp.json()
        return self._normalize(
            (r.get("title"), r.get("url"), r.get("content"))
            for r in data.get("results", [])
        )

    async def _search_ddgs(self, query: str) -> list[dict]:
        """Метапоиск ddgs (PyPI `ddgs`). Sync-библиотека — уводим в поток."""
        try:
            from ddgs import DDGS
            from ddgs.exceptions import DDGSException, RatelimitException
        except ImportError:
            logger.warning("Пакет ddgs не установлен — веб-поиск через ddgs недоступен")
            return []

        def _run() -> list[dict]:
            return DDGS(timeout=5).text(
                query, region="ru-ru", safesearch="moderate", max_results=MAX_RESULTS,
            )

        try:
            raw = await asyncio.to_thread(_run)
        except RatelimitException:
            self._ddgs_cooldown_until = time.time() + DDGS_RATELIMIT_COOLDOWN
            logger.warning("ddgs словил рейтлимит — кулдаун %d минут", DDGS_RATELIMIT_COOLDOWN // 60)
            return []
        except DDGSException:
            logger.exception("ddgs упал на запросе (len=%d)", len(query))
            return []
        return self._normalize(
            (r.get("title"), r.get("href"), r.get("body")) for r in raw or []
        )

    @staticmethod
    def _normalize(rows) -> list[dict]:
        results = []
        for title, url, snippet in rows:
            if not url:
                continue
            results.append({
                "title": (title or "")[:200],
                "url": url,
                "snippet": (snippet or "")[:300],
            })
            if len(results) >= MAX_RESULTS:
                break
        return results


web = WebSearcher()
