import asyncio
import os
from pathlib import Path
from random import randint

import disnake
from disnake.ext import commands


class CatcraftFM(commands.Cog):
    GUILD_ID = 1138425078493753366
    CHANNEL_ID = 1138425079483609224

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        base = Path(__file__).resolve().parents[2] / "data" / "catcraftfm"
        self.music_path = base / "music"
        self.dictor_path = base / "dictor"
        self._started = False
        self._task: asyncio.Task | None = None

    @commands.Cog.listener()
    async def on_ready(self):
        if self._started:
            return
        self._started = True
        self._task = asyncio.create_task(self._start_radio())

    async def _start_radio(self):
        guild = self.bot.get_guild(self.GUILD_ID)
        if guild is None:
            print("Guild not found")
            self._started = False
            return

        channel = guild.get_channel(self.CHANNEL_ID)
        if channel is None:
            print(f"Channel {self.CHANNEL_ID} not found")
            self._started = False
            return

        # Если каким-то чудом уже есть voice_client — отключаемся чисто
        if guild.voice_client is not None:
            await guild.voice_client.disconnect(force=True)

        try:
            vc = await channel.connect(timeout=10.0, reconnect=True)
        except Exception as e:
            print(f"Voice connect failed: {e}")
            self._started = False
            return

        try:
            await self._play_loop(vc)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Play loop crashed: {e}")
        finally:
            if vc.is_connected():
                await vc.disconnect(force=True)
            self._started = False  # позволяем перезапустить при следующем on_ready

    async def _play_loop(self, vc: disnake.VoiceClient):
        music_files: list[str] = []
        dictor_files: list[str] = []
        music_count = 0
        loop = asyncio.get_running_loop()

        while vc.is_connected():
            if music_count < 3:
                if not music_files:
                    music_files = sorted(os.listdir(self.music_path))
                if not music_files:
                    print("No music files")
                    return
                track = music_files.pop(0)
                path = self.music_path / track
                music_count += 1
            else:
                if not dictor_files:
                    dictor_files = os.listdir(self.dictor_path)
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
                vc.play(disnake.FFmpegPCMAudio(str(path)), after=_after)
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