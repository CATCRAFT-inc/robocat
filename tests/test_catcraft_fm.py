"""Тесты живучести CatCraft FM: зависший connect, зомби-коннект, ошибки плейбека.

Discord/voice мокается целиком — сети нет. Проверяем три режима смерти радио,
из-за которых оно «ночью выключается и не играет»:
1. channel.connect() виснет навсегда (UDP ip-discovery в disnake без таймаута);
2. внутренний poll-таск disnake умер, is_connected() врёт True (зомби);
3. каждый трек мгновенно падает с ошибкой — мёртвый сокет при живом коннекте.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.handlers.catcraft_fm import CatcraftFM, _MusicNavigator


@pytest.fixture
def cog():
    bot = MagicMock()
    instance = CatcraftFM(bot)
    # ускоряем тесты: таймауты в сотые секунды
    instance.CONNECT_TIMEOUT = 0.05
    instance.DISCONNECT_TIMEOUT = 0.05
    instance.HEARTBEAT_INTERVAL = 0.01
    return instance


def _make_vc(connected: bool = True, runner_done: bool = False):
    vc = MagicMock()
    vc.is_connected.return_value = connected
    vc.disconnect = AsyncMock()
    vc.cleanup = MagicMock()
    runner = MagicMock(spec=asyncio.Task)
    runner.done.return_value = runner_done
    vc._runner = runner
    return vc


# -------- очередь и кворум --------


def test_back_requeues_current_track():
    navigator = _MusicNavigator(["A", "B", "C"])

    assert navigator.advance() == "A"
    assert navigator.advance() == "B"
    assert navigator.back() == "A"
    assert navigator.advance() == "B"


@pytest.mark.parametrize(
    ("listeners", "votes"),
    [(0, 1), (1, 1), (2, 2), (3, 2), (4, 2), (5, 3)],
)
def test_required_votes(listeners, votes):
    assert CatcraftFM._requiredVotes(listeners) == votes


def test_human_listeners_excludes_bots(cog):
    human = MagicMock(bot=False)
    bot = MagicMock(bot=True)
    cog.channel = MagicMock(members=[human, bot])

    assert cog._human_listeners() == [human]


# -------- _vc_alive --------


def test_vc_alive_connected_with_live_runner_is_true(cog):
    assert cog._vc_alive(_make_vc(connected=True, runner_done=False)) is True


def test_vc_alive_disconnected_is_false(cog):
    assert cog._vc_alive(_make_vc(connected=False)) is False


def test_vc_alive_dead_runner_is_false(cog):
    # is_connected() True, но poll-таск disnake умер — это зомби-коннект
    assert cog._vc_alive(_make_vc(connected=True, runner_done=True)) is False


def test_vc_alive_without_runner_attribute_trusts_is_connected(cog):
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc._runner = None  # незнакомые внутренности disnake — деградируем мягко
    assert cog._vc_alive(vc) is True


# -------- _force_disconnect --------


async def test_force_disconnect_none_is_noop(cog):
    await cog._force_disconnect(None)  # не должно упасть


async def test_force_disconnect_calls_disconnect(cog):
    vc = _make_vc()
    await cog._force_disconnect(vc)
    vc.disconnect.assert_awaited_once_with(force=True)
    vc.cleanup.assert_not_called()


async def test_force_disconnect_hanging_disconnect_falls_back_to_cleanup(cog):
    vc = _make_vc()

    async def _hang(force=False):
        await asyncio.sleep(60)

    vc.disconnect = _hang
    await cog._force_disconnect(vc)  # не виснет благодаря wait_for
    vc.cleanup.assert_called_once()
    # живой poll-таск диснейка обязан быть погашен, иначе его внутренний
    # reconnect будет воевать с новым клиентом
    vc._runner.cancel.assert_called_once()


# -------- _start_radio: зависший connect --------


async def test_start_radio_hanging_connect_times_out_and_cleans_up(cog):
    """Главный ночной сценарий: connect завис → супервизор НЕ блокируется навечно."""
    stale_vc = _make_vc()

    channel = MagicMock()

    async def _hanging_connect(timeout=None, reconnect=None):
        # disnake регистрирует полусозданный клиент ещё до завершения connect
        guild.voice_client = stale_vc
        await asyncio.sleep(60)

    channel.connect = _hanging_connect

    guild = MagicMock()
    guild.get_channel.return_value = channel
    guild.voice_client = None
    cog.bot.get_guild.return_value = guild

    await asyncio.wait_for(cog._start_radio(), timeout=5)  # завершился, не завис

    # полусозданный клиент добит, чтобы следующий connect не упал с "Already connected"
    stale_vc.disconnect.assert_awaited_once_with(force=True)


async def test_start_radio_connect_exception_returns_without_crash(cog):
    channel = MagicMock()
    channel.connect = AsyncMock(side_effect=RuntimeError("boom"))
    guild = MagicMock()
    guild.get_channel.return_value = channel
    guild.voice_client = None
    cog.bot.get_guild.return_value = guild

    await asyncio.wait_for(cog._start_radio(), timeout=5)  # проглотил и вернулся


# -------- _play_loop: подряд идущие ошибки плейбека --------


async def test_play_loop_consecutive_playback_errors_exit_for_reconnect(cog, tmp_path, monkeypatch):
    # не спавним реальный ffmpeg-процесс
    monkeypatch.setattr("disnake.FFmpegPCMAudio", lambda path: MagicMock())
    for i in range(5):
        (tmp_path / f"track{i}.mp3").write_bytes(b"not really audio")
    cog.music_path = tmp_path
    cog.channel = MagicMock()
    cog.channel.send = AsyncMock()

    vc = _make_vc()
    play_attempts = []

    def _instant_error_play(source, after=None):
        # плеер мгновенно умирает с ошибкой — так выглядит мёртвый сокет
        play_attempts.append(1)
        after(RuntimeError("socket is dead"))

    vc.play = _instant_error_play
    vc.stop = MagicMock()

    await asyncio.wait_for(cog._play_loop(vc), timeout=5)  # вышел сам, не крутится вечно
    # ровно MAX_PLAYBACK_ERRORS попыток — ни вечного цикла, ни реконнекта с первой ошибки
    assert len(play_attempts) == cog.MAX_PLAYBACK_ERRORS
    # флуд-гард: ни одного «Сейчас играет» для мгновенно упавших треков
    cog.channel.send.assert_not_awaited()


async def test_play_loop_exits_when_vc_is_zombie(cog, tmp_path):
    (tmp_path / "track.mp3").write_bytes(b"not really audio")
    cog.music_path = tmp_path

    vc = _make_vc(connected=True, runner_done=True)  # зомби с порога
    await asyncio.wait_for(cog._play_loop(vc), timeout=5)
    vc.play.assert_not_called()
