import threading

from fastapi.testclient import TestClient

from celery_man.generate import FakeGenerator
from celery_man.ledger import Ledger
from celery_man.pipeline import advance, estimate, run_lock
from celery_man.project import DEFAULT_MODES, Brief, CastMember, Place, Shot, brief_from_dict, read_json, write_json
from celery_man.steps import brief_inputs, shots_inputs, windows
from celery_man.web.app import create_app
from tests.test_pipeline import BRIEF, make, text_chat, vision_chat


def project(tmp_path, song, brief=True, shots=True):
    p = make(tmp_path, song, DEFAULT_MODES)
    write_json(p.root / "project.json", p.config | {"range": [0, 4]})  # one shot keeps the fake renders quick
    if brief:
        p.save_brief(brief_from_dict(BRIEF), brief_inputs(p))
    if shots:
        b = p.load_brief()
        p.save_shots([Shot(f"s{i:02d}", a, z, s, "a person walks", ["A"], "P", "wide shot") for i, (a, z, s) in enumerate(windows(p), 1)],
                     shots_inputs(p, b))  # fmt: skip
    return p


def client(p, gen_factory=FakeGenerator) -> TestClient:
    return local(create_app(p, gen_factory, lambda p: (text_chat, vision_chat())))


def local(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1:8765")


def to_judging(p):
    """Keyframes made, approved by hand, then the round-1 takes made: the takes step waits on a pick."""
    advance(p, FakeGenerator())
    kfs = read_json(p.keyframes_path)
    write_json(p.keyframes_path, {k: v | {"approved": True} for k, v in kfs.items()})
    advance(p, FakeGenerator())


def test_save_brief_writes_stamped_file(tmp_path, song):
    p = project(tmp_path, song, brief=False, shots=False)
    form = {"logline": "a person walks", "medium": "ink", "palette": "grey", "lighting": "soft", "camera": "static",
            "cast_name": ["A", ""], "cast_description": ["a tall person", ""], "place_name": ["P"], "place_description": ["a room"],
            "avoid": "text\n\nlogos\n"}  # fmt: skip
    r = client(p).post("/brief", data=form)
    assert r.status_code == 200
    d = read_json(p.brief_path)
    assert d["inputs"] == brief_inputs(p)
    assert p.load_brief() == Brief("a person walks", {"medium": "ink", "palette": "grey", "lighting": "soft", "camera": "static"},
                                   [CastMember("A", "a tall person")], [Place("P", "a room")], ["text", "logos"])  # fmt: skip


def test_brief_page_renders_rows(tmp_path, song):
    p = project(tmp_path, song, shots=False)
    r = client(p).get("/brief")
    assert r.status_code == 200 and "a tall person" in r.text and r.text.count('name="cast_name"') == 4


def test_auto_step_is_read_only(tmp_path, song):
    p = project(tmp_path, song, shots=False)
    write_json(p.root / "project.json", p.config | {"modes": DEFAULT_MODES | {"brief": "auto"}})
    c = client(p)
    assert "set by auto" in c.get("/brief").text
    assert c.post("/brief", data={"logline": "x"}).status_code == 409


def test_shots_page_shows_counts(tmp_path, song):
    p = project(tmp_path, song)
    r = client(p).get("/shots")
    assert r.status_code == 200 and "/ 256" in r.text and "a person walks" in r.text


def test_shots_page_without_timing(tmp_path, song):
    p = project(tmp_path, song, shots=False)
    p.timing_path.unlink()
    assert "Run once to analyse the song" in client(p).get("/shots").text


def test_shots_too_long_not_saved(tmp_path, song):
    p = project(tmp_path, song)
    before = p.shots_path.read_text()
    form = {"action_s01": "a person walks " * 200, "cast_s01": ["A"], "place_s01": "P", "framing_s01": "wide shot"}
    r = client(p).post("/shots", data=form)
    assert r.status_code == 422 and "limit 256" in r.text
    assert p.shots_path.read_text() == before


def test_save_shots_stamps_inputs(tmp_path, song):
    p = project(tmp_path, song, shots=False)
    r = client(p).post("/shots", data={"action_s01": "a person sits", "cast_s01": ["A"], "place_s01": "", "framing_s01": "close"})
    assert r.status_code == 200
    assert read_json(p.shots_path)["inputs"] == shots_inputs(p, p.load_brief())
    assert p.load_shots() == [Shot("s01", 0.0, 4.0, "1", "a person sits", ["A"], None, "close")]


def test_keyframe_approve_and_reject(tmp_path, song):
    p = project(tmp_path, song)
    advance(p, FakeGenerator())
    c = client(p)
    page = c.get("/keyframes").text
    path = read_json(p.keyframes_path)["s01"]["path"]
    assert f"/files/{path}" in page and c.get(f"/files/{path}").status_code == 200
    c.post("/keyframes/s01", data={"verdict": "reject", "note": "blurry"})
    assert read_json(p.keyframes_path)["s01"] | {"path": 0, "seed": 0, "prompt_sha": 0} == {
        "path": 0, "seed": 0, "prompt_sha": 0, "approved": False, "note": "blurry"}  # fmt: skip
    c.post("/keyframes/s01", data={"verdict": "approve", "note": ""})
    assert read_json(p.keyframes_path)["s01"]["approved"] is True


def test_pick_writes_picks_json(tmp_path, song):
    p = project(tmp_path, song)
    to_judging(p)
    c = client(p)
    page = c.get("/takes").text
    assert "Reject all" in page and "/files/sheets/" in page
    key = min(r["key"] for r in Ledger(p.ledger_path).rows() if r.get("shot") == "s01" and r.get("model") == "ltx-2.5-draft")
    assert c.post("/takes/s01/pick", data={"key": "nope"}).status_code == 400
    assert c.post("/takes/s01/pick", data={"key": key}).status_code == 200
    assert read_json(p.picks_path)["s01"] == {"key": key, "clip": f"takes/{key}.mp4", "in": 0.5, "round": 1, "gen": 1, "quick": False}
    assert [s.state for s in advance(p, FakeGenerator())] == ["done"] * 7


def test_reject_disabled_in_round_two(tmp_path, song):
    p = project(tmp_path, song)
    to_judging(p)
    c = client(p)
    assert c.post("/takes/s01/reject", data={"reason": ""}).status_code == 400  # a reason is required
    c.post("/takes/s01/reject", data={"reason": "morphing"})
    assert read_json(p.picks_path)["s01"] == {"rejected": True, "round": 1, "gen": 1, "reason": "morphing"}
    assert 'href="/shots#s01"' in c.get("/takes").text
    c.post("/shots", data={"action_s01": "a person stands", "cast_s01": ["A"], "place_s01": "P", "framing_s01": "wide shot"})
    to_judging(p)
    page = c.get("/takes").text
    assert "round 2" in page and "disabled" in page
    before = p.picks_path.read_text()
    assert c.post("/takes/s01/reject", data={"reason": "still bad"}).status_code == 400
    assert p.picks_path.read_text() == before


def test_files_route_blocks_traversal(tmp_path, song):
    p = project(tmp_path, song)
    (tmp_path / "secret.txt").write_text("x")
    c = client(p)
    assert c.get("/files/../pyproject.toml").status_code == 404
    assert c.get("/files/..%2Fsecret.txt").status_code == 404
    assert c.get("/files/%2E%2E/secret.txt").status_code == 404
    assert c.get("/files/project.json").status_code == 200


def test_second_run_conflicts(tmp_path, song):
    p = project(tmp_path, song)
    go, made = threading.Event(), []

    def slow_gen():
        go.wait(10)
        made.append(FakeGenerator())
        return made[-1]

    app = create_app(p, slow_gen, lambda p: (text_chat, vision_chat()))
    c = local(app)
    assert c.post("/run", data={"shown": "100"}, follow_redirects=False).status_code == 303
    assert "running" in c.get("/").text
    assert c.post("/run", data={"shown": "100"}).status_code == 409
    go.set()
    app.state.runner.thread.join(10)
    assert made and read_json(p.keyframes_path)["s01"]["approved"] is None
    assert c.post("/run", data={"shown": "100"}, follow_redirects=False).status_code == 303
    app.state.runner.thread.join(10)


def test_run_error_is_shown(tmp_path, song):
    p = project(tmp_path, song, brief=False, shots=False)
    write_json(p.root / "project.json", p.config | {"modes": DEFAULT_MODES | {"brief": "auto"}})

    def boom():
        raise RuntimeError("no generator today")

    app = create_app(p, boom, lambda p: (None, None))
    c = local(app)
    c.post("/run")
    app.state.runner.thread.join(10)
    assert "no generator today" in c.get("/").text


def test_overview_and_modes(tmp_path, song):
    p = project(tmp_path, song)
    c = client(p)
    r = c.get("/")
    assert r.status_code == 200 and "Run (≈$" in r.text and "of $25.00" in r.text
    c.post("/modes", data={"brief": "tastemaker", "shots": "tastemaker", "keyframes": "auto", "takes": "auto"})
    assert p.modes == {"brief": "tastemaker", "shots": "tastemaker", "keyframes": "auto", "takes": "auto"}
    assert c.post("/modes", data={"brief": "sideways"}).status_code == 400


def held_run(p):
    """An app whose run is stuck in gen_factory until the returned event is set."""
    go = threading.Event()
    app = create_app(p, lambda: go.wait(10) and FakeGenerator(), lambda p: (text_chat, vision_chat()))
    return app, local(app), go


def test_saves_refused_while_running(tmp_path, song):
    p = project(tmp_path, song)
    advance(p, FakeGenerator())
    before = p.keyframes_path.read_text()
    app, c, go = held_run(p)
    c.post("/run")
    assert c.post("/keyframes/s01", data={"verdict": "approve", "note": ""}).status_code == 409
    assert c.post("/brief", data={"logline": "x"}).status_code == 409
    assert "<fieldset disabled>" in c.get("/brief").text and "Approve" not in c.get("/keyframes").text
    go.set()
    app.state.runner.thread.join(10)
    assert p.keyframes_path.read_text() == before


def test_foreign_origin_and_host_refused(tmp_path, song):
    p = project(tmp_path, song)
    app, c, go = held_run(p)
    assert c.post("/run", headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/run", headers={"Origin": "null"}).status_code == 403
    assert c.post("/run", headers={"Referer": "http://evil.example/x"}).status_code == 403
    assert not app.state.runner.running and app.state.runner.thread is None
    assert c.get("/", headers={"Host": "evil.example:8765"}).status_code == 400
    assert c.get("/", headers={"Host": "localhost:8765"}).status_code == 200
    assert c.post("/modes", headers={"Origin": "http://127.0.0.1:8765"}, data={"brief": "tastemaker"}).status_code == 200
    go.set()


def test_unusable_take_cannot_be_picked(tmp_path, song):
    p = project(tmp_path, song)
    to_judging(p)
    key = min(r["key"] for r in Ledger(p.ledger_path).rows() if r.get("shot") == "s01" and r.get("model") == "ltx-2.5-draft")
    (p.takes_dir / f"{key}.mp4").write_bytes(b"not an mp4")
    (p.sheets_dir / f"{key}.jpg").unlink()
    c = client(p)
    assert c.post("/takes/s01/pick", data={"key": key}).status_code == 400
    assert not p.picks_path.exists()


def test_brief_unequal_rows_is_422(tmp_path, song):
    p = project(tmp_path, song, brief=False, shots=False)
    r = client(p).post("/brief", data={"logline": "x", "cast_name": ["A", "B"], "cast_description": ["a tall person"]})
    assert r.status_code == 422 and not p.brief_path.exists()


def test_overview_survives_broken_project_json(tmp_path, song):
    p = project(tmp_path, song)
    (p.root / "project.json").write_text("{")
    r = client(p).get("/")
    assert r.status_code == 200 and "JSONDecodeError" in r.text


def test_files_route_blocks_symlink_out(tmp_path, song):
    p = project(tmp_path, song)
    (tmp_path / "secret.txt").write_text("x")
    (p.root / "link.txt").symlink_to(tmp_path / "secret.txt")
    assert client(p).get("/files/link.txt").status_code == 404


def test_saves_and_runs_refused_while_another_process_runs(tmp_path, song):
    p = project(tmp_path, song)
    c = client(p)
    with run_lock(p):
        assert c.post("/brief", data={"logline": "x"}).status_code == 409
        assert c.post("/run", data={"shown": "100"}).status_code == 409
        assert "running" in c.get("/").text
    assert not p.keyframes_path.exists()


def run_once(app, c, **data):
    assert c.post("/run", data=data, follow_redirects=False).status_code == 303
    app.state.runner.thread.join(20)


def test_run_stops_at_the_gate_when_the_estimate_rose(tmp_path, song):
    p = project(tmp_path, song)
    app = create_app(p, FakeGenerator, lambda p: (text_chat, vision_chat()))
    c = local(app)
    est = estimate(p)
    assert f'name="shown" value="{est:.2f}"' in c.get("/").text
    run_once(app, c, shown="0")
    assert not p.keyframes_path.exists() and "estimate is now" in c.get("/").text
    run_once(app, c, shown=f"{est:.2f}")
    assert p.keyframes_path.exists()


def test_unknown_estimate_runs_to_the_gate_then_shows_it(tmp_path, song):
    p = project(tmp_path, song, shots=False)
    write_json(p.root / "project.json", p.config | {"modes": DEFAULT_MODES | {"shots": "auto", "keyframes": "auto"}})
    app = create_app(p, FakeGenerator, lambda p: (text_chat, vision_chat()))
    c = local(app)
    assert "estimate after shots are written" in c.get("/").text
    run_once(app, c, shown="")
    assert p.shots_path.exists() and not p.keyframes_path.exists()
    page = c.get("/").text
    assert f"Run (≈${estimate(p):.2f})" in page and estimate(p) > 0


def test_round_two_without_usable_takes_can_be_rejected_then_falls_back(tmp_path, song):
    p = project(tmp_path, song)
    to_judging(p)
    c = client(p)
    c.post("/takes/s01/reject", data={"reason": "morphing"})
    c.post("/shots", data={"action_s01": "a person stands", "cast_s01": ["A"], "place_s01": "P", "framing_s01": "wide shot"})
    to_judging(p)
    for r in Ledger(p.ledger_path).rows():  # every round-2 take turns out unreadable
        if r.get("model") == "ltx-2.5-draft" and r["round"] == 2:
            (p.takes_dir / f"{r['key']}.mp4").write_bytes(b"not an mp4")
            (p.sheets_dir / f"{r['key']}.jpg").unlink(missing_ok=True)
    assert "disabled" not in c.get("/takes").text
    assert c.post("/takes/s01/reject", data={"reason": "no usable take"}).status_code == 200
    assert "quick take" in c.get("/takes").text
    assert [s.state for s in advance(p, FakeGenerator())] == ["done"] * 7
    assert read_json(p.picks_path)["s01"]["quick"]


# --- animation: look, kinds, scenes ----------------------------------------------------------------


def both(tmp_path, song):
    p = project(tmp_path, song)
    write_json(p.root / "project.json", p.config | {"kinds": ["footage", "animation"]})
    return p


BRIEF_FORM = {"logline": "x", "medium": "m", "palette": "p", "lighting": "l", "camera": "c", "cast_name": ["A"], "cast_description": ["d"],
              "place_name": ["P"], "place_description": ["d"], "avoid": ""}  # fmt: skip


def test_brief_look_saves_and_validates(tmp_path, song):
    p = project(tmp_path, song, brief=False, shots=False)
    c = client(p)
    look = {"background": "#101010", "color_1": "#ff0000", "color_2": "#00ff00", "unused_2": "on", "color_3": "#0000ff", "typeface": "mono"}
    assert c.post("/brief", data=BRIEF_FORM | look).status_code == 200
    assert p.load_brief().look == {"background": "#101010", "colors": ["#ff0000", "#0000ff"], "typeface": "mono"}
    page = c.get("/brief").text
    assert 'value="#101010"' in page and "<option selected>mono</option>" in page
    r = c.post("/brief", data=BRIEF_FORM | look | {"background": "red"})
    assert r.status_code == 422 and "look.background" in r.text
    assert p.load_brief().look["background"] == "#101010"


def test_shots_kind_select_only_when_both_allowed(tmp_path, song):
    p = both(tmp_path, song)
    assert 'name="kind_s01"' in client(p).get("/shots").text
    write_json(p.root / "project.json", p.config | {"kinds": ["footage"]})
    assert 'name="kind_s01"' not in client(p).get("/shots").text


def shot_form(**kw):
    return {"action_s01": "a", "kind_s01": "animation", "scene_s01": "grid"} | kw


def test_switch_to_animation_then_set_params(tmp_path, song):
    p = both(tmp_path, song)
    c = client(p)
    assert c.post("/shots", data=shot_form()).status_code == 200
    s = p.load_shots()[0]
    assert s.kind == "animation" and s.scene == {"template": "grid", "params": {"cols": 8, "pulse": 0.6}}
    page = c.get("/shots").text
    assert 'name="param_s01_cols"' in page and 'value="8"' in page
    c.post("/shots", data=shot_form(param_s01_cols="9", param_s01_pulse="0.6"))
    assert p.load_shots()[0].scene["params"]["cols"] == 9
    before = p.shots_path.read_text()
    r = c.post("/shots", data=shot_form(param_s01_cols="999", param_s01_pulse="0.6"))
    assert r.status_code == 422 and "cols" in r.text and p.shots_path.read_text() == before
    # switching template resets params to the new template's defaults
    c.post("/shots", data=shot_form(scene_s01="rings", param_s01_cols="9"))
    assert p.load_shots()[0].scene["template"] == "rings" and "cols" not in p.load_shots()[0].scene["params"]


def test_custom_code_saved_to_scenes(tmp_path, song):
    p = both(tmp_path, song)
    client(p).post("/shots", data=shot_form(scene_s01="custom", code_s01="function draw(){}"))
    assert p.load_shots()[0].scene == {"file": "scenes/s01.js", "params": {}}
    assert (p.scenes_dir / "s01.js").read_text() == "function draw(){}"
    assert client(p).get("/shots").text.count("function draw(){}") == 1


def test_render_error_and_poster_shown(tmp_path, song):
    p = both(tmp_path, song)
    shots = [Shot("s01", 0.0, 2.0, "1", "a", [], None, "", "animation", {"template": "grid", "params": {"cols": 8, "pulse": 0.6}})]
    p.save_shots(shots, "x")
    (p.renders_dir).mkdir()
    (p.renders_dir / "s01.jpg").write_bytes(b"jpg")
    write_json(p.renders_path, {"s01": {"key": "k", "clip": "renders/s01.mp4", "poster": "renders/s01.jpg", "error": "scene threw"}})
    c = client(p)
    page = c.get("/shots").text
    assert "scene threw" in page and "/files/renders/s01.jpg" in page and c.get("/files/renders/s01.jpg").status_code == 200


def test_kinds_form_saves_and_rejects_empty(tmp_path, song):
    p = project(tmp_path, song)
    c = client(p)
    assert c.post("/kinds", data={"kinds": ["animation"]}).status_code == 200 and p.kinds == ["animation"]
    assert c.post("/kinds", data={}).status_code == 422 and p.kinds == ["animation"]
    with run_lock(p):
        assert c.post("/kinds", data={"kinds": ["footage"]}).status_code == 409


def test_old_project_pages_unchanged(tmp_path, song):
    p = project(tmp_path, song)
    page = client(p).get("/shots").text
    assert 'name="kind_' not in page and 'name="scene_' not in page and 'name="framing_s01"' in page
    assert client(p).post("/shots", data={"action_s01": "x", "cast_s01": ["A"], "place_s01": "P", "framing_s01": "w"}).status_code == 200
    assert p.load_shots()[0].kind == "footage" and p.load_shots()[0].scene is None
