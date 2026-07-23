import asyncio
import logging
import os
import struct

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


async def _read_packet(reader: asyncio.StreamReader) -> tuple[int, int, str]:
    header = await reader.readexactly(4)
    length = struct.unpack("<I", header)[0]
    data = await reader.readexactly(length)
    req_id = struct.unpack("<i", data[0:4])[0]  # signed — auth fail returns -1
    ptype = struct.unpack("<I", data[4:8])[0]
    payload = data[8:-2].decode("utf-8", errors="replace")
    return req_id, ptype, payload


async def rcon_exec(host: str, port: int, password: str, command: str, timeout: float = 10.0) -> str:
    """Connect, authenticate, run command, collect full multi-packet response."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout
    )
    try:
        # Auth
        writer.write(_pack(1, _AUTH, password))
        await writer.drain()
        req_id, _, _ = await asyncio.wait_for(_read_packet(reader), timeout=timeout)
        if req_id == -1:
            raise PermissionError("RCON: неверный пароль")

        # Command + sentinel
        CMD_ID = 2
        SENTINEL_ID = 3
        writer.write(_pack(CMD_ID, _CMD, command))
        writer.write(_pack(SENTINEL_ID, _CMD, ""))
        await writer.drain()

        # Collect all response packets until sentinel arrives
        parts: list[str] = []
        while True:
            rid, _, payload = await asyncio.wait_for(_read_packet(reader), timeout=timeout)
            if rid == SENTINEL_ID:
                break
            if rid == CMD_ID:
                parts.append(payload)

        return "".join(parts)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            logger.debug("RCON: не удалось корректно закрыть соединение", exc_info=True)


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
