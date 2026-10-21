from dataclasses import replace

import pytest

from celery_man import steps
from celery_man.animate import FakeRenderer
from celery_man.assemble import cut_points, probe_duration
from celery_man.ledger import GPU_PRICE, Ledger
from celery_man.pipeline import advance, estimate, quick, status
from celery_man.project import Shot, brief_from_dict, read_json, write_json
from celery_man.steps import EST_STILL_S, EST_WALL_S, brief_inputs, shots_inputs, windows
from tests.test_pipeline import ALL_AUTO, BRIEF, CountingGen, make, text_chat, vision_chat

RINGS = {"template": "rings", "params": {}}
TASTE_SHOTS = ALL_AUTO | {"brief": "tastemaker", "shots": "tastemaker"}


def project(tmp_path, song, kinds=("footage", "animation"), scene=lambda i: RINGS if i % 2 == 0 else None):
    """A 16 s tastemaker-written project: shots.json written directly, scene(i) None for a footage shot."""
    p = make(tmp_path, song, modes=TASTE_SHOTS)
    write_json(p.root / "project.json", p.config | {"range": [0, 16], "kinds": list(kinds)})
    brief = brief_from_dict(BRIEF)
    p.save_brief(brief, brief_inputs(p))
    shots = []
    for i, (a, b, sec) in enumerate(windows(p), 1):
        sc = scene(i)
        shots.append(
            Shot(f"s{i:02d}", a, b, sec, f"a person walks s{i:02d}", ["A"], "P", "wide shot", "animation" if sc else "footage", sc)
        )
    p.save_shots(shots, shots_inputs(p, brief))
    return p


def go(p, gen=None, renderer=None):
    return {s.step: s for s in advance(p, gen or CountingGen(), text_chat, vision_chat(), renderer=renderer or FakeRenderer())}


def job_shots(p):
    return {r["shot"] for r in Ledger(p.ledger_path).rows() if r.get("kind") == "job" and r["shot"]}


def test_footage_only_project_unchanged(tmp_path, song):
    p = project(tmp_path, song, kinds=("footage",), scene=lambda i: None)
    n = len(p.load_shots())
    assert estimate(p) == pytest.approx(n * EST_STILL_S * GPU_PRICE["L40S"] + (n * 3 + 1) * EST_WALL_S * GPU_PRICE["L4"])
    r = FakeRenderer()
    sts = go(p, renderer=r)
    assert list(sts) == ["timing", "brief", "shots", "render", "keyframes", "takes", "assemble"]
    assert {s.state for s in sts.values()} == {"done"}
    assert not p.renders_path.exists() and r.jobs == []


def test_mixed_project_cuts_on_downbeats(tmp_path, song, monkeypatch):
    p = project(tmp_path, song)
    used = []
    real = steps.assemble
    monkeypatch.setattr(steps, "assemble", lambda cuts, *a: used.append(cuts) or real(cuts, *a))
    r = FakeRenderer()
    sts = go(p, renderer=r)
    assert {s.state for s in sts.values()} == {"done"}
    assert abs(probe_duration(p.out_dir / "video.mp4") - 16.0) < 0.1
    shots = p.load_shots()
    downbeats = read_json(p.timing_path)["downbeats"]
    assert cut_points(used[0]) == [s.start for s in shots] + [shots[-1].end]
    assert all(s.start in downbeats for s in shots)
    anim = {s.id for s in shots if s.kind == "animation"}
    assert anim == {s.id for s in shots[1::2]} and {j.shot_id for j in r.jobs} == anim
    assert job_shots(p) == {s.id for s in shots[::2]}
    assert all(c["in"] == 0 and "/renders/" in c["clip"] for s, c in zip(shots, used[0]) if s.id in anim)


def test_render_cached_by_key(tmp_path, song):
    p = project(tmp_path, song)
    go(p)
    r, gen = FakeRenderer(), CountingGen()
    go(p, gen, r)
    assert r.jobs == []
    brief = p.load_brief()
    p.save_brief(replace(brief, look=brief.look | {"colors": ["#204060"]}), brief_inputs(p))
    sts = go(p, gen, r)
    assert {s.state for s in sts.values()} == {"done"}
    assert sorted(j.shot_id for j in r.jobs) == [s.id for s in p.load_shots() if s.kind == "animation"] and gen.stills_n == []
    d = read_json(p.shots_path)
    d["shots"][0]["action"] = "a person stands"
    write_json(p.shots_path, d)
    r, gen = FakeRenderer(), CountingGen()
    assert {s.state for s in go(p, gen, r).values()} == {"done"}
    assert r.jobs == [] and gen.stills_n == ["s01"]


def test_failed_render_waits_and_does_not_retry(tmp_path, song):
    p = project(tmp_path, song, scene=lambda i: {"file": "scenes/s02.js", "params": {}} if i == 2 else RINGS if i % 2 == 0 else None)
    p.scenes_dir.mkdir()
    (p.scenes_dir / "s02.js").write_text("export default () => { throw new Error('x') }")
    sts = go(p)
    assert sts["render"].state == "waiting" and sts["render"].detail.startswith("s02: ")
    ren = read_json(p.renders_path)
    assert ren["s02"]["error"] and ren["s04"]["error"] == "" and (p.root / ren["s04"]["clip"]).exists()
    r = FakeRenderer()
    assert go(p, renderer=r)["render"].state == "waiting" and r.jobs == []
    (p.scenes_dir / "s02.js").write_text("export default () => {}")
    r = FakeRenderer()
    assert {s.state for s in go(p, renderer=r).values()} == {"done"} and [j.shot_id for j in r.jobs] == ["s02"]
    (p.scenes_dir / "s02.js").unlink()
    st = go(p)["render"]
    assert st.state == "waiting" and "missing scenes/s02.js" in st.detail


def test_all_animation_project_assembles(tmp_path, song):
    p = project(tmp_path, song, kinds=("animation",), scene=lambda i: RINGS | ({"note": "fallback: scene threw"} if i == 3 else {}))
    assert {s.state for s in go(p).values()} == {"done"}
    assert (p.out_dir / "video.mp4").exists() and not p.picks_path.exists() and not p.keyframes_path.exists()
    report = (p.out_dir / "report.md").read_text()
    assert all(f"**{s.id}** (animation)" in report for s in p.load_shots())
    assert "## Scene fallbacks" in report and "template used instead: s03\n" in report
    assert (p.root / "PROVENANCE.md").read_text().count("SIL Open Font License") == 4
    assert not [r for r in Ledger(p.ledger_path).rows() if r.get("kind") == "job"]


def test_quick_renders_animation(tmp_path, song):
    p = project(tmp_path, song)
    out = quick(p, CountingGen(), text_chat, renderer=FakeRenderer())
    assert out.exists() and out.name == "quick.mp4"
    timeline = read_json(p.out_dir / "quick_timeline.json")
    kinds = {s.id: s.kind for s in p.load_shots()}
    assert all(c["clip"].startswith("renders/") == (kinds[c["shot"]] == "animation") for c in timeline)
    assert any(c["clip"].startswith("renders/") for c in timeline)


def test_kind_not_allowed_makes_shots_stale(tmp_path, song):
    p = project(tmp_path, song, kinds=("footage",))
    st = {s.step: s for s in status(p)}["shots"]
    assert st.state != "done" and "s02" in st.detail


def test_auto_rewrite_keeps_animation_shots(tmp_path, song):
    p = project(tmp_path, song)
    kinds = [s.kind for s in p.load_shots()]
    sts = advance(p, CountingGen(), text_chat, vision_chat({"s01"}), renderer=FakeRenderer())
    assert {s.state for s in sts} == {"done"}
    assert p.load_shots()[0].action == "a person stands" and [s.kind for s in p.load_shots()] == kinds
