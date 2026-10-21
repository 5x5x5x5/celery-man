"""Audio features: per-video-frame loudness and band energy, and the per-frame data a scene reads."""

import hashlib
import logging
from bisect import bisect_right
from pathlib import Path

import librosa
import numpy as np

from celery_man.generate import FPS
from celery_man.project import Project, read_json, write_json

log = logging.getLogger(__name__)

SR, N_FFT = 24000, 2048
BANDS = {"low": (0, 250), "mid": (250, 2000), "high": (2000, None)}
EPS = 1e-9


def _norm(x: np.ndarray) -> list[float]:
    top = np.percentile(x, 99)
    return np.round(np.clip(x / top, 0, 1) if top > 0 else np.zeros_like(x), 3).tolist()


def compute(song: Path, fps: int = FPS) -> dict:
    """Per-frame level/low/mid/high for the whole song, each 0..1; hop = SR // fps so frames line up with video frames."""
    y, _ = librosa.load(song, sr=SR, mono=True)
    hop = SR // fps
    mag = np.abs(librosa.stft(y, n_fft=N_FFT, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=SR, n_fft=N_FFT)
    out = {
        "song_sha": hashlib.sha256(song.read_bytes()).hexdigest()[:12],
        "fps": fps,
        "level": _norm(librosa.feature.rms(y=y, frame_length=N_FFT, hop_length=hop)[0]),
    }
    for name, (lo, hi) in BANDS.items():
        out[name] = _norm(mag[(freqs >= lo) & (freqs < (hi or np.inf))].sum(axis=0))
    return out


def ensure(p: Project) -> dict:
    """Load features.json, recomputing when it is missing or was made from a different song."""
    from celery_man.steps import song_sha

    feats = read_json(p.features_path)
    if feats is None or feats.get("song_sha") != song_sha(p):
        feats = compute(p.song, FPS)
        write_json(p.features_path, feats)
    return feats


def _phase(marks: list[float], t: float) -> tuple[int, float]:
    """(index of the last mark at or before t, progress to the next mark); the last interval repeats after the final mark."""
    i = bisect_right(marks, t + EPS) - 1
    if i < 0 or len(marks) < 2:
        return 0, 0.0
    nxt = marks[i + 1] if i + 1 < len(marks) else marks[i] + (marks[i] - marks[i - 1])
    return i, min((t - marks[i]) / (nxt - marks[i]), 1.0)


def _hit(marks: list[float], t: float, fps: int) -> bool:
    i = bisect_right(marks, t + EPS) - 1
    return i >= 0 and marks[i] > t - 1 / fps + EPS


def frame_data(timing: dict, feats: dict, start: float, end: float, fps: int = FPS) -> list[dict]:
    """One dict per frame of the shot [start, end), using the same frame count as assemble."""
    i0 = round(start * fps)
    n = round(end * fps) - i0
    beats, downbeats = timing["beats"], timing["downbeats"]
    frames = []
    for k in range(n):
        t = (i0 + k) / fps
        beat, beat_phase = _phase(beats, t)
        bar, bar_phase = _phase(downbeats, t)
        frames.append(
            {
                "t": k / fps,
                "song_t": t,
                "progress": k / (n - 1) if n > 1 else 1.0,
                "beat": beat,
                "beat_phase": beat_phase,
                "bar": bar,
                "bar_phase": bar_phase,
                "on_beat": _hit(beats, t, fps),
                "on_downbeat": _hit(downbeats, t, fps),
                **{key: feats[key][min(i0 + k, len(feats[key]) - 1)] for key in ("level", "low", "mid", "high")},
            }
        )
    return frames
