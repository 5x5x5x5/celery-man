import json

import pytest

from celery_man import auto, suggest
from celery_man.compose import PromptTooLong, compose
from celery_man.generate import FakeGenerator
from celery_man.ledger import Ledger
from celery_man.project import Brief, CastMember, Place, Shot, brief_from_dict, read_json, write_json
from celery_man.steps import STILL_MODEL, Run, ensure_open_shots, shots_inputs, windows
from tests.test_pipeline import BRIEF, make

STYLE = {"medium": "ink", "palette": "grey", "lighting": "soft", "camera": "static"}
BRIEF_OBJ = Brief("l", STYLE, [CastMember("A", "a tall person")], [Place("P", "a room")], [])
W = (8.0, 12.0, "x")
FOOT = {"kind": "footage", "action": "a person walks", "cast": ["A"], "place": "P", "framing": "wide shot"}
MIXED = [FOOT, FOOT | {"action": "a person sits"}, {"kind": "animation", "action": "rings pulse", "template": "rings", "params": {}}]
THREE_BRIEFS = [BRIEF | {"logline": f"a person walks {i}"} for i in range(3)]


def fake_chat(reply):
    def chat(system, user, images):
        chat.calls.append((system, user))
        return json.dumps(reply)

    chat.calls = []
    return chat


def setup(tmp_path, song, kinds=("footage", "animation")):
    p = make(tmp_path, song)
    write_json(p.root / "project.json", p.config | {"kinds": list(kinds)})
    p.save_brief(brief_from_dict(BRIEF), "x")
    ensure_open_shots(p)
    return p


def decide_footage(p):
    brief = p.load_brief()
    shots = [Shot(f"s{i:02d}", a, b, s, "a person walks", ["A"], "P", "wide shot") for i, (a, b, s) in enumerate(windows(p), 1)]
    p.save_shots(shots, shots_inputs(p, brief))


def test_suggest_briefs_three():
    chat = fake_chat(THREE_BRIEFS)
    briefs = auto.suggest_briefs(chat, "", "")
    assert len(briefs) == 3 and briefs[2].logline == "a person walks 2"


def test_suggest_shots_mixed_kinds():
    chat = fake_chat(MIXED)
    ideas = auto.suggest_shots(chat, BRIEF_OBJ, "s03", W, ["footage", "animation"], [], "slower")
    assert {s.kind for s, _ in ideas} == {"footage", "animation"} and all(s.id == "s03" for s, _ in ideas)
    assert all((s.start, s.end, s.section) == W for s, _ in ideas)
    assert "At least one idea is a footage shot" in chat.calls[0][0] and "slower" in chat.calls[0][1]


def test_suggest_shots_code_idea():
    code = "function setup(env) {}\nfunction draw(ctx, f, env) {}"
    ideas = auto.suggest_shots(
        fake_chat([{"kind": "animation", "action": "a", "code": code}] * 3), BRIEF_OBJ, "s03", W, ["animation"], [], ""
    )
    assert ideas[0][0].scene == {"file": "scenes/s03.js", "params": {}} and ideas[0][1] == code


def test_shot_suggestions_get_paid_stills(tmp_path, song):
    p = setup(tmp_path, song)
    suggest.shots(Run(p, FakeGenerator(), chat_text=fake_chat(MIXED)), "s03", "")
    items = read_json(p.root / "candidates/shots/s03.json")["items"]
    assert len(items) == 3 and all((i["still"] is not None) == (i["shot"]["kind"] == "footage") for i in items)
    assert all((p.root / i["still"]).is_file() for i in items if i["still"])
    stills = sum(r.get("model") == STILL_MODEL for r in Ledger(p.ledger_path).rows())
    assert stills == sum(i["shot"]["kind"] == "footage" for i in items)


def test_too_long_footage_idea_keeps_error(tmp_path, song, monkeypatch):
    p = setup(tmp_path, song)
    long = Shot("s03", *W, "a person walks " * 200, ["A"], "P", "wide shot")
    monkeypatch.setattr(auto, "suggest_shots", lambda *a, **k: [(long, None)])
    with pytest.raises(PromptTooLong):
        compose(long, p.load_brief())
    suggest.shots(Run(p, FakeGenerator(), chat_text=fake_chat([])), "s03", "")
    (item,) = read_json(p.root / "candidates/shots/s03.json")["items"]
    assert item["still"] is None and item["error"]
    assert not any(r.get("model") == STILL_MODEL for r in Ledger(p.ledger_path).rows())


def test_use_shot_writes_scene_and_decision(tmp_path, song):
    p = setup(tmp_path, song)
    code = "function setup(env) {}\nfunction draw(ctx, f, env) {}"
    suggest.shots(Run(p, FakeGenerator(), chat_text=fake_chat([{"kind": "animation", "action": "a", "code": code}] * 3)), "s02", "")
    cid = read_json(p.root / "candidates/shots/s02.json")["items"][0]["id"]
    suggest.use_shot(p, "s02", cid)
    s02 = {s.id: s for s in p.load_shots()}["s02"]
    assert s02.kind == "animation" and (p.scenes_dir / "s02.js").read_text() == code


def test_keyframe_suggestions_four_new_seeds(tmp_path, song):
    p = setup(tmp_path, song)
    decide_footage(p)
    run = Run(p, FakeGenerator())
    suggest.keyframes(run, "s01")
    suggest.keyframes(run, "s01")
    seeds = [i["seed"] for i in read_json(p.root / "candidates/keyframes/s01.json")["items"]]
    assert len(seeds) == 8 and len(set(seeds)) == 8
    suggest.keyframes(run, "s01", near_seed=3)
    assert [i["seed"] for i in read_json(p.root / "candidates/keyframes/s01.json")["items"]][8:] == [31, 32, 33, 34]


def test_keyframe_flags_when_enabled(tmp_path, song):
    p = setup(tmp_path, song)
    decide_footage(p)
    write_json(p.root / "project.json", p.config | {"vision_flags": True})
    suggest.keyframes(Run(p, FakeGenerator(), chat_vision=fake_chat({"ok": False, "why": "blurred"})), "s01")
    assert [i["flag"] for i in read_json(p.root / "candidates/keyframes/s01.json")["items"]] == ["blurred"] * 4


def test_use_keyframe_approves(tmp_path, song):
    p = setup(tmp_path, song)
    decide_footage(p)
    suggest.keyframes(Run(p, FakeGenerator()), "s01")
    item = read_json(p.root / "candidates/keyframes/s01.json")["items"][1]
    suggest.use_keyframe(p, "s01", item["id"])
    kf = read_json(p.keyframes_path)["s01"]
    assert kf["approved"] is True and kf["path"] == item["path"] and kf["seed"] == item["seed"]


def test_use_brief_saves_it(tmp_path, song):
    p = setup(tmp_path, song)
    suggest.briefs(Run(p, FakeGenerator(), chat_text=fake_chat(THREE_BRIEFS)), "")
    cid = read_json(p.root / "candidates/brief.json")["items"][1]["id"]
    suggest.use_brief(p, cid)
    assert p.load_brief().logline == "a person walks 1"


def test_reopen_note_is_default_steer(tmp_path, song):
    p = setup(tmp_path, song)
    suggest.reopen_note(p, "s03", "closer")
    chat = fake_chat(MIXED)
    suggest.shots(Run(p, FakeGenerator(), chat_text=chat), "s03", "")
    assert "closer" in chat.calls[0][1] and read_json(p.root / "candidates/shots/s03.json")["note"] == "closer"


def test_suggestions_never_touch_decisions(tmp_path, song):
    p = setup(tmp_path, song)
    run = Run(p, FakeGenerator(), chat_text=fake_chat(THREE_BRIEFS))
    before = {f: f.read_bytes() for f in [p.shots_path, p.brief_path, p.keyframes_path] if f.exists()}
    suggest.briefs(run, "")
    run.chat_text = fake_chat(MIXED)
    suggest.shots(run, "s02", "")
    assert before == {f: f.read_bytes() for f in before} and not p.keyframes_path.exists()


def test_failed_vision_check_keeps_paid_stills(tmp_path, song):
    p = setup(tmp_path, song)
    decide_footage(p)
    write_json(p.root / "project.json", p.config | {"vision_flags": True})
    suggest.keyframes(Run(p, FakeGenerator(), chat_vision=lambda s, u, i: "no json"), "s01")
    assert [i["flag"] for i in read_json(p.root / "candidates/keyframes/s01.json")["items"]] == [None] * 4


def test_variations_twice_never_repeat_seeds(tmp_path, song):
    p = setup(tmp_path, song)
    decide_footage(p)
    run = Run(p, FakeGenerator())
    suggest.keyframes(run, "s01", near_seed=1)
    suggest.keyframes(run, "s01", near_seed=1)
    suggest.keyframes(run, "s01")
    seeds = [i["seed"] for i in read_json(p.root / "candidates/keyframes/s01.json")["items"]]
    assert seeds[:8] == [11, 12, 13, 14, 15, 16, 17, 18] and len(set(seeds)) == 12


def test_reasked_shot_ideas_never_repeat_still_jobs(tmp_path, song):
    p = setup(tmp_path, song)
    run = Run(p, FakeGenerator(), chat_text=fake_chat(MIXED))
    suggest.shots(run, "s03", "")
    suggest.shots(run, "s03", "")
    jobs = [(r["prompt"], r["seed"]) for r in Ledger(p.ledger_path).rows() if r.get("model") == STILL_MODEL]
    assert len(jobs) == 4 and len(set(jobs)) == 4
