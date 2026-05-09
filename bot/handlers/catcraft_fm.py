import asyncio
import os

from pathlib import Path
from random import randint, shuffle
from tinytag import TinyTag

import disnake
from disnake.ext import commands

from bot.storage import ColorStorage


class CatcraftFM(commands.Cog):
    GUILD_ID = 1138425078493753366
    CHANNEL_ID = 1502616927695015986

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

    @commands.Cog.listener()
    async def on_ready(self):
        if self._started:
            return
        self._started = True
        self._task = asyncio.create_task(self._start_radio())

    @commands.command(name="очередь")
    async def musicQueue(self, command: disnake.MessageCommand):
        print(TinyTag.get(str(self.current_track_path)))
        queue = ''.join([f"{self._getTrackInfo(self.music_path / i)}\n" for i in self.music_files[:4]])
        queue_message = f"**-> {self.current_track}**\n{queue}"
        embed = disnake.ui.Container(
                disnake.ui.TextDisplay(f"## 🎵 Текущий трек: {self.current_track}"),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay(queue)
            )
        await command.reply(components=embed)

    @commands.command(name='следующий', aliases=['некст', 'next'])
    async def nextTrack(self, ctx: disnake.MessageCommand):
        self.vc.stop()

    def _getTrackInfo(self, music_path: Path):
        tag: TinyTag = TinyTag.get(str(music_path))
        artist, title = tag.artist, tag.title
        return " - ".join([artist, title])

    async def _start_radio(self):
        guild = self.bot.get_guild(self.GUILD_ID)
        if guild is None:
            print("Guild not found")
            self._started = False
            return

        self.channel = guild.get_channel(self.CHANNEL_ID)
        if self.channel is None:
            print(f"Channel {self.CHANNEL_ID} not found")
            self._started = False
            return

        # Если каким-то чудом уже есть voice_client — отключаемся чисто
        if guild.voice_client is not None:
            await guild.voice_client.disconnect(force=True)

        try:
            self.vc = await self.channel.connect(timeout=10.0, reconnect=True)
        except Exception as e:
            print(f"Voice connect failed: {e}")
            self._started = False
            return

        try:
            await self._play_loop(self.vc)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Play loop crashed: {e}")
        finally:
            if self.vc.is_connected():
                await self.vc.disconnect(force=True)
            self._started = False  # позволяем перезапустить при следующем on_ready

    async def _play_loop(self, vc: disnake.VoiceClient):
        music_files: list[str] = []
        dictor_files: list[str] = []
        music_count = 0
        loop = asyncio.get_running_loop()

        while self.vc.is_connected():
            if music_count < 3:
                if not self.music_files:
                    self.music_files = os.listdir(self.music_path)
                    shuffle(self.music_files)
                if not self.music_files:
                    print("No music files")
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
                    print(f"Playback error on {track}: {error}")
                loop.call_soon_threadsafe(done.set)

            try:
                self.vc.play(disnake.FFmpegPCMAudio(str(path)), after=_after)
                self.current_track(self._getTrackInfo(str(path)))
                embed = disnake.ui.Container(
                    disnake.ui.TextDisplay(f"🎵 Сейчас играет: **{self.current_track}**"),
                    disnake.ui.Separator(),
                    disnake.ui.TextDisplay(f"-# Следующий трек: {self._getTrackInfo(self.music_path / self.music_files[0])}"),
                    accent_colour=disnake.Color.from_hex(ColorStorage.main)
                )
                await self.channel.send(components=embed)
            except Exception as e:
                print(f"Failed to start playback for {track}: {e}")
                await asyncio.sleep(1)
                continue

            await done.wait()

    def cog_unload(self):
        if self._task and not self._task.done():
            self._task.cancel()


def setup(bot: commands.Bot):
    bot.add_cog(CatcraftFM(bot))