"""Audio normalization helpers (ffmpeg) and simple energy VAD."""
from __future__ import annotations

import hashlib
import array
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .hardware import ffmpeg_available


def fingerprint_file(path: Path, extra: str = "") -> str:
    h = hashlib.sha256()
    h.update(extra.encode("utf-8"))
    with path.open("rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()[:32]


def normalize_to_wav(
    media: Path,
    dest: Path,
    *,
    sample_rate: int = 16000,
    progress: Optional[Any] = None,
) -> Path:
    """Decode/normalize to 16 kHz mono PCM WAV via ffmpeg."""
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg is required for audio normalization")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(media),
        "-ac", "1", "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not dest.exists():
        raise RuntimeError(f"ffmpeg normalize failed: {(proc.stderr or '')[-400:]}")
    if progress:
        progress(1.0)
    return dest


def read_wav_pcm(path: Path) -> Tuple[int, bytes]:
    with wave.open(str(path), "rb") as w:
        assert w.getsampwidth() == 2
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
        if w.getnchannels() > 1:
            # Downmix rare case.
            samples = array.array("h")
            samples.frombytes(frames)
            mono = array.array("h")
            ch = w.getnchannels()
            for i in range(0, len(samples), ch):
                mono.append(int(sum(samples[i:i + ch]) / ch))
            frames = mono.tobytes()
        return rate, frames


def wav_duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate() or 1)


def energy_vad_segments(
    path: Path,
    *,
    frame_ms: int = 30,
    silence_ms: int = 400,
    threshold_ratio: float = 0.02,
) -> List[Dict[str, float]]:
    """Simple RMS energy VAD returning speech regions [{start,end}, ...]."""
    rate, pcm = read_wav_pcm(path)
    samples = array.array("h")
    samples.frombytes(pcm)
    if not samples:
        return []
    frame = max(1, int(rate * frame_ms / 1000))
    silence_frames = max(1, int(silence_ms / frame_ms))
    # Peak-normalized threshold
    peak = max(abs(s) for s in samples) or 1
    thr = peak * threshold_ratio
    voiced = []
    for i in range(0, len(samples), frame):
        chunk = samples[i:i + frame]
        if not chunk:
            break
        rms = (sum(s * s for s in chunk) / len(chunk)) ** 0.5
        voiced.append(rms >= thr)

    regions: List[Dict[str, float]] = []
    i = 0
    n = len(voiced)
    while i < n:
        while i < n and not voiced[i]:
            i += 1
        if i >= n:
            break
        start_i = i
        silent_run = 0
        while i < n:
            if voiced[i]:
                silent_run = 0
            else:
                silent_run += 1
                if silent_run >= silence_frames:
                    break
            i += 1
        end_i = i - silent_run
        start = start_i * frame / rate
        end = max(start + 0.05, end_i * frame / rate)
        regions.append({"start": start, "end": end})
    if not regions:
        # Whole file as one region so chunking still works.
        regions = [{"start": 0.0, "end": len(samples) / rate}]
    return regions


def slice_wav(src: Path, dest: Path, start_s: float, end_s: float) -> Path:
    rate, pcm = read_wav_pcm(src)
    start = max(0, int(start_s * rate))
    end = max(start + 1, int(end_s * rate))
    samples = array.array("h")
    samples.frombytes(pcm)
    chunk = samples[start:end]
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(chunk.tobytes())
    return dest


def preprocess_media(
    media: Path,
    work_dir: Path,
    *,
    sample_rate: int = 16000,
) -> Dict[str, Any]:
    """Normalize media and run VAD. Returns paths + speech regions."""
    work_dir.mkdir(parents=True, exist_ok=True)
    wav = work_dir / "audio_16k.wav"
    normalize_to_wav(media, wav, sample_rate=sample_rate)
    regions = energy_vad_segments(wav)
    return {
        "wav": str(wav),
        "duration_s": wav_duration_s(wav),
        "vad_regions": regions,
        "sample_rate": sample_rate,
        "fingerprint": fingerprint_file(wav),
    }
