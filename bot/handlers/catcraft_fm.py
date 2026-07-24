import asyncio
import logging
import math
import os
from pathlib import Path
from random import randint, shuffle

import disnake
from disnake.ext import commands
from tinytag import TinyTag

from bot.discord_config import Channels, Guilds
from bot.flag_system.flag_system import flags
from bot.storage import ColorStorage


class _MusicNavigator:
    def __init__(self, tracks=()):
        self.history: list[str] = []
        self.current: str | None = None
        self.upcoming: list[str] = list(tracks)

    def extend(self, tracks):
        self.upcoming.extend(tracks)

    def advance(self) -> str | None:
        if not self.upcoming:
            return None
        if self.current is not None:
            self.history.append(self.current)
        self.current = self.upcoming.pop(0)
        return self.current

    def back(self) -> str | None:
        if not self.history:
            return None
        if self.current is not None:
            self.upcoming.insert(0, self.current)
        self.current = self.history.pop()
        return self.current

    def peek(self, limit: int = 4) -> list[str]:
        return self.upcoming[:limit]


class CatcraftFM(commands.Cog):
    PANEL_FLAG = "fm_now_playing_message"
    PREVIOUS_BUTTON = "FM_PREVIOUS"
    NEXT_BUTTON = "FM_NEXT"
    INFO_BUTTON = "FM_INFO"

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

        self.navigator = _MusicNavigator()
        self.current_track: str = "—"
        self.current_track_path: str | None = None
        self.now_playing_message_id: int | None = None

        self.vc: disnake.VoiceClient | None = None
        self.channel: disnake.VoiceChannel | None = None

        # event текущего проигрываемого трека — нужен, чтобы on_voice_state_update
        # мог разбудить play_loop при внезапном дисконнекте
        self._current_done: asyncio.Event | None = None

        self.votes: dict[str, set[int]] = {
            "next": set(),
            "previous": set(),
        }
        self._pending_direction: str | None = None

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
        guild = self.bot.get_guild(Guilds.main)
        if guild is None:
            self.logger.error("guild %s не найден", Guilds.main)
            return

        self.channel = guild.get_channel(Channels.catcraft_fm)
        if self.channel is None:
            self.logger.error("channel %s не найден", Channels.catcraft_fm)
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

        self.logger.info("подключился к войс-каналу %s", Channels.catcraft_fm)

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

            # Голосование за направление важнее плановой вставки диктора.
            direction = self._pending_direction
            self._pending_direction = None

            # выбор следующего трека
            if direction is not None or music_count < self.random:
                if not self.navigator.upcoming:
                    # to_thread: диск VDS бывает медленным, event loop не блокируем
                    music_files = await asyncio.to_thread(os.listdir, self.music_path)
                    shuffle(music_files)
                    self.navigator.extend(music_files)
                if not self.navigator.upcoming:
                    self.logger.error("папка music пуста")
                    return
                if direction == "previous":
                    track = self.navigator.back()
                    music_count = 0
                    if track is None:
                        track = self.navigator.advance()
                else:
                    track = self.navigator.advance()
                assert track is not None
                path = self.music_path / track
                music_count += 1
                is_dictor = False
                self._on_music_track_changed()
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
                try:
                    callback_loop = asyncio.get_running_loop()
                except RuntimeError:
                    callback_loop = None
                if callback_loop is loop:
                    done.set()
                else:
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

                    await self._update_now_playing()
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

    async def _saved_panel_id(self) -> int | None:
        if self.now_playing_message_id is not None:
            return self.now_playing_message_id
        if self.channel is None:
            return None
        saved = await flags.getFlag(self.channel, self.PANEL_FLAG)
        if saved is None:
            return None
        try:
            self.now_playing_message_id = int(saved.value)
        except (TypeError, ValueError):
            self.logger.warning("Некорректный ID панели CatCraft FM: %r", saved.value)
            return None
        return self.now_playing_message_id

    async def _panel_components(self) -> list[disnake.ui.UIComponent]:
        upcoming = self.navigator.peek(1)
        next_info = (
            await asyncio.to_thread(self._getTrackInfo, self.music_path / upcoming[0])
            if upcoming
            else "—"
        )
        panel = disnake.ui.Container(
            disnake.ui.TextDisplay(f"🎵 Сейчас играет: **{self.current_track}**"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"-# Следующий трек: {next_info}"),
            accent_colour=disnake.Color.from_hex(ColorStorage.main),
        )
        controls = disnake.ui.ActionRow(
            disnake.ui.Button(
                style=disnake.ButtonStyle.secondary,
                label="Предыдущий",
                custom_id=self.PREVIOUS_BUTTON,
                disabled=not self.navigator.history,
            ),
            disnake.ui.Button(
                style=disnake.ButtonStyle.primary,
                label="Следующий",
                custom_id=self.NEXT_BUTTON,
            ),
            disnake.ui.Button(
                style=disnake.ButtonStyle.secondary,
                label="?",
                custom_id=self.INFO_BUTTON,
            ),
        )
        return [panel, controls]

    async def _update_now_playing(self) -> disnake.Message | None:
        if self.channel is None:
            return None
        components = await self._panel_components()
        message_id = await self._saved_panel_id()
        if message_id is not None:
            try:
                message = await self.channel.fetch_message(message_id)
                await message.edit(components=components)
                return message
            except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
                self.logger.warning(
                    "Панель CatCraft FM %s недоступна — создаю новую",
                    message_id,
                )

        message = await self.channel.send(components=components)
        self.now_playing_message_id = message.id
        await flags.setFlag(self.channel, self.PANEL_FLAG, message.id)
        return message

    async def _is_current_panel(self, interaction: disnake.MessageInteraction) -> bool:
        message_id = await self._saved_panel_id()
        return (
            message_id is not None
            and interaction.message is not None
            and interaction.message.id == message_id
        )

    async def _queue_components(
        self,
        *,
        include_description: bool = False,
    ) -> list[disnake.ui.UIComponent]:
        tracks = self.navigator.peek()
        infos = await asyncio.to_thread(
            lambda: [self._getTrackInfo(self.music_path / track) for track in tracks]
        )
        queue = "\n".join(
            f"{index}. {info}"
            for index, info in enumerate(infos, start=1)
        ) or "—"
        children: list[disnake.ui.ContainerChildUIComponent] = []
        if include_description:
            children.extend(
                [
                    disnake.ui.TextDisplay("## 📻 CatCraft FM"),
                    disnake.ui.TextDisplay(
                        "Музыка сервера играет круглосуточно. "
                        "Кнопки «Предыдущий» и «Следующий» работают голосованием."
                    ),
                    disnake.ui.Separator(),
                ]
            )
        children.extend(
            [
                disnake.ui.TextDisplay(f"## 🎵 Текущий трек: {self.current_track}"),
                disnake.ui.TextDisplay(f"**Дальше:**\n{queue}"),
            ]
        )
        return [
            disnake.ui.Container(
                *children,
                accent_colour=disnake.Color.from_hex(ColorStorage.main),
            )
        ]

    def _on_music_track_changed(self):
        self.votes = {"next": set(), "previous": set()}

    async def _vote(
        self,
        interaction: disnake.MessageInteraction,
        direction: str,
    ):
        if self._pending_direction is not None:
            await interaction.response.send_message(
                "Трек уже переключается.",
                ephemeral=True,
            )
            return

        listeners = self._human_listeners()
        if interaction.author.id not in {member.id for member in listeners}:
            await interaction.response.send_message(
                "Голосовать можно только из голосового канала CatCraft FM.",
                ephemeral=True,
            )
            return
        if self.vc is None or not self._vc_alive(self.vc):
            await interaction.response.send_message(
                "Я сейчас не подключён к CatCraft FM.",
                ephemeral=True,
            )
            return
        if direction == "previous" and not self.navigator.history:
            await interaction.response.send_message(
                "Предыдущего трека пока нет.",
                ephemeral=True,
            )
            return

        voters = self.votes[direction]
        if interaction.author.id in voters:
            await interaction.response.send_message(
                "Ты уже проголосовал(а) в эту сторону.",
                ephemeral=True,
            )
            return

        voters.add(interaction.author.id)
        required = self._requiredVotes(len(listeners))
        if len(voters) < required:
            await interaction.response.send_message(
                f"Голос принят: {len(voters)}/{required}.",
                ephemeral=True,
            )
            return

        self._pending_direction = direction
        try:
            self.vc.stop()
        except Exception:
            self.logger.exception("Не удалось остановить трек по голосованию")
            self._pending_direction = None
            voters.clear()
            await interaction.response.send_message(
                "Не смог переключить трек — попробуйте ещё раз.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Кворум собран, переключаю трек.",
            ephemeral=True,
        )

    @commands.Cog.listener("on_button_click")
    async def fmButtons(self, interaction: disnake.MessageInteraction):
        custom_id = interaction.component.custom_id or ""
        directions = {
            self.PREVIOUS_BUTTON: "previous",
            self.NEXT_BUTTON: "next",
        }
        if custom_id not in {*directions, self.INFO_BUTTON}:
            return
        if not await self._is_current_panel(interaction):
            return
        if custom_id == self.INFO_BUTTON:
            await interaction.response.send_message(
                components=await self._queue_components(include_description=True),
                ephemeral=True,
            )
            return
        await self._vote(interaction, directions[custom_id])

    @commands.command(name="очередь", aliases=["queue", "q"])
    async def musicQueue(self, command: disnake.MessageCommand):
        await command.reply(components=await self._queue_components())

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

    @staticmethod
    def _requiredVotes(listeners: int) -> int:
        if listeners <= 1:
            return 1
        if listeners == 2:
            return 2
        return math.ceil(listeners / 2)

    def _human_listeners(self) -> list[disnake.Member]:
        if self.channel is None:
            return []
        return [member for member in self.channel.members if not member.bot]

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
