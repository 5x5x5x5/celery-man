import hashlib
import json
import re
from pathlib import Path

import pytest

from celery_man.assemble import probe_duration
from celery_man.generate import FakeGenerator
from celery_man.ledger import GPU_PRICE, Ledger
from celery_man.pipeline import advance, estimate, status
from celery_man.project import Brief, CastMember, Place, Project, Shot, read_json, sha12, write_json
from celery_man.steps import EST_STILL_S, EST_WALL_S
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

    def __init__(self, fail_clips=(), junk=()):
        super().__init__()
        self.fail_clips, self.junk, self.stills_n, self.clips_n, self._clip = set(fail_clips), set(junk), [], [], None

    def stills(self, jobs):
        self.stills_n += [j.shot_id for j in jobs]
        return super().stills(jobs)

    def clips(self, jobs):
        self.clips_n += [j.shot_id for j in jobs]
        return self._clips(jobs)

    def _clips(self, jobs):
        for j in jobs:
            if j.shot_id in self.fail_clips:
                yield RuntimeError("boom")
                continue
            if j.key in self.junk or j.shot_id in self.junk:
                self.junk.discard(j.shot_id)  # a shot id junks only its first take
                yield {"mp4": b"not an mp4", "wall_s": 1.0, "gpu": "L4"}
                continue
            self._clip = self._clip or next(super().clips([j]))
            yield dict(self._clip)


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
        m = re.search(r"walks s\d+|stands", user)
        chat.judged.append((m[0] if m else "", "must pick one" in system, len(images)))
        if any(f"a person walks {s}" in user for s in reject) and "must pick one" not in system:
            return '{"pick": null, "why": "morphing"}'
        return '{"pick": 0, "why": "steady"}'

    chat.judged = []
    return chat


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


def run_auto(p, gen=None, reject=(), vision=None):
    return advance(p, gen or CountingGen(), chat_text=text_chat, chat_vision=vision or vision_chat(reject))


def take_rows(p, shot):
    return [r for r in Ledger(p.ledger_path).rows() if r.get("kind") == "job" and r["shot"] == shot and r["model"] == "ltx-2.5-draft"]


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
    gen, vision = CountingGen(), vision_chat()
    run_auto(p, gen, vision=vision)
    assert len(gen.stills_n) == 8 and len(gen.clips_n) == 24
    assert set(states(status(p)).values()) == {"done"}
    assert {(r["gen"], r["round"]) for r in take_rows(p, "s01")} == {(1, 1), (2, 1)}
    assert read_json(p.picks_path)["s01"]["gen"] == 2
    assert len(vision.judged) == 8 and not any(must for _, must, _ in vision.judged)


def test_brief_edit_after_two_rounds_starts_a_fresh_lineage(tmp_path, song):
    p = make(tmp_path, song)
    run_auto(p, reject={"s01"})
    assert Ledger(p.ledger_path).rounds("s01") == [1, 2]
    b = p.load_brief()
    b.style["palette"] = "sepia"
    p.save_brief(b, sha12("a seed"))
    gen = CountingGen()
    assert set(states(run_auto(p, gen)).values()) == {"done"}
    assert gen.clips_n.count("s01") == 3
    assert read_json(p.picks_path)["s01"]["gen"] == 2 and read_json(p.picks_path)["s01"]["round"] == 1


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


def test_round_limit_is_per_shot(tmp_path, song):
    p = make(tmp_path, song)
    run_auto(p, CountingGen(fail_clips={"s02"}), reject={"s01"})
    assert Ledger(p.ledger_path).rounds("s01") == [1, 2] and "s02" not in read_json(p.picks_path)
    picks = read_json(p.picks_path)
    picks["s01"] = {"rejected": True, "gen": 1, "round": 2, "reason": "still bad"}
    write_json(p.picks_path, picks)
    gen = CountingGen()
    sts = {s.step: s for s in run_auto(p, gen)}
    assert "s01" not in gen.clips_n and gen.clips_n == ["s02"] * 3
    assert sts["takes"].state == "waiting" and sts["takes"].detail == "s01: pick a round-2 take"
    assert "s02" in read_json(p.picks_path)


def test_one_junk_clip_keeps_the_rest_of_the_batch(tmp_path, song):
    p = make(tmp_path, song)
    vision = vision_chat()
    sts = run_auto(p, CountingGen(junk={"s02"}), vision=vision)
    assert set(states(sts).values()) == {"done"}
    rows = [r for r in Ledger(p.ledger_path).rows() if r.get("model") == "ltx-2.5-draft"]
    assert len(rows) == 24 and all(r["output"] for r in rows)
    assert ("walks s02", False, 2) in vision.judged  # the junk take is never shown to the judge


def test_rewrite_to_the_same_shot_raises(tmp_path, song):
    from celery_man.auto import AutoError

    def lazy_text(system, user, images):
        if "simplify one shot" in system:
            return json.dumps({"action": "a person walks s01", "cast": ["A"], "place": "P", "framing": "wide shot"})
        return text_chat(system, user, images)

    p = make(tmp_path, song)
    with pytest.raises(AutoError, match="same shot"):
        advance(p, CountingGen(), chat_text=lazy_text, chat_vision=vision_chat({"s01"}))


def test_estimate_fresh_project_matches_formula(tmp_path, song):
    p = make(tmp_path, song, modes={"brief": "tastemaker", "shots": "tastemaker", "keyframes": "auto", "takes": "auto"})
    brief = Brief("l", BRIEF["style"], [CastMember("A", "a tall person")], [Place("P", "a room")], [])
    p.save_brief(brief, sha12("a seed"))
    windows = plan_windows(read_json(p.timing_path), 0, 32)
    p.save_shots([Shot(f"s{i:02d}", a, b, s, "a person walks", ["A"], "P", "wide shot") for i, (a, b, s) in enumerate(windows, 1)], "x")
    n = 8
    assert estimate(p) == pytest.approx(n * EST_STILL_S * GPU_PRICE["L40S"] + (n * 3 + 1) * EST_WALL_S * GPU_PRICE["L4"])


class DiesOnThirdClip(CountingGen):
    """Yields two clips, then the batch dies the way a killed run does; records the ledger as each batch starts."""

    def __init__(self, ledger_path):
        super().__init__()
        self.ledger_path, self.seen = ledger_path, {}

    def stills(self, jobs):
        self.seen["stills"] = Ledger(self.ledger_path).rows()
        yield from super().stills(jobs)

    def clips(self, jobs):
        self.seen["clips"] = Ledger(self.ledger_path).rows()
        for n, res in enumerate(super().clips(jobs)):
            if n == 2:
                raise RuntimeError("killed")
            yield res


def test_finished_clips_survive_a_dying_batch(tmp_path, song):
    p = make(tmp_path, song)
    gen = DiesOnThirdClip(p.ledger_path)
    with pytest.raises(RuntimeError, match="killed"):
        run_auto(p, gen)
    rows = [r for r in Ledger(p.ledger_path).rows() if r.get("model") == "ltx-2.5-draft"]
    assert len(rows) == 2 and all(Path(r["output"]).is_file() for r in rows)
    # the reservation is in the ledger before the first result is consumed, so a hard kill leaves the estimate charged
    [rs] = [r for r in gen.seen["stills"] if r.get("model") == "reserve"]
    assert (rs["gpu"], rs["wall_s"]) == ("L40S", EST_STILL_S * 9)
    [rc] = [r for r in gen.seen["clips"] if r.get("model") == "reserve" and r["gpu"] == "L4"]
    assert rc["wall_s"] == EST_WALL_S * 25
    reserve = [r["wall_s"] for r in Ledger(p.ledger_path).rows() if r.get("model") == "reserve"]
    assert len(reserve) == 4 and sum(reserve) == 0  # each batch's finally settles its reservation
    assert any(r.get("model") == "overhead" for r in Ledger(p.ledger_path).rows())


def test_clean_run_spend_nets_to_actuals(tmp_path, song):
    p = make(tmp_path, song)
    run_auto(p)
    led = Ledger(p.ledger_path)
    actual = sum(r["wall_s"] * GPU_PRICE[r["gpu"]] for r in led.rows() if r.get("kind") == "job" and r["model"] != "reserve")
    assert led.spent_usd() == pytest.approx(3 + actual)
    assert led.rounds("s01") == [1]
