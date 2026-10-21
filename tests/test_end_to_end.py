import numpy as np
import pytest
from scipy.io import wavfile

from celery_man import timing
from celery_man.animate import ChromiumRenderer
from celery_man.assemble import probe_duration
from celery_man.generate import FakeGenerator
from celery_man.pipeline import advance
from celery_man.project import Project, Shot, brief_from_dict, read_json, write_json
from celery_man.steps import brief_inputs, shots_inputs, windows
from celery_man.templates import catalogue
from tests.test_pipeline import ALL_AUTO, BRIEF, text_chat, vision_chat

CUSTOM = "function setup(env) {}\nfunction draw(ctx, f, env) { ctx.fillStyle = env.look.background; ctx.fillRect(0, 0, env.width, env.height); }\n"


def click_track(path, bpm=120, secs=40, sr=22050):
    """beat_this finds no pulse in a pure sine, so synthesize a kick pattern: 4/4, accented first beat plus a bass-and-hat groove."""
    y, rng = np.zeros(sr * secs), np.random.default_rng(0)
    for n in range(int(secs * bpm / 60)):
        i, t = int(n * 60 / bpm * sr), np.arange(int(0.12 * sr)) / sr
        y[i : i + len(t)] += (1.0 if n % 4 == 0 else 0.6) * np.sin(2 * np.pi * (60 + 90 * np.exp(-t * 30)) * t) * np.exp(-t * 18)
        h = i + int(0.5 * 60 / bpm * sr)
        y[h : h + 1500] += 0.15 * rng.standard_normal(1500) * np.exp(-np.arange(1500) / 300)
    wavfile.write(path, sr, (y / np.abs(y).max() * 32000).astype(np.int16))


@pytest.mark.slow
def test_end_to_end_animation_project(tmp_path):
    song = tmp_path / "click.wav"
    click_track(song)
    p = Project.create(tmp_path / "proj", song, "test tone", kinds=("animation",))
    modes = ALL_AUTO | {"brief": "tastemaker", "shots": "tastemaker"}
    write_json(p.root / "project.json", p.config | {"modes": modes, "range": [0, 24]})
    write_json(p.timing_path, timing.detect(song))
    brief = brief_from_dict(BRIEF)
    p.save_brief(brief, brief_inputs(p))
    (p.root / "scenes").mkdir()
    (p.root / "scenes" / "custom.js").write_text(CUSTOM)
    names, shots = sorted(catalogue()), []
    for i, (a, b, sec) in enumerate(windows(p)):
        # one shot per template, cycling; the last uses the custom scene file
        scene = {"file": "scenes/custom.js", "params": {}} if i == len(names) else {"template": names[i % len(names)], "params": {}}
        shots.append(Shot(f"s{i + 1:02d}", a, b, sec, "a person walks", ["A"], "P", "wide shot", "animation", scene))
    assert len(shots) > len(names), len(shots)  # room for the custom shot too
    p.save_shots(shots, shots_inputs(p, brief))

    sts = advance(p, FakeGenerator(), text_chat, vision_chat(), renderer=ChromiumRenderer())

    assert {s.state for s in sts} == {"done"}
    assert abs(probe_duration(p.out_dir / "video.mp4") - (shots[-1].end - shots[0].start)) < 0.1
    report = (p.out_dir / "report.md").read_text()
    assert all(f"template `{n}`" in report for n in names)
    assert "file `scenes/custom.js`" in report
    assert read_json(p.renders_path)[shots[len(names)].id]["error"] == ""
