import hashlib
import threading
import time

from fastapi.testclient import TestClient

from celery_man import steps
from celery_man.generate import FakeGenerator
from celery_man.pipeline import advance, run_busy, run_lock
from celery_man.project import DEFAULT_MODES, Project, Shot, brief_from_dict, read_json, write_json
from celery_man.steps import brief_inputs, shots_inputs, windows
from celery_man.templates import catalogue
from celery_man.web import app as web_app
from celery_man.web.app import create_app
from celery_man.web.jobs import Jobs
from tests.test_pipeline import BRIEF, make, text_chat, vision_chat

STATE_KEYS = {"project", "timing", "brief", "shots", "steps", "spend", "prices", "candidates", "jobs", "cut"}


def client(root) -> TestClient:
    return TestClient(create_app(root, FakeGenerator, lambda p: (text_chat, vision_chat())), base_url="http://127.0.0.1:8765")


def decided(tmp_path, song) -> Project:
    """tmp_path/proj: a project with a brief and every shot decided (footage)."""
    p = make(tmp_path, song, DEFAULT_MODES)
    write_json(p.root / "project.json", p.config | {"range": [0, 4]})
    p.save_brief(brief_from_dict(BRIEF), brief_inputs(p))
    p.save_shots([Shot(f"s{i:02d}", a, z, s, "a person walks", ["A"], "P", "wide shot") for i, (a, z, s) in enumerate(windows(p), 1)],
                 shots_inputs(p, p.load_brief()))  # fmt: skip
    return p


def fake_detect(song):
    beats = [i * 0.5 for i in range(81)]
    return {"duration": 40.0, "beats": beats, "downbeats": beats[::4], "tempo": [[0.0, 120.0]],
            "sections": [{"start": 0.0, "end": 16.0, "label": "1"}, {"start": 16.0, "end": 40.0, "label": "2"}],
            "confidence": 1.0, "song_sha": hashlib.sha256(song.read_bytes()).hexdigest()[:12]}  # fmt: skip


def idle(jobs: dict) -> bool:
    return jobs["running"] is None and not jobs["queued"] and not jobs["free"]


def wait_jobs(c, pid, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = c.get(f"/api/projects/{pid}/state").json()
        if idle(st["jobs"]):
            return st
        time.sleep(0.05)
    raise AssertionError("jobs did not finish")


def test_lists_folder_of_projects(tmp_path, song):
    Project.create(tmp_path / "one", song, "s")
    Project.create(tmp_path / "two", song, "s", ("animation",))
    (tmp_path / "not-a-project").mkdir()
    assert client(tmp_path).get("/api/projects").json() == [
        {"id": "one", "song": "song.wav", "kinds": ["footage"]},
        {"id": "two", "song": "song.wav", "kinds": ["animation"]},
    ]


def test_single_project_folder_served(tmp_path, song):
    Project.create(tmp_path / "solo", song, "s")
    c = client(tmp_path / "solo")
    assert [x["id"] for x in c.get("/api/projects").json()] == ["solo"]
    st = c.get("/api/projects/solo/state").json()
    assert st["project"]["id"] == "solo" and st["timing"] is None and st["shots"] == [] and st["cut"] == []
    assert c.get("/api/projects/other/state").status_code == 404


def test_create_project_runs_timing(tmp_path, song, monkeypatch):
    monkeypatch.setattr(steps, "detect", fake_detect)
    c = client(tmp_path)
    r = c.post("/api/projects", data={"name": "one", "source": "s", "kinds": "footage,animation"}, files={"song": song.open("rb")})
    assert r.status_code == 200 and r.json() == {"id": "one"}
    st = wait_jobs(c, "one")
    assert st["timing"]["windows"] and all(s["open"] for s in st["shots"])
    assert [s["shot"]["id"] for s in st["shots"]] == [w["id"] for w in st["timing"]["windows"]]
    assert st["project"]["kinds"] == ["footage", "animation"] and st["project"]["song_source"] == "s"
    assert st["jobs"]["errors"] == []
    again = c.post("/api/projects", data={"name": "one", "source": "s", "kinds": "footage"}, files={"song": song.open("rb")})
    assert again.status_code == 409


def test_create_rejects_non_audio(tmp_path):
    r = client(tmp_path).post("/api/projects", data={"name": "one", "source": "s", "kinds": "footage"}, files={"song": ("a.mp3", b"text")})
    assert r.status_code == 422 and not (tmp_path / "one").exists()


def test_create_rejects_bad_name_and_kinds(tmp_path, song):
    c = client(tmp_path)
    for name, kinds in (("../x", "footage"), ("", "footage"), ("a b", "footage"), ("ok", "footage,sideways"), ("ok", "")):
        r = c.post("/api/projects", data={"name": name, "source": "s", "kinds": kinds}, files={"song": song.open("rb")})
        assert r.status_code == 422, (name, kinds)
    assert list(tmp_path.iterdir()) == []


def test_range_change_with_decided_shots_refused(tmp_path, song):
    p = decided(tmp_path, song)
    c, u = client(p.root), "/api/projects/proj"
    before = p.shots_path.read_text()
    assert c.patch(u, json={"range": [0, 8]}).status_code == 409
    assert c.patch(u, json={"kinds": ["animation"]}).status_code == 409
    assert p.shots_path.read_text() == before and p.range == [0, 4]
    assert c.patch(u, json={"range": [0, 8], "reset_shots": True}).status_code == 200
    shots = p.load_shots()
    assert p.range == [0, 8] and all(s.is_open for s in shots) and len(shots) == len(windows(p))


def test_range_change_reopens_open_shots_and_validates(tmp_path, song):
    p = make(tmp_path, song, DEFAULT_MODES)
    steps.ensure_open_shots(p)
    c, u = client(p.root), "/api/projects/proj"
    assert c.patch(u, json={"range": [0, 8]}).status_code == 200
    assert len(p.load_shots()) == len(windows(p)) and all(s.is_open for s in p.load_shots())
    assert c.patch(u, json={"seed": "a person walks", "vision_flags": True}).status_code == 200
    assert p.config["seed"] == "a person walks" and p.config["vision_flags"] is True
    for bad in ({"range": [8, 8]}, {"range": "x"}, {"kinds": []}, {"seed": 3}, {"vision_flags": "yes"}, {"colour": 1}):
        assert c.patch(u, json=bad).status_code == 422, bad
    assert p.range == [0, 8]


def test_shot_modes(tmp_path, song):
    p = decided(tmp_path, song)
    c, u = client(p.root), "/api/projects/proj/shot-modes"
    assert c.put(u, json={"shots": ["s01"], "step": "takes", "mode": "auto"}).status_code == 200
    assert p.mode("takes", "s01") == "auto" and p.modes["takes"] == "tastemaker"
    assert c.put(u, json={"shots": [], "step": "brief", "mode": "auto"}).status_code == 200
    assert p.modes["brief"] == "auto" and p.mode("takes", "s01") == "auto"
    for bad in ({"shots": ["s01"], "step": "takes", "mode": "sideways"}, {"shots": ["s99"], "step": "takes", "mode": "auto"},
                {"shots": ["s01"], "step": "brief", "mode": "auto"}):  # fmt: skip
        assert c.put(u, json=bad).status_code == 422, bad


def test_cross_site_post_refused(tmp_path, song):
    c = client(tmp_path)
    form = {"data": {"name": "one", "source": "s", "kinds": "footage"}}
    assert c.post("/api/projects", headers={"Origin": "http://evil.example"}, files={"song": song.open("rb")}, **form).status_code == 403
    assert c.post("/api/projects", headers={"Referer": "http://evil.example/x"}, files={"song": song.open("rb")}, **form).status_code == 403
    assert c.get("/api/projects", headers={"Host": "evil.example:8765"}).status_code == 400
    assert not (tmp_path / "one").exists()


def test_job_error_recorded_and_worker_survives(tmp_path, song):
    p = make(tmp_path, song)
    jobs, ran = Jobs(p), threading.Event()

    def boom():
        raise RuntimeError("no generator today")

    bad = jobs.paid("bad", boom)
    jobs.paid("good", ran.set)
    assert ran.wait(10)
    assert jobs.snapshot()["errors"] == [{"id": bad, "name": "bad", "error": "RuntimeError: no generator today"}]
    jobs.free("bad free", boom)
    deadline = time.monotonic() + 10
    while len(jobs.snapshot()["errors"]) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [e["name"] for e in jobs.snapshot()["errors"]] == ["bad", "bad free"]


def test_paid_jobs_fifo_holding_run_lock(tmp_path, song):
    p = make(tmp_path, song)
    jobs, go, order = Jobs(p), threading.Event(), []

    def first():
        go.wait(10)
        with run_lock(p):  # what advance does inside a paid job: the worker's own lock lets it in
            order.append("first")

    a = jobs.paid("first", first)
    b = jobs.paid("second", lambda: order.append("second"))
    deadline = time.monotonic() + 10
    while jobs.snapshot()["running"] is None and time.monotonic() < deadline:
        time.sleep(0.01)
    snap = jobs.snapshot()
    assert snap["running"]["id"] == a and snap["running"]["started"] and [j["id"] for j in snap["queued"]] == [b]
    assert run_busy(p)  # the CLI is kept out while a paid job runs
    go.set()
    while not idle(jobs.snapshot()) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert order == ["first", "second"] and jobs.snapshot()["errors"] == [] and not run_busy(p)


def test_paid_job_busy_while_another_process_runs(tmp_path, song):
    p = make(tmp_path, song)
    jobs, ran = Jobs(p), []
    with run_lock(p):
        jobs.paid("blocked", lambda: ran.append(1))
        deadline = time.monotonic() + 10
        while not jobs.snapshot()["errors"] and time.monotonic() < deadline:
            time.sleep(0.01)
    assert not ran and jobs.snapshot()["errors"][0]["error"].startswith("BusyError")


def test_state_shape_keys(tmp_path, song):
    p = decided(tmp_path, song)
    st = client(p.root).get("/api/projects/proj/state").json()
    assert set(st) == STATE_KEYS
    assert set(st["project"]) == {"id", "song", "song_source", "range", "kinds", "cap_usd", "modes", "shot_modes", "seed", "vision_flags"}
    assert set(st["timing"]) == {"duration", "windows"} and set(st["timing"]["windows"][0]) == {"id", "start", "end", "section"}
    assert st["brief"]["logline"] == "a person walks" and "inputs" not in st["brief"]
    assert set(st["shots"][0]) == {"shot", "open", "modes", "render", "keyframe", "takes"}
    assert st["steps"][0]["step"] == "timing" and set(st["steps"][0]) == {"step", "state", "detail"}
    assert set(st["spend"]) == {"spent", "reserve", "cap"} and set(st["prices"]) == {"continue", "still", "keyframe_set", "take_round"}
    assert st["candidates"] == {"brief": None, "shots": {}, "keyframes": {}}
    assert set(st["jobs"]) == {"running", "queued", "free", "errors"} and len(st["cut"]) == len(st["shots"])


def test_state_per_shot_needs(tmp_path, song):
    p = decided(tmp_path, song)
    advance(p, FakeGenerator())
    write_json(p.keyframes_path, {k: v | {"approved": True} for k, v in read_json(p.keyframes_path).items()})
    advance(p, FakeGenerator())
    anim = {"template": min(catalogue()), "params": {}}
    write_json(p.root / "project.json", p.config | {"kinds": ["footage", "animation"]})
    shots = read_json(p.shots_path)
    shots["shots"].append(Shot("s99", 9, 10, "1", "a person walks", ["A"], "P", "wide", "animation", anim).__dict__)
    write_json(p.shots_path, shots)
    (p.root / "candidates" / "keyframes").mkdir(parents=True)
    write_json(p.root / "candidates" / "keyframes" / "s01.json", {"items": []})
    st = client(p.root).get("/api/projects/proj/state").json()
    s01 = st["shots"][0]
    assert s01["modes"] == {"shots": "tastemaker", "keyframes": "tastemaker", "takes": "tastemaker"} and s01["render"] is None
    assert s01["keyframe"]["need"] == "done" and s01["keyframe"]["entry"]["approved"] is True
    assert s01["keyframe"]["changed_since_upload"] is False
    t = s01["takes"]
    assert t["need"] == "judge" and t["round"] == 1 and t["cap"] is None and t["pick"] is None and len(t["takes"]) == 3
    assert all(x["sheet"].startswith("sheets/") and x["clip"].startswith("takes/") and x["seed"] for x in t["takes"])
    s99 = st["shots"][-1]
    assert s99["render"] == {"need": "gen", "clip": None, "poster": None, "error": None} and s99["takes"] is None
    assert st["candidates"]["keyframes"] == {"s01": {"items": []}}


def test_files_blocks_traversal(tmp_path, song):
    p = decided(tmp_path, song)
    (tmp_path / "secret.txt").write_text("x")
    (p.root / "link.txt").symlink_to(tmp_path / "secret.txt")
    c, u = client(p.root), "/api/projects/proj/files"
    assert c.get(f"{u}/../x").status_code == 404
    assert c.get(f"{u}/..%2F..%2Fsecret.txt").status_code == 404
    assert c.get(f"{u}/%2E%2E/%2E%2E/secret.txt").status_code == 404
    assert c.get(f"{u}/link.txt").status_code == 404
    assert c.get(f"{u}/project.json").status_code == 200


def test_front_end_served_or_503(tmp_path, song, monkeypatch):
    Project.create(tmp_path / "solo", song, "s")
    monkeypatch.setattr(web_app, "DIST", tmp_path / "dist")
    c = client(tmp_path / "solo")
    r = c.get("/")
    assert r.status_code == 503 and r.text == "front end not built: npm --prefix web ci && npm --prefix web run build"
    (tmp_path / "dist" / "assets").mkdir(parents=True)
    (tmp_path / "dist" / "index.html").write_text("<p>app</p>")
    (tmp_path / "dist" / "assets" / "a.js").write_text("1")
    assert c.get("/").text == "<p>app</p>" and c.get("/assets/a.js").text == "1"
    assert c.get("/assets/%2E%2E/%2E%2E/solo/project.json").status_code == 404
    assert c.get("/api/nothing").status_code == 404
