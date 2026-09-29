"""Серверная работа со звуком через ffmpeg: длительность, паузы, нарезка на куски.

Нужна, чтобы длинная запись распознавалась независимыми кусками: зацикливание
Whisper («вот так, вот так…») тогда не выходит за границу одного куска.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

_SILENCE = re.compile(r"silence_(start|end): (-?[\d.]+)")


class AudioError(RuntimeError):
    pass


async def _run(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise AudioError(f"{args[0]}: {err.decode(errors='replace')[-500:]}")
    return out.decode(errors="replace") + err.decode(errors="replace")


async def duration(path: Path) -> float | None:
    """Длительность в секундах; None, если ffprobe не смог её определить."""
    try:
        out = await _run(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        )
        return float(out.strip().splitlines()[0])
    except (AudioError, ValueError, IndexError):
        return None


async def silences(path: Path, noise_db: int = -35, min_len: float = 0.5) -> list[tuple[float, float]]:
    """Паузы [(начало, конец), …] по всей записи."""
    out = await _run(
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
        "-af", f"silencedetect=noise={noise_db}dB:d={min_len}", "-f", "null", "-",
    )
    result, start = [], None
    for kind, value in _SILENCE.findall(out):
        if kind == "start":
            start = float(value)
        elif start is not None:
            result.append((start, float(value)))
            start = None
    return result


def cut_points(total: float, pauses: list[tuple[float, float]], target: float, window: float = 90) -> list[float]:
    """Точки разреза ≈ каждые target секунд — по середине ближайшей паузы в пределах ±window.

    Последний кусок не делаем короче половины target: он приклеивается к предыдущему.
    """
    middles = [(a + b) / 2 for a, b in pauses]
    points: list[float] = []
    last = 0.0
    while total - last > target * 1.5:
        want = last + target
        near = [m for m in middles if abs(m - want) <= window and m > last]
        cut = min(near, key=lambda m: abs(m - want)) if near else want
        points.append(cut)
        last = cut
    return points


async def extract(path: Path, start: float, end: float, out: Path) -> Path:
    """Кусок [start, end) в 16 кГц моно Opus — формат, который Whisper и так приводит к себе."""
    await _run(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "32k", str(out),
    )
    return out
