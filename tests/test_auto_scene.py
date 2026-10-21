import json

from celery_man import auto_scene, steps
from celery_man.animate import FakeRenderer
from celery_man.pipeline import advance
from celery_man.project import read_json, write_json
from celery_man.templates import FALLBACK_TEMPLATE
from tests.test_auto import stub
from tests.test_pipeline import CountingGen, make, text_chat, vision_chat
from tests.test_renders import project

FILE = {"file": "scenes/s01.js", "params": {}}


def setup_run(tmp_path, song):
    p = project(tmp_path, song, scene=lambda i: FILE if i == 1 else None)
    r = FakeRenderer()
    return steps.Run(p, CountingGen(), renderer=r), p.load_shots()[0], p.load_brief(), r


def test_fixed_code_on_second_try(tmp_path, song):
    run, shot, brief, r = setup_run(tmp_path, song)
    chat = stub(json.dumps({"code": "function setup(){}\nfunction draw(){}"}))
    out = auto_scene.settle(chat, run, shot, brief, "throw new Error('x')")
    assert out.scene == FILE | {"note": ""} and len(r.jobs) == 2
    assert "scene threw" in chat.calls[0][1] and "throw new Error" in chat.calls[0][1] and "draw(ctx, f, env)" in chat.calls[0][0]
    assert (run.p.root / "scenes/s01.js").read_text() == "function setup(){}\nfunction draw(){}"
    assert read_json(run.p.renders_path)["s01"]["error"] == ""


def test_bad_code_three_times_falls_back_to_template(tmp_path, song):
    run, shot, brief, r = setup_run(tmp_path, song)
    chat = stub(json.dumps({"code": "throw 2"}), json.dumps({"code": "throw 3"}), json.dumps({"template": "grid", "params": {"cols": 4}}))
    out = auto_scene.settle(chat, run, shot, brief, "throw 1")
    assert out.scene["template"] == "grid" and out.scene["params"] == {"cols": 4, "pulse": 0.6}
    assert out.scene["note"].startswith("fallback:") and "scene threw" in out.scene["note"]
    assert [j.shot_id for j in r.jobs] == ["s01"] * 3


def test_fallback_when_template_pick_also_fails(tmp_path, song):
    run, shot, brief, _ = setup_run(tmp_path, song)
    chat = stub(*[json.dumps({"code": f"throw {i}"}) for i in (2, 3)], "junk", "more junk", '{"template": "nope"}')
    out = auto_scene.settle(chat, run, shot, brief, "throw 1")
    assert out.scene["template"] == FALLBACK_TEMPLATE and out.scene["params"] == {} and out.scene["note"].startswith("fallback:")


def test_auto_shots_settle_and_render_step_reuses(tmp_path, song):
    p = make(tmp_path, song)
    write_json(p.root / "project.json", p.config | {"range": [0, 16], "kinds": ["footage", "animation"]})

    def chat(system, user, images):
        if "plan the shots" in system:
            rows = json.loads(text_chat(system, user, images))
            rows[0] = {"id": "s01", "kind": "animation", "action": "a custom scene", "code": "function setup(){}\nfunction draw(){}"}
            return json.dumps(rows)
        return text_chat(system, user, images)

    r = FakeRenderer()
    assert {s.state for s in advance(p, CountingGen(), chat, vision_chat(), renderer=r)} == {"done"}
    s01 = p.load_shots()[0]
    assert s01.scene == FILE | {"note": ""} and (p.root / "scenes/s01.js").is_file()
    assert [j.shot_id for j in r.jobs] == ["s01"]


def test_unfixable_reply_falls_back_to_template(tmp_path, song):
    run, shot, brief, r = setup_run(tmp_path, song)
    chat = stub("junk", "junk", "junk", json.dumps({"template": "grid", "params": {}}))
    out = auto_scene.settle(chat, run, shot, brief, "throw 1")
    assert out.scene["template"] == "grid" and out.scene["note"].startswith("fallback:") and "scene threw" in out.scene["note"]
    assert len(r.jobs) == 1
