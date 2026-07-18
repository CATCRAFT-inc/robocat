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
    # channel.connect() может зависнуть НАВСЕГДА: внутри disnake handshake есть
    # UDP ip-discovery (sock_recv) без таймаута, а параметр timeout= его не покрывает.
    # Потерянный UDP-пакет при ночном обслуживании войсов Discord = мёртвый супервизор.
    CONNECT_TIMEOUT = 60
    DISCONNECT_TIMEOUT = 10        # disconnect тоже ждёт сеть — не даём ему повесить нас
    MAX_PLAYBACK_ERRORS = 3        # столько треков подряд с ошибкой = коннект мёртв, реконнект
    BACKOFF_RESET_SECONDS = 300    # радио прожило дольше — считаем запуск удачным, сбрасываем backoff

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        base = Path(__file__).resolve().parents[2] / "data" / "catcraftfm"
        self.music_path = base / "music"
        self.dictor_path = base / "dictor"

        self._started = False
        self._task: asyncio.Task | None = None
        self._warned_no_runner = False

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

        self.random = 7

        self.logger = logging.getLogger("robocat.fm")

    @commands.Cog.listener()
    async def on_ready(self):
        if self._started:
            return
        self._started = True
        self._task = asyncio.create_task(self._radio_supervisor())
        self._task.add_done_callback(self._log_supervisor_done)
        self.logger.info("CatCraft FM: радио-супервизор запущен")

    def _log_supervisor_done(self, task: asyncio.Task):
        """Done-callback таски супервизора: тихая смерть фоновой таски обязана попасть в лог."""
        if task.cancelled():
            # штатная отмена (cog_unload / рестарт через !radiostart) — не тревога
            self.logger.info("радио-супервизор остановлен (отмена таски)")
            return
        self.logger.critical("радио-супервизор завершился: %r", task.exception())

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
                await self._force_disconnect(self.vc)

    async def _radio_supervisor(self):
        delay = self.RECONNECT_DELAY
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            try:
                await self._start_radio()
            except asyncio.CancelledError:
                self.logger.warning("супервизор отменён")
                return
            except Exception:
                self.logger.exception("_start_radio упал")
            if loop.time() - started > self.BACKOFF_RESET_SECONDS:
                delay = self.RECONNECT_DELAY
            self.logger.info("перезагружаемся через %ss...", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.MAX_RECONNECT_DELAY)

    async def _force_disconnect(self, vc: disnake.VoiceProtocol | None):
        """Отключить voice client, не дав ему повесить супервизора.

        disconnect() внутри ждёт сеть и может зависнуть так же, как connect().
        Если не получилось — vc.cleanup() снимает клиент с регистрации в disnake,
        иначе следующий connect() упадёт с "Already connected to a voice channel".
        """
        if vc is None:
            return
        try:
            await asyncio.wait_for(vc.disconnect(force=True), timeout=self.DISCONNECT_TIMEOUT)
        except Exception:
            self.logger.exception("disconnect завис/упал — чищу клиент вручную")
            # cleanup() снимает клиент с регистрации, но НЕ гасит poll-таск диснейка:
            # живой _runner продолжил бы внутренние реконнекты и воевал с новым клиентом
            runner = getattr(vc, "_runner", None)
            if isinstance(runner, asyncio.Task) and not runner.done():
                runner.cancel()
            try:
                vc.cleanup()
            except Exception:
                self.logger.exception("cleanup тоже упал")

    def _vc_alive(self, vc: disnake.VoiceClient) -> bool:
        """is_connected() врёт, если внутренний poll-таск disnake умер с необработанной
        ошибкой: _connected остаётся выставленным, а сокет мёртв (радио «в канале, но молчит»).
        Единственный видимый признак такого зомби — завершившийся _runner."""
        if not vc.is_connected():
            return False
        runner = getattr(vc, "_runner", None)
        if not isinstance(runner, asyncio.Task):
            # незнакомые внутренности (переименовали в новом disnake?) —
            # доверяем is_connected(), но не молча: зомби-детект выключен
            if not self._warned_no_runner:
                self._warned_no_runner = True
                self.logger.warning("_runner не найден — зомби-детект коннекта отключён")
            return True
        if runner.done():
            self.logger.warning("внутренний voice-runner disnake умер — коннект зомби")
            return False
        return True

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
            await self._force_disconnect(guild.voice_client)

        try:
            # wait_for снаружи обязателен: timeout=10.0 диснейка НЕ покрывает
            # весь handshake (см. комментарий у CONNECT_TIMEOUT)
            self.vc = await asyncio.wait_for(
                self.channel.connect(timeout=10.0, reconnect=True),
                timeout=self.CONNECT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            self.logger.error(
                "connect не завершился за %ss — хендшейк завис, добиваю клиент и ретраюсь",
                self.CONNECT_TIMEOUT,
            )
            # при отмене connect диснейк НЕ снимает полусозданный клиент с регистрации
            await self._force_disconnect(guild.voice_client)
            return
        except Exception:
            self.logger.exception("не удалось подключиться к каналу")
            await self._force_disconnect(guild.voice_client)
            return

        self.logger.info("подключился к войс-каналу %s", self.CHANNEL_ID)

        try:
            await self._play_loop(self.vc)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("play loop crash")
        finally:
            await self._force_disconnect(self.vc)
            self._current_done = None

    async def _play_loop(self, vc: disnake.VoiceClient):
        dictor_files: list[str] = []
        music_count = 0
        playback_errors = 0
        loop = asyncio.get_running_loop()

        while True:
            if not self._vc_alive(vc):
                self.logger.warning("vc не живой — выхожу из play_loop")
                return

            # выбор следующего трека
            if music_count < self.random:
                if not self.music_files:
                    # to_thread: диск VDS бывает медленным, event loop не блокируем
                    self.music_files = await asyncio.to_thread(os.listdir, self.music_path)
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
                    dictor_files = await asyncio.to_thread(os.listdir, self.dictor_path)
                    shuffle(dictor_files)
                if not dictor_files:
                    music_count = 0
                    continue
                track = dictor_files.pop(randint(0, len(dictor_files) - 1))
                path = self.dictor_path / track
                music_count = 0
                is_dictor = True
                self.random = randint(5,15)

            done = asyncio.Event()
            self._current_done = done
            play_result: dict = {"error": None}

            def _after(error: Exception | None, track=track, done=done, play_result=play_result):
                if error is not None:
                    self.logger.error("ошибка плейбека в %s: %s", track, error)
                play_result["error"] = error
                loop.call_soon_threadsafe(done.set)

            try:
                vc.play(disnake.FFmpegPCMAudio(str(path)), after=_after)
            except Exception:
                self.logger.exception("не смог запустить трек %s", track)
                self._current_done = None
                # синхронный фейл play — та же серия ошибок, что и фейл в _after:
                # иначе мёртвый коннект крутится тут вечно, минуя реконнект
                playback_errors += 1
                if playback_errors >= self.MAX_PLAYBACK_ERRORS:
                    self.logger.warning(
                        "%s подряд неудачных запусков трека — полный реконнект",
                        playback_errors,
                    )
                    return
                await asyncio.sleep(1)
                continue

            # отправка now-playing эмбеда (только для музыки; трек, мгновенно
            # упавший в _after, не анонсируем — при мёртвом сокете это флудило
            # бы канал ложными «Сейчас играет» каждый цикл реконнекта)
            if not is_dictor and play_result["error"] is None:
                try:
                    # TinyTag читает файл с диска — в поток, не в event loop
                    self.current_track = await asyncio.to_thread(self._getTrackInfo, path)
                    self.current_track_path = str(path)

                    if self.music_files:
                        next_info = await asyncio.to_thread(
                            self._getTrackInfo, self.music_path / self.music_files[0]
                        )
                    else:
                        next_info = "—"
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
                    if not self._vc_alive(vc):
                        self.logger.warning("vc отвалился во время трека — выхожу из play_loop")
                        try:
                            vc.stop()
                        except Exception:
                            self.logger.exception("vc.stop() упал при выходе из play_loop после отвала vc")
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
                            self.logger.exception("vc.stop() упал при форс-скипе трека %s", track)
                        break

            self._current_done = None

            # Мёртвый сокет при живом (на вид) коннекте: каждый трек мгновенно
            # завершается с ошибкой. Несколько подряд — коннект не жилец, реконнект.
            if play_result["error"] is not None:
                playback_errors += 1
                if playback_errors >= self.MAX_PLAYBACK_ERRORS:
                    self.logger.warning(
                        "%s треков подряд с ошибкой плейбека — полный реконнект",
                        playback_errors,
                    )
                    return
            else:
                playback_errors = 0

    @commands.command(name="очередь", aliases=["queue", "q"])
    async def musicQueue(self, command: disnake.MessageCommand):
        tracks = list(self.music_files[:4])
        infos = await asyncio.to_thread(
            lambda: [self._getTrackInfo(self.music_path / i) for i in tracks]
        )
        queue = "".join(f"{info}\n" for info in infos)
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
        if self.vc is None or not self._vc_alive(self.vc):
            await ctx.reply("я сейчас не в голосовом канале", delete_after=5)
            return

        if len(ctx.channel.members) > 2:
            self._is_expired()
            listeners = len(ctx.channel.members)
            required_votes = self._requiredVotes(listeners)
            if ctx.author.id in self.votes_list:
                await ctx.reply("ты уже проголосовал(а) за пропуск песни!", delete_after=5)
                return

            # голос фиксируем ДО await: быстрый повтор команды иначе успевал
            # пройти проверку выше и посчитаться несколько раз
            self.votes_list.append(ctx.author.id)
            self.skip_votes += 1
            self.last_skip = datetime.now().timestamp()
            if self.skip_votes >= required_votes:
                # сброс состояния и stop() ДО await: второй голос, пришедший во
                # время send, иначе тоже проходил кворум и стопил уже новый трек
                votes = self.skip_votes
                self.votes_list = []
                self.last_skip = 0
                self.skip_votes = 0
                self.vc.stop()
                await ctx.channel.send(
                    f"{votes} котика проголосовали за скип трека, пропускаем..."
                )
            else:
                await ctx.channel.send(
                    f"{ctx.author.mention} проголосовал за пропуск песни! "
                    f"({self.skip_votes}/{required_votes})"
                )
        else:
            self.vc.stop()

    @commands.command(name="radiostart")
    async def _radioForceStart(self, ctx):
        if self._started and self._task and not self._task.done():
            await ctx.reply("уже", delete_after=5)
            return
        await ctx.reply("угу", delete_after=5)
        self._started = True
        self.logger.info("радио перезапущено вручную (!radiostart) пользователем %s", ctx.author.id)
        self._task = asyncio.create_task(self._radio_supervisor())
        self._task.add_done_callback(self._log_supervisor_done)

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
            self.logger.info("CatCraft FM: ког выгружается, останавливаю радио-супервизор")
            self._task.cancel()


def setup(bot: commands.Bot):
    bot.add_cog(CatcraftFM(bot))