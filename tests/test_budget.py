import pytest

from celery_man.assemble import probe_duration
from celery_man.ledger import Ledger
from celery_man.pipeline import BudgetError, WaitingError, quick, quick_cost
from celery_man.project import read_json, write_json
from tests.test_pipeline import CountingGen, make, run_auto, states, text_chat


def with_cap(p, cap):
    write_json(p.root / "project.json", p.config | {"cap_usd": cap})
    return p


def jobs(p):
    return [r for r in Ledger(p.ledger_path).rows() if r.get("kind") == "job"]


def test_preflight_refuses_before_any_spend(tmp_path, song):
    p = with_cap(make(tmp_path, song), 3.10)
    gen = CountingGen()
    with pytest.raises(BudgetError, match="quick pass over the range needs"):
        run_auto(p, gen)
    assert jobs(p) == [] and gen.stills_n == gen.clips_n == []


def test_fallback_to_quick_when_round_unaffordable(tmp_path, song):
    p = with_cap(make(tmp_path, song), 4.5)  # $3 allowance + ~$1.07 quick fits; a 24-take round (~$2.7) does not
    gen = CountingGen()
    sts = run_auto(p, gen)
    assert set(states(sts).values()) == {"done"}
    assert abs(probe_duration(p.out_dir / "video.mp4") - 32.0) < 0.1
    picks = read_json(p.picks_path)
    assert len(picks) == 8 and all(pk["quick"] and pk["round"] == 0 and pk["in"] == 0.5 for pk in picks.values())
    assert len(gen.clips_n) == 8
    takes = [r for r in jobs(p) if r["shot"]]
    assert {r["model"] for r in takes if r["model"] != "flux-schnell"} == {"quick"}
    report = (p.out_dir / "report.md").read_text()
    assert "Quick fallback (cap reached)" in report and "s08" in report.split("Quick fallback")[1]
    assert quick_cost(p) == 0


def test_quick_mode_uses_one_take_per_shot(tmp_path, song):
    tm = {"brief": "auto", "shots": "auto", "keyframes": "tastemaker", "takes": "tastemaker"}
    p = make(tmp_path, song, modes=tm)
    assert quick_cost(p) == 0  # no shots yet: nothing to price
    gen = CountingGen()
    out = quick(p, gen, chat_text=text_chat)
    assert out == p.out_dir / "quick.mp4" and abs(probe_duration(out) - 32.0) < 0.1
    assert len(gen.clips_n) == 8 and sorted(gen.clips_n) == sorted(set(gen.clips_n))
    assert not p.picks_path.exists()
    assert len(read_json(p.out_dir / "quick_timeline.json")) == 8
    kfs = read_json(p.keyframes_path)
    assert all(e["approved"] is None and e["note"] == "quick" and e["seed"] == 1 for e in kfs.values())
    takes = [r for r in jobs(p) if r["model"] == "quick"]
    assert len(takes) == 8 and {(r["gen"], r["round"], r["seed"]) for r in takes} == {(0, 0, 1)}
    assert states(run_auto(p, CountingGen()))["keyframes"] == "waiting"  # quick stills still await review

    shots = p.load_shots()
    shots[2].action = "a person sits"
    p.save_shots(shots, read_json(p.shots_path)["inputs"])
    gen = CountingGen()
    quick(p, gen)
    assert gen.stills_n == ["s03"] and gen.clips_n == ["s03"]


def test_quick_waits_on_tastemaker_brief(tmp_path, song):
    p = make(tmp_path, song, modes={"brief": "tastemaker", "shots": "auto", "keyframes": "auto", "takes": "auto"})
    with pytest.raises(WaitingError) as e:
        quick(p, CountingGen(), chat_text=text_chat)
    assert e.value.step == "brief"


def test_quick_refuses_over_cap(tmp_path, song):
    p = with_cap(make(tmp_path, song), 3.10)
    gen = CountingGen()
    with pytest.raises(BudgetError):
        quick(p, gen, chat_text=text_chat)
    assert jobs(p) == [] and gen.stills_n == []
