"""ffmpeg/ffprobe-обвязка для «просмотра» видео основной моделью.

Hosted-Gemma принимает только текст и картинки (видео через compat-endpoint
не бывает), поэтому видео разбирается на равномерно выбранные кадры jpg
и скармливается зрению модели — нативно и бесплатно. Аудио бот не слушает.

Все вызовы — субпроцессы с таймаутом и временными файлами. Нет ffmpeg на машине —
функции честно возвращают []/None с warning, бот живёт дальше.
"""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger("robocat.media")

FFMPEG_BIN = "ffmpeg"
FFPROBE_BIN = "ffprobe"
FFMPEG_TIMEOUT = 60          # секунд на один вызов ffmpeg/ffprobe
MAX_VIDEO_FRAMES = 8


async def _run(args: list[str], timeout: int = FFMPEG_TIMEOUT) -> bytes | None:
    """Выполнить субпроцесс; stdout при rc=0, иначе None (все фейлы — в лог)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.warning("%s не найден — обработка медиа недоступна", args[0])
        return None
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        logger.error("%s превысил таймаут %dс", args[0], timeout)
        return None
    if proc.returncode != 0:
        logger.warning(
            "%s rc=%s: %s", args[0], proc.returncode,
            (stderr or b"")[-300:].decode(errors="replace"),
        )
        return None
    return stdout or b""


async def probe_duration(path: Path) -> float | None:
    """Длительность медиа-файла в секундах через ffprobe."""
    out = await _run([
        FFPROBE_BIN, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0", str(path),
    ])
    if not out:
        return None
    try:
        return float(out.decode().strip())
    except ValueError:
        return None


async def extract_frames(data: bytes, *, max_frames: int = MAX_VIDEO_FRAMES) -> tuple[list[bytes], float | None]:
    """Равномерно распределённые кадры видео (jpg) и его длительность.

    Кадров не больше max_frames независимо от длины ролика."""
    workdir = Path(tempfile.mkdtemp(prefix="robocat_media_"))
    try:
        src = workdir / "in.bin"
        src.write_bytes(data)
        duration = await probe_duration(src)
        # длительность неизвестна → консервативно считаем ролик минутным
        fps = max_frames / duration if duration and duration > max_frames else 1
        out = await _run([
            FFMPEG_BIN, "-y", "-i", str(src),
            "-vf", f"fps={fps:.4f}",
            "-frames:v", str(max_frames),
            "-q:v", "5",
            str(workdir / "frame_%02d.jpg"),
        ])
        if out is None:
            return [], duration
        frames = [p.read_bytes() for p in sorted(workdir.glob("frame_*.jpg"))]
        return frames[:max_frames], duration
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
