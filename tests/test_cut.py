import shutil

from celery_man import features
from celery_man.assemble import card_clip, probe_duration
from celery_man.cut import cut_plan
from celery_man.generate import FakeGenerator
from celery_man.pipeline import status
from celery_man.project import DEFAULT_LOOK, Shot, read_json, update_json, write_json
from celery_man.renders import render_key
from celery_man.steps import Run, kf_need, run_assemble, windows
from tests.test_pipeline import make, run_auto


def mixed(tmp_path, song):
    """s01 picked take, s02 animation rendered, s03 approved still only, s04 decided without keyframe, s05 open."""
    p = make(tmp_path, song)
    run_auto(p)
    brief = p.load_brief()
    take = p.root / read_json(p.picks_path)["s01"]["clip"]
    shots = p.load_shots()
    shots[1] = Shot("s02", shots[1].start, shots[1].end, shots[1].section, "a person walks", ["A"], "P", "wide shot", "animation",
                    {"template": "title", "params": {}})  # fmt: skip
    p.save_shots(shots, read_json(p.shots_path)["inputs"])
    update_json(p, p.picks_path, lambda d: {k: v for k, v in d.items() if k not in ("s03", "s04")})
    update_json(p, p.keyframes_path, lambda d: {k: v for k, v in d.items() if k != "s04"})
    update_json(
        p,
        p.shots_path,
        lambda d: (
            d
            | {
                "shots": [
                    *d["shots"][:4],
                    {**d["shots"][4], "kind": "open", "action": "", "cast": [], "place": None, "framing": ""},
                    *d["shots"][5:],
                ]
            }
        ),
    )
    feats = features.ensure(p)
    key = render_key(p, p.load_shots()[1], brief.look, feats)
    (p.root / "renders").mkdir(exist_ok=True)
    shutil.copy(take, p.root / f"renders/s02-{key}.mp4")
    write_json(p.renders_path, {"s02": {"key": key, "clip": f"renders/s02-{key}.mp4", "poster": "", "error": ""}})
    return p


def test_cut_plan_kinds(tmp_path, song):
    p = mixed(tmp_path, song)
    plan = cut_plan(p)
    assert [c["kind"] for c in plan][:5] == ["take", "render", "still", "card", "blank"]
    assert plan[2]["src"].endswith(".png") and plan[3]["text"] == p.load_shots()[3].action and plan[4]["src"] is None


def test_assemble_with_gaps_makes_full_length_video(tmp_path, song):
    p = mixed(tmp_path, song)
    run_assemble(Run(p, FakeGenerator()))
    w = windows(p)
    assert abs(probe_duration(p.out_dir / "video.mp4") - (w[-1][1] - w[0][0])) < 0.1
    assert "## Filled windows" in (p.out_dir / "report.md").read_text()
    assert status(p)[-1].state == "done"


def test_card_text_with_quotes_and_colons(tmp_path):
    card_clip("a: 'b' \"c\" 50%", DEFAULT_LOOK, 1.0, tmp_path / "c.mp4")
    assert probe_duration(tmp_path / "c.mp4") >= 1.0


def test_stale_pick_falls_back_to_still(tmp_path, song):
    p = make(tmp_path, song)
    run_auto(p)
    assert cut_plan(p)[0]["kind"] == "take"
    brief = p.load_brief()
    update_json(p, p.shots_path, lambda d: d | {"shots": [{**d["shots"][0], "action": "a person sits"}, *d["shots"][1:]]})
    update_json(
        p,
        p.keyframes_path,
        lambda d: (
            d
            | {
                "s01": {
                    **d["s01"],
                    "prompt_sha": __import__("celery_man.compose", fromlist=["x"]).prompt_sha(p.load_shots()[0], brief),
                    "approved": True,
                }
            }
        ),
    )
    assert kf_need(p, p.load_shots()[0], brief, read_json(p.keyframes_path)["s01"]) == "done"
    assert cut_plan(p)[0]["kind"] == "still"
