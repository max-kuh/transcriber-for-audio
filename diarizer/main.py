"""Сервис диаризации: кто и когда говорит. sherpa-onnx, работает на CPU.

POST /diarize  (multipart: file, num_speakers?)  ->  {"turns": [{start, end, speaker}], "speakers": N}
GET  /health
"""
import asyncio
import os
import subprocess

import numpy as np
import sherpa_onnx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

MODELS = os.getenv("DIARIZER_MODELS", "/models")
THREADS = int(os.getenv("DIARIZER_THREADS", "4"))
# Порог кластеризации при неизвестном числе говорящих: выше — меньше спикеров
THRESHOLD = float(os.getenv("DIARIZER_THRESHOLD", "0.5"))
MIN_ON = float(os.getenv("DIARIZER_MIN_ON", "0.3"))
MIN_OFF = float(os.getenv("DIARIZER_MIN_OFF", "0.5"))

app = FastAPI(title="Diarizer")
_lock = asyncio.Lock()  # диаризатор один, запросы идут по очереди


def build(num_speakers: int) -> sherpa_onnx.OfflineSpeakerDiarization:
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=f"{MODELS}/sherpa-onnx-pyannote-segmentation-3-0/model.onnx"
            ),
            num_threads=THREADS,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=f"{MODELS}/embedding.onnx", num_threads=THREADS
        ),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers, threshold=THRESHOLD),
        min_duration_on=MIN_ON,
        min_duration_off=MIN_OFF,
    )
    if not config.validate():
        raise RuntimeError("некорректная конфигурация диаризатора")
    return sherpa_onnx.OfflineSpeakerDiarization(config)


def decode(path: str, rate: int) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def run(path: str, num_speakers: int) -> list[dict]:
    sd = build(num_speakers)
    samples = decode(path, sd.sample_rate)
    result = sd.process(samples).sort_by_start_time()
    return [{"start": r.start, "end": r.end, "speaker": r.speaker} for r in result]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/diarize")
async def diarize(file: UploadFile = File(...), num_speakers: int = Form(-1)) -> dict:
    # на диск, чтобы ffmpeg мог читать произвольный контейнер
    tmp = f"/tmp/{os.urandom(8).hex()}"
    try:
        with open(tmp, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
        async with _lock:
            turns = await asyncio.to_thread(run, tmp, num_speakers if num_speakers > 0 else -1)
    except subprocess.CalledProcessError as exc:
        raise HTTPException(422, f"не удалось декодировать аудио: {exc.stderr.decode(errors='replace')[-300:]}")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return {"turns": turns, "speakers": len({t["speaker"] for t in turns})}
