import subprocess

import numpy as np

from celery_man import features
from celery_man.features import compute, ensure, frame_data
from celery_man.project import Project

FPS = 24


def timing(beat=0.5, bar=2.0, dur=20.0, first=0.0):
    return {"beats": [first + i * beat for i in range(int(dur / beat))], "downbeats": [i * bar for i in range(int(dur / bar))]}


def flat(n=2000):
    return {k: [i / 1000 for i in range(n)] for k in ("level", "low", "mid", "high")}


def test_frame_count_matches_assemble_formula():
    fd = frame_data(timing(), flat(), 1.01, 3.5, FPS)
    assert len(fd) == round(3.5 * 24) - round(1.01 * 24)
    assert fd[0]["t"] == 0 and fd[0]["song_t"] == round(1.01 * 24) / 24
    assert fd[-1]["progress"] == 1


def test_beat_phase_and_flags():
    fd = frame_data(timing(), flat(), 0, 10, FPS)
    assert [i for i, f in enumerate(fd[:50]) if f["on_beat"]] == [0, 12, 24, 36, 48]
    assert [i for i, f in enumerate(fd) if f["on_downbeat"]] == [0, 48, 96, 144, 192]
    assert fd[6]["beat_phase"] == 0.5 and fd[6]["bar_phase"] == 0.125
    assert fd[12]["beat"] == 1


def test_before_first_beat_is_phase_zero():
    fd = frame_data(timing(first=0.3), flat(), 0, 1, FPS)
    early = [f for f in fd if f["song_t"] < 0.3]
    assert early and all(f["beat"] == 0 and f["beat_phase"] == 0 and not f["on_beat"] for f in early)


def test_after_last_beat_extends_last_interval():
    t = {"beats": [0.0, 0.5], "downbeats": [0.0, 2.0]}
    f = frame_data(t, flat(), 0.75, 1.0, FPS)[0]
    assert f["beat_phase"] == 0.5


def test_levels_index_by_song_frame():
    fd = frame_data(timing(), flat(), 2.0, 3.0, FPS)
    assert [f["level"] for f in fd[:3]] == [(48 + k) / 1000 for k in range(3)]
    short = {k: [0.1, 0.9] for k in ("level", "low", "mid", "high")}
    assert frame_data(timing(), short, 2.0, 2.2, FPS)[-1]["high"] == 0.9


def test_compute_on_tone(tmp_path):
    wav = tmp_path / "t.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=2", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-filter_complex",
         "[1]atrim=0:2[s];[0][s]concat=n=2:v=0:a=1", str(wav)],
        check=True,
    )  # fmt: skip
    f = compute(wav, FPS)
    assert abs(len(f["level"]) - 4 * 24) <= 1
    assert all(0 <= v <= 1 for k in ("level", "low", "mid", "high") for v in f[k])
    lv = np.array(f["level"])
    assert lv[5:43].mean() > 0.5 and lv[53:91].mean() < 0.05
    assert np.mean(f["low"]) > np.mean(f["high"])


def test_ensure_caches_and_restamps(tmp_path, song, monkeypatch):
    p = Project.create(tmp_path / "proj", song, "test")
    calls = []
    real = features.compute
    monkeypatch.setattr(features, "compute", lambda s, fps: calls.append(1) or {**real(s, fps)})
    a = ensure(p)
    assert ensure(p) == a and len(calls) == 1
    p.song.write_bytes(p.song.read_bytes() + b"\0" * 64)
    assert ensure(p)["song_sha"] != a["song_sha"] and len(calls) == 2
