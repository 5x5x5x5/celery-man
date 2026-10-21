import threading

from fastapi.testclient import TestClient

from celery_man.generate import FakeGenerator
from celery_man.ledger import Ledger
from celery_man.pipeline import advance
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
    return TestClient(create_app(p, gen_factory, lambda p: (text_chat, vision_chat())))


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
    assert [s.state for s in advance(p, FakeGenerator())] == ["done"] * 6


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
    c = TestClient(app)
    assert c.post("/run", follow_redirects=False).status_code == 303
    assert "running" in c.get("/").text
    assert c.post("/run").status_code == 409
    go.set()
    app.state.runner.thread.join(10)
    assert made and read_json(p.keyframes_path)["s01"]["approved"] is None
    assert c.post("/run", follow_redirects=False).status_code == 303
    app.state.runner.thread.join(10)


def test_run_error_is_shown(tmp_path, song):
    p = project(tmp_path, song, brief=False, shots=False)
    write_json(p.root / "project.json", p.config | {"modes": DEFAULT_MODES | {"brief": "auto"}})

    def boom():
        raise RuntimeError("no generator today")

    app = create_app(p, boom, lambda p: (None, None))
    c = TestClient(app)
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
