import hashlib
import json
import re
import subprocess

import pytest

from celery_man.assemble import probe_duration
from celery_man.generate import FakeGenerator
from celery_man.ledger import Ledger
from celery_man.pipeline import advance, estimate, status
from celery_man.project import Brief, CastMember, Place, Project, Shot, read_json, sha12, write_json
from celery_man.steps import RoundLimit
from celery_man.timing import plan_windows

ALL_AUTO = {"brief": "auto", "shots": "auto", "keyframes": "auto", "takes": "auto"}
BRIEF = {
    "logline": "a person walks",
    "style": {"medium": "ink", "palette": "grey", "lighting": "soft", "camera": "static"},
    "cast": [{"name": "A", "description": "a tall person"}],
    "places": [{"name": "P", "description": "a room"}],
    "avoid": [],
}


class CountingGen(FakeGenerator):
    """FakeGenerator that counts jobs, fails clips (only) for chosen shots, and renders one clip it then reuses."""

    def __init__(self, fail_clips=()):
        super().__init__()
        self.fail_clips, self.stills_n, self.clips_n, self._clip = set(fail_clips), [], [], None

    def stills(self, jobs):
        self.stills_n += [j.shot_id for j in jobs]
        return super().stills(jobs)

    def clips(self, jobs):
        self.clips_n += [j.shot_id for j in jobs]
        out = []
        for j in jobs:
            if j.shot_id in self.fail_clips:
                out.append(RuntimeError("boom"))
                continue
            self._clip = self._clip or super().clips([j])[0]
            out.append(dict(self._clip))
        return out


def text_chat(system, user, images):
    if "creative brief" in system:
        return json.dumps(BRIEF)
    if "plan the shots" in system:
        ids = re.findall(r'"id": "(s\d+)"', user)
        return json.dumps([{"id": i, "action": f"a person walks {i}", "cast": ["A"], "place": "P", "framing": "wide shot"} for i in ids])
    if "simplify one shot" in system:
        return json.dumps({"action": "a person stands", "cast": ["A"], "place": "P", "framing": "wide shot"})
    raise AssertionError(system)


def vision_chat(reject=()):
    def chat(system, user, images):
        if "keyframe image" in system:
            return '{"ok": true, "why": "fine"}'
        if any(f"a person walks {s}" in user for s in reject) and "must pick one" not in system:
            return '{"pick": null, "why": "morphing"}'
        return '{"pick": 0, "why": "steady"}'

    return chat


@pytest.fixture
def song(tmp_path_factory):
    path = tmp_path_factory.mktemp("song") / "song.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=40", str(path)], check=True)
    return path


def make(tmp_path, song, modes=ALL_AUTO) -> Project:
    p = Project.create(tmp_path / "proj", song, "test tone")
    write_json(p.root / "project.json", p.config | {"modes": dict(modes), "range": [0, 32], "seed": "a seed"})
    beats = [i * 0.5 for i in range(81)]
    write_json(
        p.timing_path,
        {
            "duration": 40.0,
            "beats": beats,
            "downbeats": beats[::4],
            "tempo": [[0.0, 120.0]],
            "sections": [{"start": 0.0, "end": 16.0, "label": "1"}, {"start": 16.0, "end": 40.0, "label": "2"}],
            "confidence": 1.0,
            "song_sha": hashlib.sha256(p.song.read_bytes()).hexdigest()[:12],
        },
    )
    return p


def states(sts):
    return {s.step: s.state for s in sts}


def run_auto(p, gen=None, reject=()):
    return advance(p, gen or CountingGen(), chat_text=text_chat, chat_vision=vision_chat(reject))


def test_all_auto_runs_to_video(tmp_path, song):
    p = make(tmp_path, song)
    sts = run_auto(p)
    assert set(states(sts).values()) == {"done"}
    assert abs(probe_duration(p.out_dir / "video.mp4") - 32.0) < 0.1
    assert len(p.load_shots()) == 8
    assert "s08" in (p.out_dir / "report.md").read_text()
    assert "Apache-2.0" in (p.root / "PROVENANCE.md").read_text()
    assert states(status(p)) == states(sts)


def test_tastemaker_waits_for_brief(tmp_path, song):
    p = make(tmp_path, song, modes={"brief": "tastemaker", "shots": "tastemaker", "keyframes": "tastemaker", "takes": "tastemaker"})
    gen = CountingGen()
    sts = advance(p, gen)
    assert states(sts)["timing"] == "done" and states(sts)["brief"] == "waiting"
    assert gen.stills_n == gen.clips_n == []


def test_mixed_modes(tmp_path, song):
    p = make(tmp_path, song, modes={"brief": "tastemaker", "shots": "tastemaker", "keyframes": "auto", "takes": "auto"})
    brief = Brief("l", BRIEF["style"], [CastMember("A", "a tall person")], [Place("P", "a room")], [])
    p.save_brief(brief, sha12("a seed"))
    assert states(advance(p, CountingGen()))["shots"] == "waiting"
    windows = plan_windows(read_json(p.timing_path), 0, 32)
    shots = [Shot(f"s{i:02d}", a, b, s, "a person walks", ["A"], "P", "wide shot") for i, (a, b, s) in enumerate(windows, 1)]
    p.save_shots(shots, sha12([windows, ["A"], ["P"]]))
    sts = advance(p, CountingGen(), chat_vision=vision_chat())
    assert set(states(sts).values()) == {"done"}
    assert (p.out_dir / "video.mp4").exists()


def test_auto_step_without_model_raises(tmp_path, song):
    from celery_man.auto import AutoError

    p = make(tmp_path, song)
    with pytest.raises(AutoError, match="auto brief needs"):
        advance(p, CountingGen())


def test_editing_brief_marks_everything_stale_without_running(tmp_path, song):
    p = make(tmp_path, song)
    run_auto(p)
    b = p.load_brief()
    b.style["palette"] = "sepia"
    p.save_brief(b, sha12("a seed"))
    st = states(status(p))
    assert (st["brief"], st["shots"], st["keyframes"], st["takes"]) == ("done", "done", "stale", "stale")
    assert estimate(p) > 0
    gen = CountingGen()
    run_auto(p, gen)
    assert len(gen.stills_n) == 8 and len(gen.clips_n) == 24
    assert set(states(status(p)).values()) == {"done"}


def test_rerun_reuses_finished_takes(tmp_path, song):
    p = make(tmp_path, song)
    first = run_auto(p, CountingGen(fail_clips={"s03"}))
    assert states(first)["takes"] != "done" and not (p.out_dir / "video.mp4").exists()
    before = estimate(p)
    gen = CountingGen()
    sts = run_auto(p, gen)
    assert gen.stills_n == [] and gen.clips_n == ["s03"] * 3
    assert set(states(sts).values()) == {"done"}
    assert before > 0 and estimate(p) == 0


def test_auto_reject_rewrites_then_round_two_must_pick(tmp_path, song):
    p = make(tmp_path, song)
    gen = CountingGen()
    sts = run_auto(p, gen, reject={"s01"})
    assert set(states(sts).values()) == {"done"}
    s01 = p.load_shots()[0]
    assert s01.action == "a person stands"
    assert read_json(p.shots_path)["inputs"] == sha12([plan_windows(read_json(p.timing_path), 0, 32), ["A"], ["P"]])
    assert Ledger(p.ledger_path).rounds("s01") == [1, 2]
    assert read_json(p.picks_path)["s01"]["round"] == 2
    assert gen.stills_n.count("s01") == 2 and gen.clips_n.count("s01") == 6


def test_round_three_refused(tmp_path, song):
    p = make(tmp_path, song)
    run_auto(p, reject={"s01"})
    picks = read_json(p.picks_path)
    picks["s01"] = {"rejected": True, "round": 2, "reason": "still bad"}
    write_json(p.picks_path, picks)
    gen = CountingGen()
    with pytest.raises(RoundLimit, match="s01"):
        run_auto(p, gen)
    assert gen.clips_n == []
