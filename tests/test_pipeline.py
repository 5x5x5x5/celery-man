import hashlib
import json
import re
from pathlib import Path

import pytest

from celery_man.assemble import probe_duration
from celery_man.generate import FakeGenerator
from celery_man.ledger import GPU_PRICE, Ledger
from celery_man.pipeline import advance, estimate, status
from celery_man.project import DEFAULT_MODES, Brief, CastMember, Place, Project, Shot, open_shot, read_json, sha12, update_json, write_json
from celery_man.steps import EST_STILL_S, EST_WALL_S, shots_inputs, windows
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

    def __init__(self, fail_clips=(), junk=(), junk_takes=()):
        super().__init__()
        self.fail_clips, self.junk, self.stills_n, self.clips_n, self._clip = set(fail_clips), set(junk), [], [], None
        self.junk_takes = set(junk_takes)  # shots whose judged takes (seeds >= 1000, not the quick seed) are all junk

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
            if j.key in self.junk or j.shot_id in self.junk or (j.shot_id in self.junk_takes and j.seed >= 1000):
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
    report = (p.out_dir / "report.md").read_text()
    assert "(round 2, forced)" in report.split("**s01**")[1].split("\n")[0] and "forced" not in report.split("**s02**")[1]


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


def test_held_run_lock_refuses_advance_and_quick(tmp_path, song):
    from celery_man.pipeline import BusyError, quick, run_lock

    p = make(tmp_path, song)
    with run_lock(p):
        with pytest.raises(BusyError):
            run_auto(p)
        with pytest.raises(BusyError):
            quick(p, CountingGen(), chat_text=text_chat)
    assert not p.brief_path.exists()
    assert set(states(run_auto(p)).values()) == {"done"}  # released


def test_confirm_sees_the_real_estimate_after_shots(tmp_path, song):
    p, seen = make(tmp_path, song), []
    gen = CountingGen()
    sts = advance(p, gen, text_chat, vision_chat(), confirm=lambda c: seen.append(c) or False)
    assert len(seen) == 1 and seen[0] > 0 and seen[0] == pytest.approx(estimate(p))
    assert states(sts)["shots"] == "done" and states(sts)["keyframes"] != "done"
    assert gen.stills_n == gen.clips_n == [] and not [r for r in Ledger(p.ledger_path).rows() if r.get("kind") == "job"]
    assert set(states(advance(p, gen, text_chat, vision_chat(), confirm=lambda c: True)).values()) == {"done"}


def test_quick_declined_at_the_gate_spends_nothing(tmp_path, song):
    from celery_man.pipeline import Declined, quick

    p, seen = make(tmp_path, song), []
    gen = CountingGen()
    with pytest.raises(Declined):
        quick(p, gen, chat_text=text_chat, confirm=lambda c: seen.append(c) or False)
    assert len(seen) == 1 and seen[0] > 0 and p.shots_path.exists() and gen.stills_n == []


def test_all_takes_unusable_rejects_then_falls_back_to_quick(tmp_path, song):
    p = make(tmp_path, song)
    gen = CountingGen(junk_takes={"s01"})
    sts = run_auto(p, gen)
    assert set(states(sts).values()) == {"done"} and (p.out_dir / "video.mp4").exists()
    assert p.load_shots()[0].action == "a person stands"  # round 1 was rejected as unusable, then rewritten
    assert Ledger(p.ledger_path).rounds("s01") == [1, 2]
    pick = read_json(p.picks_path)["s01"]
    assert pick["quick"] and gen.clips_n.count("s01") == 7  # 2 rounds of 3, then one quick take


def test_tastemaker_all_unusable_round_waits_on_an_edit(tmp_path, song):
    p = make(tmp_path, song, modes=ALL_AUTO | {"takes": "tastemaker"})
    sts = {s.step: s for s in run_auto(p, CountingGen(junk_takes={"s01"}))}
    assert read_json(p.picks_path)["s01"] == {"rejected": True, "round": 1, "gen": 1, "reason": "all takes unusable"}
    assert sts["takes"].state == "waiting" and "s01 edit" in sts["takes"].detail


def test_hand_edited_shot_windows_are_stale(tmp_path, song):
    p = make(tmp_path, song)
    run_auto(p)
    d = read_json(p.shots_path)
    d["shots"][2]["end"] += 0.5
    write_json(p.shots_path, d)
    st = {s.step: s for s in status(p)}["shots"]
    assert st.state == "stale" and "s03" in st.detail
    write_json(p.root / "project.json", p.config | {"modes": ALL_AUTO | {"shots": "tastemaker"}})
    st = {s.step: s for s in status(p)}["shots"]
    assert st.state == "waiting" and "s03" in st.detail
    del d["shots"][-1]
    write_json(p.shots_path, d)
    assert "7 shots" in {s.step: s for s in status(p)}["shots"].detail


# --- per-shot progression ---------------------------------------------------------------------------


def status_of(p, step):
    return {s.step: s for s in status(p)}[step]


def saved_brief(p):
    brief = Brief("l", BRIEF["style"], [CastMember("A", "a tall person")], [Place("P", "a room")], [])
    p.save_brief(brief, sha12("a seed"))
    return brief


def decide(p, brief, ids):
    """Shots file with the given ids decided as footage and the rest open."""
    shots = [
        Shot(f"s{i:02d}", a, b, s, f"a person walks s{i:02d}", ["A"], "P", "wide shot")
        if f"s{i:02d}" in ids
        else open_shot(f"s{i:02d}", a, b, s)
        for i, (a, b, s) in enumerate(windows(p), 1)
    ]
    p.save_shots(shots, shots_inputs(p, brief))


def with_shot_modes(p, shot_modes):
    write_json(p.root / "project.json", p.config | {"shot_modes": shot_modes})


def test_timing_writes_open_shots(tmp_path, song):
    p = make(tmp_path, song, DEFAULT_MODES)
    advance(p, FakeGenerator())
    assert all(s.is_open for s in p.load_shots()) and len(p.load_shots()) == len(windows(p))
    assert read_json(p.shots_path)["inputs"] == ""
    assert status_of(p, "shots").state == "waiting"  # on the brief


def test_decided_shot_gets_keyframe_while_others_open(tmp_path, song):
    p = make(tmp_path, song, DEFAULT_MODES)
    decide(p, saved_brief(p), {"s01"})
    with_shot_modes(p, {"s01": {"keyframes": "auto"}})
    advance(p, FakeGenerator(), chat_vision=vision_chat())
    kfs = read_json(p.keyframes_path)
    assert set(kfs) == {"s01"} and kfs["s01"]["approved"] is True and status_of(p, "shots").state == "waiting"
    assert status_of(p, "takes").state == "waiting"  # s01's takes ran and wait on a pick


def test_auto_window_filled_tastemaker_left_open(tmp_path, song):
    p = make(tmp_path, song, DEFAULT_MODES)
    saved_brief(p)
    with_shot_modes(p, {"s02": {"shots": "auto"}})
    advance(p, FakeGenerator(), chat_text=text_chat)
    shots = {s.id: s for s in p.load_shots()}
    assert not shots["s02"].is_open and shots["s01"].is_open
    assert [s.id for s in shots.values() if not s.is_open] == ["s02"]
    assert status_of(p, "shots").state == "waiting"


def test_only_limits_runners(tmp_path, song):
    p = make(tmp_path, song, DEFAULT_MODES)
    decide(p, saved_brief(p), {"s01", "s02"})
    advance(p, FakeGenerator(), only={"s01"})
    assert set(read_json(p.keyframes_path)) == {"s01"}


def test_concurrent_keyframe_write_survives(tmp_path, song):
    p = make(tmp_path, song, DEFAULT_MODES)
    decide(p, saved_brief(p), {"s01", "s02"})
    advance(p, FakeGenerator(), only={"s02"})

    class ApprovingGen(FakeGenerator):
        def stills(self, jobs):
            update_json(p, p.keyframes_path, lambda d: d | {"s02": d["s02"] | {"approved": True}})
            return super().stills(jobs)

    advance(p, ApprovingGen())
    kfs = read_json(p.keyframes_path)
    assert kfs["s02"]["approved"] is True and kfs["s01"]["approved"] is None


def test_only_moves_its_shot_past_others_needs(tmp_path, song):
    p = make(tmp_path, song, DEFAULT_MODES)
    decide(p, saved_brief(p), {"s01", "s02"})
    with_shot_modes(p, {"s01": {"keyframes": "auto"}})
    advance(p, FakeGenerator(), chat_vision=vision_chat(), only={"s01"})
    assert set(read_json(p.keyframes_path)) == {"s01"} and len(take_rows(p, "s01")) == 3
    assert status_of(p, "keyframes").state == "stale"  # the full status still sees s02


def test_stale_shots_file_stops_advance_before_spending(tmp_path, song):
    p = make(tmp_path, song, DEFAULT_MODES)
    decide(p, saved_brief(p), {"s01"})
    write_json(p.shots_path, read_json(p.shots_path) | {"inputs": "bogus"})
    with_shot_modes(p, {"s01": {"keyframes": "auto"}})
    gen = CountingGen()
    sts = advance(p, gen, chat_vision=vision_chat())
    assert states(sts)["shots"] == "waiting" and gen.stills_n == gen.clips_n == []


def test_takes_wait_on_a_pending_tastemaker_keyframe(tmp_path, song):
    p = make(tmp_path, song, DEFAULT_MODES)
    decide(p, saved_brief(p), {"s01"})
    advance(p, FakeGenerator())
    assert read_json(p.keyframes_path)["s01"]["approved"] is None
    st = status_of(p, "takes")
    assert st.state == "waiting" and "s01 keyframe" in st.detail


# --- round cap: auto stops at 2, a tastemaker does not -------------------------------------------------


def _rejected_twice_then_edited(tmp_path, song, takes_mode):
    """s01 has rounds 1 and 2 in the ledger, round 2 rejected; the shot is then edited and the pipeline advanced."""
    p = make(tmp_path, song)
    run_auto(p, reject={"s01"})
    assert Ledger(p.ledger_path).rounds("s01") == [1, 2]
    write_json(p.picks_path, read_json(p.picks_path) | {"s01": {"rejected": True, "gen": 1, "round": 2, "reason": "bad"}})
    write_json(p.root / "project.json", p.config | {"modes": ALL_AUTO | {"takes": takes_mode}})
    d = read_json(p.shots_path)
    d["shots"][0]["action"] = "a person sits"
    write_json(p.shots_path, d)
    return p, run_auto(p)


def test_tastemaker_round_three_runs(tmp_path, song):
    p, _ = _rejected_twice_then_edited(tmp_path, song, "tastemaker")
    assert Ledger(p.ledger_path).rounds("s01") == [1, 2, 3]


def test_auto_round_three_is_limit(tmp_path, song):
    p, sts = _rejected_twice_then_edited(tmp_path, song, "auto")
    assert Ledger(p.ledger_path).rounds("s01") == [1, 2] and status_of_sts(sts, "takes").detail == "s01: pick a round-2 take"


def status_of_sts(sts, step):
    return {s.step: s for s in sts}[step]


def test_unusable_round_two_tastemaker_rejected_not_quick(tmp_path, song):
    p = make(tmp_path, song, ALL_AUTO | {"takes": "tastemaker"})
    gen = CountingGen(junk_takes={"s01"})
    run_auto(p, gen)
    d = read_json(p.shots_path)
    d["shots"][0]["action"] = "a person sits"
    write_json(p.shots_path, d)
    run_auto(p, gen)
    pick = read_json(p.picks_path)["s01"]
    assert Ledger(p.ledger_path).rounds("s01") == [1, 2] and pick["rejected"] is True and pick["round"] == 2 and not pick.get("quick")


def test_cold_start_spend_does_not_stop_a_confirmed_continue(tmp_path, song):
    class ColdGen(CountingGen):
        def stills(self, jobs):  # real Modal: a cold start bills far past the estimate
            return [r | {"wall_s": 20 * EST_STILL_S} for r in super().stills(jobs)]

    p, seen = make(tmp_path, song), []
    advance(p, CountingGen(), text_chat, vision_chat(), confirm=lambda c: False)  # brief and shots, nothing spent
    shown = estimate(p)
    sts = advance(p, ColdGen(), text_chat, vision_chat(), confirm=lambda c: seen.append(c) or c <= shown + 0.01)
    assert set(states(sts).values()) == {"done"} and len(seen) == 2 and seen[1] == pytest.approx(shown)
