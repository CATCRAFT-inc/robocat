# ВЫКЛЮЧЕНО: сервер закрыт, ког не загружается из main.py. Код оставлен на
# случай, если сервер когда-нибудь откроют снова.
import asyncio
import logging
import os
import struct
import time

import disnake
from disnake.ext import commands

from bot.discord_config import Roles, has_config_roles

logger = logging.getLogger("robocat.rcon")


_AUTH = 3
_CMD = 2
_RESP = 0


def _pack(req_id: int, ptype: int, payload: str) -> bytes:
    body = payload.encode("utf-8") + b"\x00\x00"
    return struct.pack("<III", 4 + 4 + len(body), req_id, ptype) + body


MAX_PACKET = 8192  # спека Source RCON — 4096; вдвое с запасом
FIRST_PACKET_WAIT = 2.0  # см. rcon_exec_many


async def _read_length(reader: asyncio.StreamReader) -> int:
    """Заголовок пакета.

    🛑 Отменять ЭТО ожидание безопасно, а чтение тела — уже нет: `readexactly`
    забирает байты из буфера только когда набралось нужное количество, поэтому
    отмена на заголовке ничего не съедает, а отмена ПОСЛЕ него оставляет поток
    сдвинутым на четыре байта. Именно поэтому короткий дедлайн вешается только
    сюда (воспроизведено: после отмены на теле следующий разбор падал на
    «неправдоподобной длине 2» — читал request id как длину).
    """
    length = struct.unpack("<I", await reader.readexactly(4))[0]
    if not 8 <= length <= MAX_PACKET:
        # Без границы битый заголовок с length=0xFFFFFFFF заставляет читателя
        # копить буфер до таймаута. Путь стал публичным (его дёргает !пинг).
        raise ValueError(f"RCON: неправдоподобная длина пакета {length}")
    return length


async def _read_body(reader: asyncio.StreamReader, length: int) -> tuple[int, int, str]:
    data = await reader.readexactly(length)
    req_id = struct.unpack("<i", data[0:4])[0]  # signed — auth fail returns -1
    ptype = struct.unpack("<I", data[4:8])[0]
    payload = data[8:-2].decode("utf-8", errors="replace")
    return req_id, ptype, payload


async def _read_packet(reader: asyncio.StreamReader) -> tuple[int, int, str]:
    return await _read_body(reader, await _read_length(reader))


async def rcon_exec_many(
    host: str, port: int, password: str, commands_: list[str],
    timeout: float = 10.0, total_timeout: float = 30.0,
) -> list[str]:
    """Одно соединение — несколько команд. Возвращает ответы в порядке команд.

    Каждая команда получает свой request id и свой пакет-маркер следом: сервер
    отвечает на пакеты по порядку, поэтому маркер надёжно отделяет ответ одной
    команды от следующей, даже когда ответ приехал несколькими пакетами.

    `timeout` — бюджет на ОДНУ операцию, `total_timeout` — на весь вызов.
    Второй обязателен: без него сервер, сыплющий пакетами, держит корутину
    сколько угодно, и потолок был только у `!пинг`, а у админской `/rcon` — нет.
    """
    deadline = time.monotonic() + total_timeout

    def left(step: float) -> float:
        """Сколько ждать следующую операцию: меньшее из шага и остатка бюджета."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError("RCON: исчерпан общий бюджет времени")
        return min(step, remaining)

    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=left(timeout)
    )
    try:
        # Auth
        writer.write(_pack(1, _AUTH, password))
        await writer.drain()
        req_id, _, _ = await asyncio.wait_for(_read_packet(reader), timeout=left(timeout))
        if req_id == -1:
            raise PermissionError("RCON: неверный пароль")

        responses: list[str] = []
        for i, command in enumerate(commands_):
            cmd_id = 2 + i * 2
            sentinel_id = cmd_id + 1

            # 🛑 Маркер НЕЛЬЗЯ слать в одном всплеске с командой. Ванильный RCON
            # разбирает сокет одним read(): если оба пакета попали в один
            # TCP-сегмент, длина первого не сходится с размером прочитанного,
            # и сервер молча закрывает соединение (наружу — IncompleteReadError).
            # Поэтому маркер уходит только после первого пакета ответа: к этому
            # моменту сервер дочитал команду и снова ждёт ввод.
            writer.write(_pack(cmd_id, _CMD, command))
            await writer.drain()

            parts: list[str] = []
            # Ждём первый пакет, но НЕ бесконечно: команда, на которую сервер
            # не отвечает ничем, раньше отдавала пустую строку сразу, и мы не
            # имеем права превращать это в таймаут. Двух секунд с запасом
            # хватает, чтобы сервер успел вычитать команду из сокета.
            # Дедлайн — ТОЛЬКО на заголовке: см. докстринг `_read_length`.
            try:
                length = await asyncio.wait_for(
                    _read_length(reader), timeout=left(min(FIRST_PACKET_WAIT, timeout))
                )
            except asyncio.TimeoutError:
                logger.debug("RCON: команда не дала ни одного пакета, шлю маркер")
            else:
                rid, _, payload = await asyncio.wait_for(
                    _read_body(reader, length), timeout=left(timeout)
                )
                if rid == cmd_id:
                    parts.append(payload)
                else:
                    logger.warning("RCON: пакет с неожиданным id %s — отброшен", rid)

            writer.write(_pack(sentinel_id, _CMD, ""))
            await writer.drain()

            # Хвост многопакетного ответа приедет до ответа на маркер.
            while True:
                rid, _, payload = await asyncio.wait_for(_read_packet(reader), timeout=left(timeout))
                if rid == sentinel_id:
                    break
                if rid == cmd_id:
                    parts.append(payload)
                else:
                    # Молчаливый отброс превращал ответ в пустую строку,
                    # неотличимую от «команда ничего не вывела».
                    logger.warning("RCON: пакет с неожиданным id %s — отброшен", rid)
            responses.append("".join(parts))

        return responses
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            logger.debug("RCON: не удалось корректно закрыть соединение", exc_info=True)


async def rcon_exec(host: str, port: int, password: str, command: str, timeout: float = 10.0) -> str:
    """Connect, authenticate, run command, collect full multi-packet response."""
    responses = await rcon_exec_many(host, port, password, [command], timeout)
    return responses[0]


class RCONHandler(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.host = os.getenv("RCON_HOST", "")
        self.port = int(os.getenv("RCON_PORT", "25575"))
        self.password = os.getenv("RCON_PASSWORD", "")

    @commands.slash_command(name="rcon", description="Выполнить RCON команду на сервере Minecraft")
    @has_config_roles("admin", "st_admin")
    async def rconCommand(
        self,
        inter: disnake.ApplicationCommandInteraction,
        command: str = commands.Param(description="Команда для сервера, например: say Hello"),
    ):
        if not self.host or not self.password:
            await inter.send(
                "❌ RCON не настроен — укажи `RCON_HOST` и `RCON_PASSWORD` в `.env`",
                ephemeral=True,
            )
            return

        await inter.response.defer(ephemeral=True)

        try:
            response = await rcon_exec(self.host, self.port, self.password, command)
        except PermissionError as e:
            logger.error("RCON: аутентификация не прошла — неверный пароль")
            await inter.edit_original_response(f"❌ {e}")
            return
        except asyncio.TimeoutError:
            logger.warning("RCON: таймаут подключения к серверу")
            await inter.edit_original_response("❌ Таймаут подключения к серверу")
            return
        except OSError as e:
            # str(e) может содержать адрес из .env — логируем только тип ошибки
            logger.error("RCON: не удалось подключиться (%s)", type(e).__name__)
            await inter.edit_original_response(f"❌ Не удалось подключиться: `{e}`")
            return
        except Exception as e:
            logger.exception("RCON unexpected error")
            await inter.edit_original_response(f"❌ Ошибка: `{e}`")
            return

        response_text = response.strip() if response else "*(Нет ответа)*"
        logger.info("RCON by %s → %r → %r", inter.author, command, response_text[:100])

        # Лимит V2 — 4000 символов на ВСЕ TextDisplay сообщения разом:
        # длинная команда + ответ раньше пробивали его и ловили HTTP 400
        cmd_display = command if len(command) <= 300 else command[:300] + "…"
        limit = 3500 - len(cmd_display)
        display = response_text[:limit] if len(response_text) > limit else response_text
        truncated = len(response_text) > limit

        await inter.edit_original_response(
            components=disnake.ui.Container(
                disnake.ui.TextDisplay(f"**`{cmd_display}`**"),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay(f"```\n{display}\n```"),
                *([disnake.ui.TextDisplay(f"-# Ответ обрезан ({len(response_text)} символов)")] if truncated else []),
            )
        )


def setup(bot: commands.Bot):
    bot.add_cog(RCONHandler(bot))
