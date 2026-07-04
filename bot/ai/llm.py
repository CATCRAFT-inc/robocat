"""Единая точка доступа к LLM для всего бота.

Владеет ротацией вендоров (кулдауны на ошибках), учётом токенов и utility-моделью
для структурного вывода. engine.py, тикеты и /digest ходят в API только отсюда.

Клиенты создаются лениво — импорт модуля без сети/ключей ничего не запрашивает.
"""

import logging
import os
import time
from pathlib import Path

import openai
from openai import AsyncClient
import yaml

_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "data" / "ai_settings.yaml"

RATE_LIMIT_COOLDOWN = 15 * 60      # 15 минут
AUTH_COOLDOWN = 6 * 60 * 60        # 6 часов


class AIUnavailable(Exception):
    """Все вендоры на кулдауне/недоступны."""


class _Vendor:
    def __init__(self, cfg: dict):
        self.model = cfg["model"]
        self.base_url = cfg["base_url"]
        self.env = cfg["env"]
        self.has_vision = cfg.get("has_vision", False)
        self.extra_body = cfg.get("extra_body")
        self.cooldown_until = 0.0
        self._client: AsyncClient | None = None

    @property
    def available(self) -> bool:
        return time.time() >= self.cooldown_until

    @property
    def client(self) -> AsyncClient:
        if self._client is None:
            self._client = AsyncClient(base_url=self.base_url, api_key=os.getenv(self.env))
        return self._client

    def cooldown(self, seconds: float):
        self.cooldown_until = time.time() + seconds

    async def close(self):
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None


class LLM:
    def __init__(self):
        self.logger = logging.getLogger("robocat.llm")
        self._loaded = False
        self.vendors: list[_Vendor] = []
        self.utility: _Vendor | None = None

    # -------- загрузка настроек --------

    def _load(self):
        with _SETTINGS_PATH.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
        self.vendors = [_Vendor(v) for v in data.get("vendors", [])]
        util_cfg = data.get("utility_model")
        self.utility = _Vendor(util_cfg) if util_cfg else None
        self._loaded = True

    def _ensure_loaded(self):
        if not self._loaded:
            self._load()

    async def reload(self):
        """Закрыть старые клиенты и перечитать ai_settings.yaml."""
        for vendor in self.vendors:
            await vendor.close()
        if self.utility is not None:
            await self.utility.close()
        self._load()

    # -------- инфо для /aiinfo и engine --------

    @property
    def current_vendor(self) -> _Vendor | None:
        """Первый доступный (не на кулдауне) чат-вендор."""
        self._ensure_loaded()
        for vendor in self.vendors:
            if vendor.available:
                return vendor
        return None

    def image_client(self) -> AsyncClient:
        """Клиент для генерации картинок (endpoint utility-вендора — Gemini)."""
        self._ensure_loaded()
        if self.utility is None:
            raise AIUnavailable("utility-модель не настроена")
        return self.utility.client

    def cooldown_report(self) -> str:
        self._ensure_loaded()
        lines = []
        for vendor in self.vendors:
            if vendor.available:
                lines.append(f"{vendor.env}/{vendor.model}: доступен")
            else:
                lines.append(f"{vendor.env}/{vendor.model}: кулдаун <t:{int(vendor.cooldown_until)}:R>")
        return "\n".join(lines) or "нет вендоров"

    # -------- учёт токенов --------

    async def _track_usage(self, response):
        # Ленивый импорт: держим импорт модуля свободным от цепочки bot.utils/bot.bot,
        # чтобы `import bot.ai.llm` не требовал сети/ключей и не ловил циклический импорт.
        from bot.flag_system.flag_system import flags
        try:
            usage = getattr(response, "usage", None)
            if usage and usage.total_tokens:
                await flags.setFlag("abstract", "token_used", f"+{usage.total_tokens}")
        except Exception:
            self.logger.exception("Не удалось записать использованные токены")

    # -------- ядро ротации --------

    async def _execute(self, vendors: list[_Vendor], build_params, *, parse: bool = False):
        """Пройтись по вендорам по порядку. Кулдаун на RateLimit/Auth,
        1 ретрай на транзиентных ошибках, затем следующий вендор.
        Все недоступны → AIUnavailable.
        """
        self._ensure_loaded()
        for vendor in vendors:
            if vendor is None or not vendor.available:
                continue
            params = build_params(vendor)
            for attempt in range(2):  # 1 попытка + 1 ретрай на транзиентных ошибках
                try:
                    if parse:
                        response = await vendor.client.chat.completions.parse(**params)
                    else:
                        response = await vendor.client.chat.completions.create(**params)
                except openai.RateLimitError:
                    self.logger.warning("RateLimit у %s — кулдаун 15м", vendor.env)
                    vendor.cooldown(RATE_LIMIT_COOLDOWN)
                    break
                except openai.AuthenticationError:
                    self.logger.warning("AuthError у %s — кулдаун 6ч", vendor.env)
                    vendor.cooldown(AUTH_COOLDOWN)
                    break
                except (openai.InternalServerError, openai.APIConnectionError) as e:
                    self.logger.warning("Транзиентная ошибка у %s (попытка %s): %s", vendor.env, attempt + 1, e)
                    if attempt == 0:
                        continue
                    break
                else:
                    await self._track_usage(response)
                    return response
        raise AIUnavailable("Все ИИ-вендоры недоступны")

    # -------- публичное API (контракт) --------

    async def complete(self, messages: list[dict], *, tools: list | None = None,
                       max_tokens: int = 1024, temperature: float = 0.6, top_p: float = 1,
                       require_vision: bool = False):
        """Низкий уровень для engine.py: полный ответ SDK, с ротацией вендоров.

        require_vision=True — ротация только по вендорам с has_vision
        (в диалоге есть картинка, не-vision модель вернула бы 400)."""
        def build(vendor: _Vendor) -> dict:
            params = {
                "model": vendor.model,
                "messages": messages,
                "temperature": temperature,
                "top_p": top_p,
                "stream": False,
                "max_tokens": max_tokens,
            }
            if tools:
                params["tools"] = tools
            if vendor.extra_body:
                params["extra_body"] = vendor.extra_body
            return params

        vendors = [v for v in self.vendors if v.has_vision] if require_vision else self.vendors
        return await self._execute(vendors, build)

    async def ask(self, prompt: str, *, system: str | None = None,
                  max_tokens: int = 1024, use_utility: bool = False) -> str:
        """Одноразовый вопрос → текст. Кидает AIUnavailable."""
        self._ensure_loaded()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        if use_utility:
            if self.utility is None:
                raise AIUnavailable("utility-модель не настроена")
            vendors = [self.utility]
        else:
            vendors = self.vendors

        def build(vendor: _Vendor) -> dict:
            params = {
                "model": vendor.model,
                "messages": messages,
                "stream": False,
                "max_tokens": max_tokens,
            }
            if vendor.extra_body:
                params["extra_body"] = vendor.extra_body
            return params

        response = await self._execute(vendors, build)
        return response.choices[0].message.content or ""

    async def parse(self, prompt: str, schema, *, system: str | None = None):
        """Структурный вывод через utility-модель. Кидает AIUnavailable."""
        self._ensure_loaded()
        if self.utility is None:
            raise AIUnavailable("utility-модель не настроена")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        def build(vendor: _Vendor) -> dict:
            params = {
                "model": vendor.model,
                "messages": messages,
                "temperature": 0,
                "max_tokens": 512,
                "response_format": schema,
            }
            if vendor.extra_body:
                params["extra_body"] = vendor.extra_body
            return params

        response = await self._execute([self.utility], build, parse=True)
        return response.choices[0].message.parsed


llm = LLM()  # модульный синглтон, ленивая инициализация при первом вызове
