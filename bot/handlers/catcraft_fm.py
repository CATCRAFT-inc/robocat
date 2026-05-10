import asyncio
import logging
import os

from pathlib import Path
from random import randint, shuffle
from tinytag import TinyTag
from datetime import datetime
import math

import disnake
from disnake.ext import commands

from bot.storage import ColorStorage


class CatcraftFM(commands.Cog):
    GUILD_ID = 1138425078493753366
    CHANNEL_ID = 1502616927695015986

    RECONNECT_DELAY = 10 # секунд между попытками реконнекта
    MAX_RECONNECT_DELAY = 120 # максимальная пауза при backoff

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        base = Path(__file__).resolve().parents[2] / "data" / "catcraftfm"
        self.music_path = base / "music"
        self.dictor_path = base / "dictor"
        self._started = False
        self._task: asyncio.Task | None = None

        self.music_files = []
        self.current_track = []
        self.current_track_path: str = None

        self.vc: disnake.VoiceClient = None

        self.skip_votes = 0
        self.votes_list = []
        self.last_skip = 0

        self.logger = logging.getLogger("robocat.fm")

    @commands.Cog.listener()
    async def on_ready(self):
        if self._started:
            return
        self._started = True
        self._task = asyncio.create_task(self._radio_supervisor())

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: disnake.Member,
        before: disnake.VoiceState,
        after: disnake.VoiceState,
    ):
        if member != self.bot.user:
            return
        if before.channel is not None and after.channel is None:
            self.logger.warning("отключён от канала - включаем супервизора")
            if self.vc is not None and self.vc.is_connected():
                await self.vc.disconnect(force=True)

    async def _radio_supervisor(self):
        delay = self.RECONNECT_DELAY
        while True:
            try:
                await self._start_radio()
            except asyncio.CancelledError:
                self.logger.warning("супервизор отменён")
                return
            except Exception as e:
                self.logger.exception(f"_start_radio поднялся:", e)
            self.logger.info("перезагружаемся через %ss...", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.MAX_RECONNECT_DELAY)

    async def _start_radio(self):
        guild = self.bot.get_guild(self.GUILD_ID)
        if guild is None:
            print("[CatcraftFM] Guild not found")
            return

        self.channel = guild.get_channel(self.CHANNEL_ID)
        if self.channel is None:
            print(f"[CatcraftFM] Channel {self.CHANNEL_ID} not found")
            return

        if guild.voice_client is not None:
            await guild.voice_client.disconnect(force=True)

        try:
            self.vc = await self.channel.connect(timeout=10.0, reconnect=True)
        except Exception as e:
            self.logger.exception("Не удалось подключиться к каналу: %s", e)
            return

        try:
            await self._play_loop(self.vc)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.exception("play loop crash: %s", e)
        finally:
            if self.vc and self.vc.is_connected():
                await self.vc.disconnect(force=True)

    async def _play_loop(self, vc: disnake.VoiceClient):
        dictor_files: list[str] = []
        music_count = 0
        loop = asyncio.get_running_loop()

        while True:
            # Проверяем коннект в начале каждой итерации
            if not vc.is_connected():
                self.logger.warning("потеряно соединенеи к ГС - выхожу из цикла")
                return

            if music_count < 3:
                if not self.music_files:
                    self.music_files = os.listdir(self.music_path)
                    shuffle(self.music_files)
                if not self.music_files:
                    print("[CatcraftFM] No music files")
                    return
                track = self.music_files.pop(0)
                path = self.music_path / track
                music_count += 1
            else:
                if not dictor_files:
                    dictor_files = os.listdir(self.dictor_path)
                    shuffle(dictor_files)
                if not dictor_files:
                    music_count = 0
                    continue
                track = dictor_files.pop(randint(0, len(dictor_files) - 1))
                path = self.dictor_path / track
                music_count = 0

            done = asyncio.Event()

            def _after(error: Exception | None):
                if error is not None:
                    self.logger.exception("ошибка плейбека в %s: %s", track, error)
                loop.call_soon_threadsafe(done.set)

            is_dictor = music_count == 0

            try:
                vc.play(disnake.FFmpegPCMAudio(str(path)), after=_after)
            except Exception as e:
                self.logger.exception("ошибка плейбека в %s: %s", track, e)
                await asyncio.sleep(1)
                continue

            if not is_dictor:
                self.current_track = self._getTrackInfo(str(path))
                self.current_track_path = str(path)

                next_info = (
                    self._getTrackInfo(self.music_path / self.music_files[0])
                    if self.music_files else "—"
                )
                embed = disnake.ui.Container(
                    disnake.ui.TextDisplay(f"🎵 Сейчас играет: **{self.current_track}**"),
                    disnake.ui.Separator(),
                    disnake.ui.TextDisplay(f"-# Следующий трек: {next_info}"),
                    accent_colour=disnake.Color.from_hex(ColorStorage.main)
                )
                try:
                    await self.channel.send(components=embed)
                except Exception as e:
                    self.logger.exception("ошибка плейбека: %s", e)

            await done.wait()

    @commands.command(name="очередь", aliases=['queue', 'q'])
    async def musicQueue(self, command: disnake.MessageCommand):
        queue = ''.join([f"{self._getTrackInfo(self.music_path / i)}\n" for i in self.music_files[:4]])
        embed = disnake.ui.Container(
            disnake.ui.TextDisplay(f"## 🎵 Текущий трек: {self.current_track}"),
            disnake.ui.TextDisplay(queue)
        )
        await command.reply(components=embed)

    def _is_expired(self):
        now = datetime.now().timestamp()
        if now - self.last_skip > 5 * 60:
            self.skip_votes = 0
            self.votes_list = []
            self.last_skip = 0

    @commands.command(name='следующий', aliases=['некст', 'next', 'skip', 'скип', 'ytrcn'])
    async def nextTrack(self, ctx: commands.Context):
        if ctx.channel.id == 1502616927695015986:
            if len(ctx.channel.members) > 2:
                self._is_expired()
                listeners = len(ctx.channel.members)
                required_votes = self._requiredVotes(listeners)
                if ctx.author.id not in self.votes_list:
                    self.skip_votes += 1
                    if self.skip_votes >= required_votes:
                        await ctx.channel.send(f"{self.skip_votes} котика проголосовали за скип трека, пропускаем...")
                        self.vc.stop()
                        self.votes_list = []
                        self.last_skip = 0
                        self.skip_votes = 0
                    else:
                        await ctx.channel.send(f"{ctx.author.mention} проголосовал за пропуск песни! ({self.skip_votes}/{required_votes})")
                        self.votes_list.append(ctx.author.id)
                        self.last_skip = datetime.now().timestamp()
                else:
                    await ctx.reply("Ты уже проголосовал(а) за пропуск песни!", delete_after=5)
            else:
                self.vc.stop()

    @commands.command(name='radiostart')
    async def _radioForceStart(self, ctx):
        if self._started:
            await ctx.reply("уже", delete_after=5)
            return
        await ctx.reply("угу", delete_after=5)
        self._started = True
        self._task = asyncio.create_task(self._radio_supervisor())

    def _requiredVotes(self, listeners: int) -> int:
        if listeners <= 1:
            return 1
        if listeners == 2:
            return 2
        return math.ceil(listeners / 2)

    def _getTrackInfo(self, music_path: Path):
        tag: TinyTag = TinyTag.get(str(music_path))
        artist, title = tag.artist, tag.title
        return " - ".join([artist, title])

    def cog_unload(self):
        if self._task and not self._task.done():
            self._task.cancel()


def setup(bot: commands.Bot):
    bot.add_cog(CatcraftFM(bot))