import itertools

import numpy as np
import pytest

from celery_man.timing import MAX_WINDOW_S, TimingError, build_timing, plan_windows


def grid(bpm: float, bars: int, t0: float = 0.0, per_bar: int = 4) -> tuple[list[float], list[float]]:
    beat = 60.0 / bpm
    beats = [t0 + i * beat for i in range(bars * per_bar + 1)]
    return beats, beats[::per_bar]


def timing_for(beats, downbeats, sections=None):
    sections = sections or [{"start": 0.0, "end": downbeats[-1], "label": "1"}]
    return build_timing(beats, downbeats, downbeats[-1], sections, "abc123abc123")


def assert_tiles(windows, start, end):
    assert windows[0][0] == pytest.approx(start) and windows[-1][1] == pytest.approx(end)
    for (_, a_end, _), (b_start, _, _) in itertools.pairwise(windows):
        assert a_end == b_start
    assert all(e > s for s, e, _ in windows)


def test_windows_tile_range_at_120bpm():
    t = timing_for(*grid(120, 32))
    w = plan_windows(t)
    assert len(w) == 16
    assert all(e - s == pytest.approx(4.0) for s, e, _ in w)
    assert_tiles(w, 0.0, 64.0)


def test_windows_at_90bpm_fall_back_to_one_bar():
    t = timing_for(*grid(90, 8))
    w = plan_windows(t)
    assert len(w) == 8
    assert all(e - s == pytest.approx(2.667, abs=1e-3) for s, e, _ in w)
    assert_tiles(w, 0.0, t["downbeats"][-1])


def test_windows_split_slow_bars_at_beats():
    beats, downbeats = grid(40, 6)
    t = timing_for(beats, downbeats)
    w = plan_windows(t)
    assert all(e - s <= MAX_WINDOW_S for s, e, _ in w)
    edges = {round(x, 4) for s, e, _ in w for x in (s, e)}
    assert edges <= {round(b, 4) for b in beats}
    assert {round(d, 4) for d in downbeats} <= edges
    assert_tiles(w, 0.0, downbeats[-1])


def test_windows_never_cross_sections():
    beats, downbeats = grid(120, 9)  # 9 bars: a 2-bar greedy pass would straddle 6.0 s
    sections = [{"start": 0.0, "end": 6.0, "label": "A"}, {"start": 6.0, "end": 18.0, "label": "B"}]
    w = plan_windows(timing_for(beats, downbeats, sections))
    assert_tiles(w, 0.0, 18.0)
    assert all(not (s < 6.0 < e) for s, e, _ in w)
    assert [lab for s, _, lab in w if s < 6.0] == ["A", "A"]
    assert {lab for s, _, lab in w if s >= 6.0} == {"B"}


def test_tempo_change_is_reported():
    b1, d1 = grid(100, 8)
    b2, d2 = grid(130, 8, t0=b1[-1])
    beats, downbeats = b1 + b2[1:], d1 + d2[1:]
    t = timing_for(beats, downbeats)
    assert len(t["tempo"]) == 2
    assert t["tempo"][0][1] == pytest.approx(100, abs=0.5) and t["tempo"][1][1] == pytest.approx(130, abs=0.5)
    edges = {round(x, 4) for s, e, _ in plan_windows(t) for x in (s, e)}
    assert edges <= {round(d, 4) for d in downbeats}


def test_range_snaps_to_downbeats():
    t = timing_for(*grid(120, 16))
    w = plan_windows(t, 3.1, 20.9)  # nearest downbeats: 4.0 and 20.0
    assert_tiles(w, 4.0, 20.0)
    with pytest.raises(TimingError):
        plan_windows(t, 10.1, 10.4)  # both snap to 10.0


def test_low_confidence_raises():
    rng = np.random.default_rng(0)
    beats, downbeats = grid(120, 16)
    jittered = sorted(d + rng.uniform(-0.6, 0.6) if i else d for i, d in enumerate(downbeats))
    t = timing_for(beats, jittered)
    assert t["confidence"] < 0.8
    with pytest.raises(TimingError, match="steady pulse"):
        plan_windows(t)


@pytest.mark.slow
def test_detect_on_synthetic_drums(tmp_path):
    import soundfile as sf

    from celery_man.timing import detect

    sr, bpm, dur = 44100, 110, 20.0
    beat = 60 / bpm
    y = np.zeros(int(sr * dur))
    rng = np.random.default_rng(1)

    def hit(t: float, sig: np.ndarray) -> None:
        i = int(t * sr)
        n = min(len(sig), len(y) - i)
        y[i : i + n] += sig[:n]

    n = int(0.15 * sr)
    env = np.exp(-np.arange(n) / (0.03 * sr))
    kick = np.sin(2 * np.pi * 55 * np.arange(n) / sr) * env
    snare = rng.standard_normal(n) * env * 0.5
    hat = rng.standard_normal(n // 4) * env[: n // 4] * 0.15
    truth = np.arange(0, dur - 0.2, beat)
    for i, t in enumerate(truth):
        hit(t, kick if i % 4 == 0 else snare if i % 2 else np.zeros(1))
        hit(t, hat)
        hit(t + beat / 2, hat)
    song = tmp_path / "drums.wav"
    sf.write(song, y / np.abs(y).max() * 0.9, sr)

    t = detect(song)
    found = np.array(t["beats"])
    hits = sum(np.abs(found - b).min() <= 0.07 for b in truth)
    assert hits >= 0.9 * len(truth)
    assert np.median([bpm_ for _, bpm_ in t["tempo"]]) == pytest.approx(bpm, abs=2)
    assert len(t["song_sha"]) == 12 and t["duration"] == pytest.approx(dur, abs=0.05)
