import asyncio
import logging
import math
import os
from datetime import datetime
from pathlib import Path
from random import randint, shuffle

import disnake
from disnake.ext import commands
from tinytag import TinyTag

from bot.storage import ColorStorage


class CatcraftFM(commands.Cog):
    GUILD_ID = 1138425078493753366
    CHANNEL_ID = 1502616927695015986

    RECONNECT_DELAY = 10           # стартовая пауза между попытками реконнекта
    MAX_RECONNECT_DELAY = 120      # потолок exponential backoff
    HEARTBEAT_INTERVAL = 5.0       # как часто будим play_loop проверить коннект
    MAX_TRACK_SECONDS = 20 * 60    # failsafe: ни один трек не висит дольше 20 минут

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        base = Path(__file__).resolve().parents[2] / "data" / "catcraftfm"
        self.music_path = base / "music"
        self.dictor_path = base / "dictor"

        self._started = False
        self._task: asyncio.Task | None = None

        self.music_files: list[str] = []
        self.current_track: str = "—"
        self.current_track_path: str | None = None

        self.vc: disnake.VoiceClient | None = None
        self.channel: disnake.VoiceChannel | None = None

        # event текущего проигрываемого трека — нужен, чтобы on_voice_state_update
        # мог разбудить play_loop при внезапном дисконнекте
        self._current_done: asyncio.Event | None = None

        self.skip_votes = 0
        self.votes_list: list[int] = []
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
            self.logger.warning("отключён от канала — будим супервизора")
            # разбудить play_loop, если он висит на done.wait()
            if self._current_done is not None:
                self._current_done.set()
            if self.vc is not None and self.vc.is_connected():
                try:
                    await self.vc.disconnect(force=True)
                except Exception:
                    self.logger.exception("ошибка disconnect в on_voice_state_update")

    async def _radio_supervisor(self):
        delay = self.RECONNECT_DELAY
        while True:
            try:
                await self._start_radio()
            except asyncio.CancelledError:
                self.logger.warning("супервизор отменён")
                return
            except Exception:
                self.logger.exception("_start_radio упал")
            self.logger.info("перезагружаемся через %ss...", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.MAX_RECONNECT_DELAY)

    async def _start_radio(self):
        guild = self.bot.get_guild(self.GUILD_ID)
        if guild is None:
            self.logger.error("guild %s не найден", self.GUILD_ID)
            return

        self.channel = guild.get_channel(self.CHANNEL_ID)
        if self.channel is None:
            self.logger.error("channel %s не найден", self.CHANNEL_ID)
            return

        if guild.voice_client is not None:
            try:
                await guild.voice_client.disconnect(force=True)
            except Exception:
                self.logger.exception("не смог дёрнуть существующий voice_client")

        try:
            self.vc = await self.channel.connect(timeout=10.0, reconnect=True)
        except Exception:
            self.logger.exception("не удалось подключиться к каналу")
            return

        try:
            await self._play_loop(self.vc)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("play loop crash")
        finally:
            if self.vc and self.vc.is_connected():
                try:
                    await self.vc.disconnect(force=True)
                except Exception:
                    self.logger.exception("ошибка disconnect в finally")
            self._current_done = None

    async def _play_loop(self, vc: disnake.VoiceClient):
        dictor_files: list[str] = []
        music_count = 0
        loop = asyncio.get_running_loop()

        while True:
            if not vc.is_connected():
                self.logger.warning("vc не подключён — выхожу из play_loop")
                return

            # выбор следующего трека
            if music_count < 3:
                if not self.music_files:
                    self.music_files = os.listdir(self.music_path)
                    shuffle(self.music_files)
                if not self.music_files:
                    self.logger.error("папка music пуста")
                    return
                track = self.music_files.pop(0)
                path = self.music_path / track
                music_count += 1
                is_dictor = False
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
                is_dictor = True

            done = asyncio.Event()
            self._current_done = done

            def _after(error: Exception | None):
                if error is not None:
                    self.logger.error("ошибка плейбека в %s: %s", track, error)
                loop.call_soon_threadsafe(done.set)

            try:
                vc.play(disnake.FFmpegPCMAudio(str(path)), after=_after)
            except Exception:
                self.logger.exception("не смог запустить трек %s", track)
                self._current_done = None
                await asyncio.sleep(1)
                continue

            # отправка now-playing эмбеда (только для музыки)
            if not is_dictor:
                try:
                    self.current_track = self._getTrackInfo(path)
                    self.current_track_path = str(path)

                    next_info = (
                        self._getTrackInfo(self.music_path / self.music_files[0])
                        if self.music_files else "—"
                    )
                    embed = disnake.ui.Container(
                        disnake.ui.TextDisplay(f"🎵 Сейчас играет: **{self.current_track}**"),
                        disnake.ui.Separator(),
                        disnake.ui.TextDisplay(f"-# Следующий трек: {next_info}"),
                        accent_colour=disnake.Color.from_hex(ColorStorage.main),
                    )
                    await self.channel.send(components=embed)
                except Exception:
                    self.logger.exception("ошибка отправки now-playing эмбеда")

            # ждём конца трека, но с heartbeat'ом, чтобы не зависнуть навсегда,
            # если коннект отвалится без вызова _after
            elapsed = 0.0
            while not done.is_set():
                try:
                    await asyncio.wait_for(done.wait(), timeout=self.HEARTBEAT_INTERVAL)
                except asyncio.TimeoutError:
                    elapsed += self.HEARTBEAT_INTERVAL
                    if not vc.is_connected():
                        self.logger.warning("vc отвалился во время трека — выхожу из play_loop")
                        try:
                            vc.stop()
                        except Exception:
                            pass
                        self._current_done = None
                        return
                    if elapsed >= self.MAX_TRACK_SECONDS:
                        self.logger.warning(
                            "трек %s играет дольше %ss — форсированно скипаю",
                            track, self.MAX_TRACK_SECONDS,
                        )
                        try:
                            vc.stop()
                        except Exception:
                            pass
                        break

            self._current_done = None

    @commands.command(name="очередь", aliases=["queue", "q"])
    async def musicQueue(self, command: disnake.MessageCommand):
        queue = "".join(
            f"{self._getTrackInfo(self.music_path / i)}\n"
            for i in self.music_files[:4]
        )
        embed = disnake.ui.Container(
            disnake.ui.TextDisplay(f"## 🎵 Текущий трек: {self.current_track}"),
            disnake.ui.TextDisplay(queue or "—"),
        )
        await command.reply(components=embed)

    def _is_expired(self):
        now = datetime.now().timestamp()
        if now - self.last_skip > 5 * 60:
            self.skip_votes = 0
            self.votes_list = []
            self.last_skip = 0

    @commands.command(name="следующий", aliases=["некст", "next", "skip", "скип", "ytrcn"])
    async def nextTrack(self, ctx: commands.Context):
        if ctx.channel.id != self.CHANNEL_ID:
            return
        if ctx.author not in ctx.channel.members:
            return
        if self.vc is None or not self.vc.is_connected():
            await ctx.reply("я сейчас не в голосовом канале", delete_after=5)
            return

        if len(ctx.channel.members) > 2:
            self._is_expired()
            listeners = len(ctx.channel.members)
            required_votes = self._requiredVotes(listeners)
            if ctx.author.id in self.votes_list:
                await ctx.reply("ты уже проголосовал(а) за пропуск песни!", delete_after=5)
                return

            self.skip_votes += 1
            if self.skip_votes >= required_votes:
                await ctx.channel.send(
                    f"{self.skip_votes} котика проголосовали за скип трека, пропускаем..."
                )
                self.vc.stop()
                self.votes_list = []
                self.last_skip = 0
                self.skip_votes = 0
            else:
                await ctx.channel.send(
                    f"{ctx.author.mention} проголосовал за пропуск песни! "
                    f"({self.skip_votes}/{required_votes})"
                )
                self.votes_list.append(ctx.author.id)
                self.last_skip = datetime.now().timestamp()
        else:
            self.vc.stop()

    @commands.command(name="radiostart")
    async def _radioForceStart(self, ctx):
        if self._started and self._task and not self._task.done():
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

    def _getTrackInfo(self, music_path) -> str:
        p = Path(music_path)
        try:
            tag = TinyTag.get(str(p))
            artist = tag.artist or "Unknown"
            title = tag.title or p.stem
            return f"{artist} - {title}"
        except Exception as e:
            self.logger.warning("не смог прочитать теги %s: %s", p.name, e)
            return p.stem

    def cog_unload(self):
        if self._task and not self._task.done():
            self._task.cancel()


def setup(bot: commands.Bot):
    bot.add_cog(CatcraftFM(bot))