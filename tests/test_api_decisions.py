import json
import threading

from fastapi.testclient import TestClient

from celery_man.generate import FakeGenerator
from celery_man.ledger import Ledger
from celery_man.pipeline import advance, held_run_lock, run_lock
from celery_man.project import DEFAULT_MODES, read_json, write_json
from celery_man.steps import EST_STILL_S, STILL_MODEL, ensure_open_shots
from celery_man.web.app import create_app
from tests.test_api_core import decided, wait_jobs
from tests.test_pipeline import BRIEF, make, text_chat, vision_chat

U = "/api/projects/proj"


def client(root, gen=FakeGenerator, chats=None) -> TestClient:
    chats = chats or (lambda p: (text_chat, vision_chat()))
    return TestClient(create_app(root, gen, chats), base_url="http://127.0.0.1:8765")


def no_chat(system, user, images):
    raise AssertionError("the model was called")


def shot(c, sid="s01") -> dict:
    return next(s["shot"] for s in c.get(f"{U}/state").json()["shots"] if s["shot"]["id"] == sid)


def with_takes(tmp_path, song):
    """A decided project whose shots have approved keyframes and a landed round of takes."""
    p = decided(tmp_path, song)
    advance(p, FakeGenerator())
    write_json(p.keyframes_path, {k: v | {"approved": True} for k, v in read_json(p.keyframes_path).items()})
    advance(p, FakeGenerator())
    return p


def test_put_shot_rejects_window_change(tmp_path, song):
    p = decided(tmp_path, song)
    c = client(p.root)
    s = shot(c)
    before = p.shots_path.read_text()
    assert c.put(f"{U}/shots/s01", json={"shot": s | {"end": 9}}).status_code == 422
    assert c.put(f"{U}/shots/s01", json={"shot": s | {"id": "s02"}}).status_code == 422
    assert (
        c.put(f"{U}/shots/s01", json={"shot": s | {"kind": "animation", "scene": {"template": "rings", "params": {}}}}).status_code == 422
    )
    assert p.shots_path.read_text() == before
    r = c.put(f"{U}/shots/s01", json={"shot": s | {"action": "a person sits"}})
    assert r.status_code == 200 and p.load_shots()[0].action == "a person sits"


def test_put_shot_too_long_prompt_422(tmp_path, song):
    p = decided(tmp_path, song)
    c = client(p.root)
    r = c.put(f"{U}/shots/s01", json={"shot": shot(c) | {"action": "a person walks " * 200}})
    assert r.status_code == 422 and r.json()["error"].startswith("prompt is")


def test_brief_put_suggest_use(tmp_path, song):
    p = make(tmp_path, song, DEFAULT_MODES)
    three = [BRIEF | {"logline": f"a person walks {i}"} for i in range(3)]
    c = client(p.root, chats=lambda p: (lambda s, u, i: json.dumps(three), no_chat))
    assert c.put(f"{U}/brief", json={"brief": BRIEF | {"cast": "A"}}).status_code == 422
    assert c.put(f"{U}/brief", json={"brief": BRIEF}).status_code == 200 and p.load_brief().logline == "a person walks"
    assert c.post(f"{U}/suggest/brief", json={"steer": "slower"}).status_code == 202
    items = wait_jobs(c, "proj")["candidates"]["brief"]["items"]
    assert len(items) == 3
    assert c.post(f"{U}/brief/use", json={"id": items[2]["id"]}).status_code == 200
    assert p.load_brief().logline == "a person walks 2"
    assert c.post(f"{U}/brief/use", json={"id": "nope"}).status_code == 422


def test_continue_price_rise_refused_nothing_spent(tmp_path, song):
    p = decided(tmp_path, song)
    c = client(p.root)
    rows = len(Ledger(p.ledger_path).rows())
    r = c.post(f"{U}/continue", json={"price": 0.0})
    assert r.status_code == 409 and r.json()["price"] > 0 and len(Ledger(p.ledger_path).rows()) == rows
    assert c.get(f"{U}/state").json()["jobs"]["queued"] == []


def test_over_reserve_refused(tmp_path, song):
    p = decided(tmp_path, song)
    write_json(p.root / "project.json", p.config | {"cap_usd": 0.01})
    c = client(p.root)
    price = c.get(f"{U}/state").json()["prices"]["keyframe_set"]
    r = c.post(f"{U}/suggest/keyframes/s01", json={"price": price})
    assert r.status_code == 409 and "reserve" in r.json()["reason"]
    r = c.post(f"{U}/suggest/shots", json={"shots": ["s01"], "steer": "", "price": price})
    assert r.status_code == 409 and "reserve" in r.json()["reason"]


def test_continue_only_one_shot(tmp_path, song):
    p = decided(tmp_path, song)
    c = client(p.root)
    price = c.get(f"{U}/state").json()["prices"]["continue"]
    assert c.post(f"{U}/continue", json={"price": price, "only": ["s99"]}).status_code == 422
    r = c.post(f"{U}/continue", json={"price": price, "only": ["s01"]})
    assert r.status_code == 202 and r.json()["job"]
    st = wait_jobs(c, "proj")
    assert st["jobs"]["errors"] == [] and set(read_json(p.keyframes_path)) == {"s01"}


def test_suggest_shots_then_use(tmp_path, song):
    p = decided(tmp_path, song)
    idea = {"kind": "footage", "action": "a person sits", "cast": ["A"], "place": "P", "framing": "close"}
    c = client(p.root, chats=lambda p: (lambda s, u, i: json.dumps([idea] * 3), no_chat))
    price = c.get(f"{U}/state").json()["prices"]["still"] * 3
    assert c.post(f"{U}/suggest/shots", json={"shots": ["s01"], "steer": "", "price": price - 0.02}).status_code == 409
    assert c.post(f"{U}/suggest/shots", json={"shots": ["s01"], "steer": "", "price": price}).status_code == 202
    items = wait_jobs(c, "proj")["candidates"]["shots"]["s01"]["items"]
    assert len(items) == 3 and all(i["still"] for i in items)
    assert c.post(f"{U}/shots/s01/use", json={"id": items[0]["id"]}).status_code == 200
    assert p.load_shots()[0].action == "a person sits"


def test_suggest_keyframes_then_use(tmp_path, song):
    p = decided(tmp_path, song)
    c = client(p.root)
    price = c.get(f"{U}/state").json()["prices"]["keyframe_set"]
    assert c.post(f"{U}/suggest/keyframes/s01", json={"price": price}).status_code == 202
    items = wait_jobs(c, "proj")["candidates"]["keyframes"]["s01"]["items"]
    assert len(items) == 4
    assert c.post(f"{U}/keyframes/s01/use", json={"id": items[1]["id"]}).status_code == 200
    kf = read_json(p.keyframes_path)["s01"]
    assert kf["approved"] is True and kf["seed"] == items[1]["seed"]
    assert c.post(f"{U}/keyframes/s01/none", json={"note": "closer"}).status_code == 200
    cands = c.get(f"{U}/state").json()["candidates"]
    assert cands["keyframes"]["s01"]["items"] == [] and cands["shots"]["s01"]["note"] == "closer"


def test_pick_in_point_out_of_range_422(tmp_path, song):
    p = with_takes(tmp_path, song)
    c = client(p.root)
    takes = c.get(f"{U}/state").json()["shots"][0]["takes"]
    key = takes["takes"][0]["key"]
    assert c.post(f"{U}/takes/s01/pick", json={"key": key, "in": 99}).status_code == 422
    assert c.post(f"{U}/takes/s01/pick", json={"key": key, "in": -1}).status_code == 422
    assert c.post(f"{U}/takes/s01/pick", json={"key": "nope", "in": 0.5}).status_code == 422
    assert "s01" not in (read_json(p.picks_path) or {})
    assert c.post(f"{U}/takes/s01/pick", json={"key": key, "in": 0.5}).status_code == 200
    pick = read_json(p.picks_path)["s01"]
    assert pick == {"key": key, "clip": f"takes/{key}.mp4", "in": 0.5, "round": 1, "gen": 1, "quick": False}
    assert c.get(f"{U}/state").json()["shots"][0]["takes"]["need"] == "done"


def test_reject_with_edited_shot_skips_model(tmp_path, song):
    p = with_takes(tmp_path, song)
    c = client(p.root, chats=lambda p: (no_chat, no_chat))
    s = shot(c)
    assert c.post(f"{U}/takes/s01/reject", json={"reason": "morphing", "shot": s | {"end": 9}}).status_code == 422
    assert "s01" not in (read_json(p.picks_path) or {})
    r = c.post(f"{U}/takes/s01/reject", json={"reason": "morphing", "shot": s | {"action": "a person stands"}})
    assert r.status_code == 200
    assert read_json(p.picks_path)["s01"] == {"rejected": True, "round": 1, "gen": 1, "reason": "morphing"}
    assert p.load_shots()[0].action == "a person stands"
    st = wait_jobs(c, "proj")
    assert st["jobs"]["errors"] == [] and st["shots"][0]["keyframe"]["need"] == "gen"  # the edit is the rewrite


def test_reject_without_shot_rewrites(tmp_path, song):
    p = with_takes(tmp_path, song)
    c = client(p.root)
    assert c.post(f"{U}/takes/s01/reject", json={"reason": "morphing"}).status_code == 202
    st = wait_jobs(c, "proj")
    assert st["jobs"]["errors"] == [] and p.load_shots()[0].action == "a person stands"


def test_upload_bad_keyframe_422_nothing_written(tmp_path, song):
    p = decided(tmp_path, song)
    r = client(p.root).post(f"{U}/uploads/keyframe/s01", files={"file": ("a.png", b"not an image")})
    assert r.status_code == 422 and "PNG" in r.json()["error"]
    assert not p.keyframes_path.exists() and not list(p.keys_dir.glob("*"))


def test_assemble_route_makes_video(tmp_path, song):
    p = make(tmp_path, song, DEFAULT_MODES)
    ensure_open_shots(p)
    c = client(p.root)
    assert c.post(f"{U}/assemble", json={}).status_code == 202
    assert wait_jobs(c, "proj", timeout=120)["jobs"]["errors"] == []
    assert (p.out_dir / "video.mp4").is_file()


class SlowGen(FakeGenerator):
    """Holds the paid worker in stills until go is set; wall_s: what each still is charged (the fake's own is 1 s)."""

    def __init__(self, wall_s=None):
        super().__init__()
        self.go, self.started, self.wall_s = threading.Event(), threading.Event(), wall_s

    def stills(self, jobs):
        self.started.set()
        assert self.go.wait(20)
        return (r | {"wall_s": self.wall_s or r["wall_s"]} for r in super().stills(jobs))


def test_decision_while_paid_job_runs(tmp_path, song):
    p = decided(tmp_path, song)
    slow = SlowGen()
    c = client(p.root, gen=lambda: slow)
    price = c.get(f"{U}/state").json()["prices"]["keyframe_set"]
    try:
        assert c.post(f"{U}/suggest/keyframes/s01", json={"price": price}).status_code == 202
        assert slow.started.wait(20)
        s = shot(c)
        assert c.put(f"{U}/shots/s01", json={"shot": s | {"action": "a person sits"}}).status_code == 200
        assert c.get(f"{U}/state").json()["jobs"]["running"] is not None
        assert p.load_shots()[0].action == "a person sits"
    finally:
        slow.go.set()
    assert wait_jobs(c, "proj")["jobs"]["errors"] == []


def test_nested_held_run_lock_keeps_outer_marker(tmp_path, song):
    p = make(tmp_path, song)
    with held_run_lock(p):
        with held_run_lock(p):
            pass
        with run_lock(p):  # still ours: the inner exit must not drop the outer holder's marker
            pass


def test_queued_paid_job_rechecks_reserve(tmp_path, song):
    p = decided(tmp_path, song)
    gen = SlowGen(wall_s=EST_STILL_S)  # charged as estimated, so the first set really uses up the room
    gen.go.set()
    c = client(p.root, gen=lambda: gen)
    st = c.get(f"{U}/state").json()
    price = st["prices"]["keyframe_set"]
    write_json(p.root / "project.json", p.config | {"cap_usd": st["spend"]["spent"] + st["spend"]["reserve"] + 1.5 * price})
    hold = threading.Event()
    c.app.state.jobs["proj"].paid("hold", lambda: hold.wait(20))  # the worker is busy, so both clicks queue
    try:
        for _ in range(2):  # each fits at the click: nothing is spent yet
            assert c.post(f"{U}/suggest/keyframes/s01", json={"price": price}).status_code == 202
    finally:
        hold.set()
    errors = wait_jobs(c, "proj")["jobs"]["errors"]
    assert len(errors) == 1 and errors[0]["name"] == "suggest keyframes s01" and errors[0]["error"].startswith("OverReserve")
    assert sum(r.get("model") == STILL_MODEL for r in Ledger(p.ledger_path).rows()) == 4


def test_rewrite_skipped_when_shot_edited_meanwhile(tmp_path, song):
    p = with_takes(tmp_path, song)
    asked, go = threading.Event(), threading.Event()

    def slow_chat(system, user, images):
        asked.set()
        assert go.wait(20)
        return text_chat(system, user, images)

    c = client(p.root, chats=lambda p: (slow_chat, no_chat))
    try:
        assert c.post(f"{U}/takes/s01/reject", json={"reason": "morphing"}).status_code == 202
        assert asked.wait(20)
        assert c.put(f"{U}/shots/s01", json={"shot": shot(c) | {"action": "a person sits"}}).status_code == 200
    finally:
        go.set()
    assert wait_jobs(c, "proj")["jobs"]["errors"] == []
    assert p.load_shots()[0].action == "a person sits"


def test_continue_only_must_be_ids(tmp_path, song):
    p = decided(tmp_path, song)
    c = client(p.root)
    price = c.get(f"{U}/state").json()["prices"]["continue"]
    for bad in ([1], [["s01"]], [None], "s01"):
        assert c.post(f"{U}/continue", json={"price": price, "only": bad}).status_code == 422, bad
