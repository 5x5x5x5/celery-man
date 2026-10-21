"""Song timing: detect beats, downbeats and sections; plan beat-snapped shot windows."""

import hashlib
import itertools
import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MAX_WINDOW_S = 4.54
CONFIDENCE_MIN = 0.8
TEMPO_CHANGE = 0.03


class TimingError(ValueError):
    pass


def _r(x) -> float:
    return round(float(x), 4)


def _tempo(beats: list[float]) -> list[list[float]]:
    # "4-beat median": median of the 3 intervals spanned by 4 consecutive beats (odd count, so a step change gives one clean jump)
    iv = np.diff(beats)
    tempo: list[list[float]] = []
    for i in range(max(len(iv) - 2, 0)):
        bpm = 60.0 / float(np.median(iv[i : i + 3]))
        if not tempo or abs(bpm - tempo[-1][1]) > TEMPO_CHANGE * tempo[-1][1]:
            tempo.append([_r(beats[i]), round(bpm, 1)])
    return tempo


def _confidence(downbeats: list[float]) -> float:
    iv = np.diff(downbeats)
    if len(iv) < 2:
        return 0.0
    steady = np.abs(iv[1:] - iv[:-1]) <= 0.1 * iv[:-1]
    return round(float(steady.mean()), 4)


def build_timing(beats, downbeats, duration, sections, song_sha) -> dict:
    beats, downbeats = [_r(b) for b in beats], [_r(d) for d in downbeats]
    return {
        "duration": _r(duration),
        "beats": beats,
        "downbeats": downbeats,
        "tempo": _tempo(beats),
        "sections": [{"start": _r(s["start"]), "end": _r(s["end"]), "label": s["label"]} for s in sections],
        "confidence": _confidence(downbeats),
        "song_sha": song_sha,
    }


def _nearest(xs: list[float], t: float) -> float:
    return min(xs, key=lambda x: abs(x - t))


def plan_windows(
    timing: dict, start: float | None = None, end: float | None = None, max_s: float = MAX_WINDOW_S
) -> list[tuple[float, float, str]]:
    if timing["confidence"] < CONFIDENCE_MIN:
        raise TimingError(f"the song has no steady pulse (confidence {timing['confidence']:.2f} < {CONFIDENCE_MIN})")
    dbs, beats, sections = timing["downbeats"], timing["beats"], timing["sections"]
    start = dbs[0] if start is None else _nearest(dbs, start)
    end = dbs[-1] if end is None else _nearest(dbs, end)
    if end <= start:
        raise TimingError(f"empty range: {start} to {end} after snapping to downbeats")
    bars = [d for d in dbs if start <= d <= end]
    cuts = [s["start"] for s in sections]

    def label(t: float) -> str:
        return next((s["label"] for s in reversed(sections) if s["start"] <= t), sections[0]["label"] if sections else "")

    def crosses(a: float, b: float) -> bool:
        return any(a < c < b for c in cuts)

    windows: list[tuple[float, float, str]] = []
    i = 0
    while i < len(bars) - 1:
        a = bars[i]
        if i + 2 < len(bars) and bars[i + 2] - a <= max_s and not crosses(a, bars[i + 2]):
            ends, i = [bars[i + 2]], i + 2
        else:
            ends, i = _split_bar(a, bars[i + 1], beats, max_s), i + 1
        for e in ends:
            windows.append((a, e, label(a)))
            a = e
    return windows


def _split_bar(a: float, b: float, beats: list[float], max_s: float) -> list[float]:
    """End points that tile bar [a, b], each piece <= max_s and cut at the last beat that fits."""
    ends = []
    while b - a > max_s:
        fits = [x for x in beats if a < x < b and x - a <= max_s]
        if not fits:
            raise TimingError(f"a single beat at {a:.2f}s is longer than {max_s}s")
        a = fits[-1]
        ends.append(a)
    return ends + [b]


def _sections(y: np.ndarray, sr: int, beats: list[float], downbeats: list[float], duration: float) -> list[dict]:
    import librosa

    frames = librosa.time_to_frames(beats, sr=sr)
    feats = np.vstack([librosa.feature.chroma_cqt(y=y, sr=sr), librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)])
    feats = (feats - feats.mean(axis=1, keepdims=True)) / (feats.std(axis=1, keepdims=True) + 1e-9)
    synced = librosa.util.sync(feats, frames)  # column c starts at frame ([0] + frames)[c]
    k = max(2, round(duration / 30))
    starts = np.concatenate([[0], frames])
    bounds = librosa.frames_to_time(starts[librosa.segment.agglomerative(synced, min(k, synced.shape[1]))], sr=sr)
    # shortcut: labels are "1", "2", ... in order, not matched across repeats; upgrade if repeat-aware labels are needed
    cuts = sorted({_nearest(downbeats, t) for t in bounds[1:]} - {downbeats[0]})
    edges = [0.0, *cuts, duration]
    return [{"start": s, "end": e, "label": str(n + 1)} for n, (s, e) in enumerate(itertools.pairwise(edges))]


def detect(song: Path) -> dict:
    import librosa
    from beat_this.inference import File2Beats

    song = Path(song)
    beats, downbeats = File2Beats(checkpoint_path="final0", device="cpu", dbn=False)(str(song))
    beats, downbeats = [float(b) for b in beats], [float(d) for d in downbeats]
    if len(downbeats) < 2:
        raise TimingError(f"found {len(downbeats)} downbeats in {song.name}; the song has no steady pulse")
    y, sr = librosa.load(song, sr=22050, mono=True)
    duration = len(y) / sr
    sections = _sections(y, sr, beats, downbeats, duration)
    log.info("timing: %d beats, %d downbeats, %d sections in %.1fs", len(beats), len(downbeats), len(sections), duration)
    return build_timing(beats, downbeats, duration, sections, hashlib.sha256(song.read_bytes()).hexdigest()[:12])
