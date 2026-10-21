import json
from pathlib import Path

import pytest

from celery_man import llm
from celery_man.auto import AutoError, ask_json, pick_take, write_shots
from celery_man.project import Brief, CastMember, ModelCfg, Place


def stub(*replies):
    calls = []
    it = iter(replies)

    def chat(system, user, images):
        calls.append((system, user, images))
        return next(it)

    chat.calls = calls
    return chat


def brief():
    st = {"medium": "ink", "palette": "grey", "lighting": "soft", "camera": "static"}
    return Brief("l", st, [CastMember("A", "a tall person")], [Place("P", "a room")], [])


WINDOWS = [(0.0, 4.0, "x"), (4.0, 8.0, "y")]


def shots_reply(cast="A", **extra):
    return json.dumps(
        [{"id": i, "action": "a person walks", "cast": [cast], "place": "P", "framing": "wide shot"} | extra for i in ("s01", "s02")]
    )


def test_ask_json_retries_then_succeeds():
    chat = stub("not json at all", 'Sure! ```json\n{"a": 1}\n```')
    assert ask_json(chat, "s", "u", [], lambda d: d["a"]) == 1
    assert "rejected" in chat.calls[1][1] and "not json at all" in chat.calls[1][1]


def test_ask_json_gives_up_after_three():
    chat = stub("x", "y", "z")
    with pytest.raises(AutoError):
        ask_json(chat, "s", "u", [], lambda d: d)
    assert len(chat.calls) == 3


def test_write_shots_rejects_unknown_cast_then_accepts():
    chat = stub(shots_reply("B"), shots_reply("A"))
    shots = write_shots(chat, brief(), WINDOWS, ["footage"])[0]
    assert [s.id for s in shots] == ["s01", "s02"]
    assert "unknown cast 'B' (known: A)" in chat.calls[1][1]


def test_write_shots_keeps_windows():
    shots = write_shots(stub(shots_reply(start=99, end=100)), brief(), WINDOWS, ["footage"])[0]
    assert [(s.start, s.end, s.section) for s in shots] == [(0.0, 4.0, "x"), (4.0, 8.0, "y")]


def test_pick_take_must_pick_rejects_null():
    shot = write_shots(stub(shots_reply()), brief(), WINDOWS, ["footage"])[0][0]
    chat = stub('{"pick": null, "why": "none"}', '{"pick": 1, "why": "best"}')
    assert pick_take(chat, shot, brief(), [Path("a.png"), Path("b.png")], must_pick=True) == (1, "best")
    assert len(chat.calls) == 2


def test_chat_sends_images_as_data_urls(monkeypatch, tmp_path):
    seen = {}

    class Fake:
        def __init__(self, **kw):
            seen["init"] = kw
            self.chat = self
            self.completions = self

        def create(self, **kw):
            seen["req"] = kw
            msg = type("M", (), {"content": "ok"})()
            return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()

    monkeypatch.setattr("openai.OpenAI", Fake)
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG")
    assert llm.chat(ModelCfg("http://h/v1", "m", ""), "sys", "usr", [img]) == "ok"
    assert seen["init"]["api_key"] == "none"
    content = seen["req"]["messages"][1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert seen["req"]["temperature"] == 0.7


def test_ask_json_finds_list_inside_prose():
    chat = stub("Here you go: [1, 2] hope it helps {not json}")
    assert ask_json(chat, "s", "u", [], lambda d: d) == [1, 2]


def test_ask_json_missing_key_feedback():
    chat = stub('{"b": 1}', '{"a": 1}')
    ask_json(chat, "s", "u", [], lambda d: d["a"])
    assert "missing key 'a'" in chat.calls[1][1]


def test_ask_json_server_error_not_retried():
    import openai

    def dead(system, user, images):
        dead.n += 1
        raise openai.APIConnectionError(request=None)

    dead.n = 0
    with pytest.raises(AutoError):
        ask_json(dead, "s", "u", [], lambda d: d)
    assert dead.n == 1


def test_write_shots_reports_all_bad_shots_and_is_lenient():
    st = {"medium": "ink", "palette": "grey", "lighting": "soft", "camera": "static"}
    b = Brief("l", st, [CastMember("A", "a tall person")], [Place("P", "a room")], [])
    ws = [(0.0, 4.0, "x"), (4.0, 8.0, "x"), (8.0, 12.0, "x"), (12.0, 16.0, "y")]
    row = {"action": "a person walks", "framing": "wide shot"}
    bad = [
        {"id": "s01", "cast": ["B"], "place": "P"},
        {"id": "s02", "cast": ["A"], "place": "Q"},
        {"id": "s03", "cast": ["C"], "place": None},
        {"id": "s04", "cast": "A", "place": "None"},
    ]
    good = [{"id": f"s0{i}", "cast": ["A"], "place": "P"} for i in (1, 2, 3)] + [bad[3]]
    chat = stub(json.dumps({"shots": [r | row for r in bad]}), json.dumps([r | row for r in good]))
    shots, _ = write_shots(chat, b, ws, ["footage"])
    assert all(i in chat.calls[1][1] for i in ("s01", "s02", "s03"))
    assert shots[3].cast == ["A"] and shots[3].place is None


def test_pick_take_empty_sheets():
    shot = write_shots(stub(shots_reply()), brief(), WINDOWS, ["footage"])[0][0]
    with pytest.raises(ValueError):
        pick_take(stub(), shot, brief(), [], must_pick=False)


def test_write_brief_is_lenient_and_strict_feedback():
    from celery_man.auto import write_brief

    st = {"medium": ["ink", "paper"], "palette": "grey", "lighting": "soft", "camera": "static"}
    sloppy = {
        "logline": "l",
        "style": st,
        "cast": {"name": "A", "description": "d"},
        "places": {"name": "P", "description": "a room"},
        "avoid": "blur, text;  ,glare",
    }
    b = write_brief(stub(json.dumps(sloppy)), "")
    assert (
        b.avoid == ["blur", "text", "glare"]
        and b.style["medium"] == "ink, paper"
        and [c.name for c in b.cast] == ["A"]
        and [p.name for p in b.places] == ["P"]
    )
    chat = stub(json.dumps(sloppy | {"avoid": 5}), json.dumps(sloppy))
    write_brief(chat, "")
    assert "avoid (a list of strings): expected list, got int" in chat.calls[1][1]


def test_rewrite_shot_rejects_empty_action_or_framing():
    from celery_man.auto import rewrite_shot
    from celery_man.project import Shot

    shot = Shot("s01", 0.0, 4.0, "x", "a person walks", ["A"], "P", "wide shot")
    empty = json.dumps({"action": "", "cast": ["A"], "place": "P", "framing": "wide shot"})
    good = json.dumps({"action": "a person stands", "cast": ["A"], "place": "P", "framing": "wide shot"})
    chat = stub(empty, json.dumps({"action": "a person stands", "cast": ["A"], "place": "P", "framing": ""}), good)
    assert rewrite_shot(chat, shot, brief(), "morphing").action == "a person stands"
    assert "non-empty" in chat.calls[1][1] and "non-empty" in chat.calls[2][1]


TODAY_SHOTS_SYSTEM = (
    "You plan the shots of a music video, one per window. Craft rules: every shot is one concrete, filmable action under a locked "
    "or slowly moving camera. No on-screen text, captions, signs or signage. Reply with JSON only, no prose. Reply with a JSON list "
    "with one object per id: {id, action, cast ([names]), place (name or null), framing}. Do not change or add windows."
)
MIXED = ["footage", "animation"]
WINDOWS3 = [(0.0, 4.0, "x"), (4.0, 8.0, "y"), (8.0, 12.0, "y")]


def mixed_reply(rings_params=None):
    return json.dumps(
        [
            {"id": "s01", "kind": "animation", "action": "rings pulse", "template": "rings", "params": rings_params or {"count": 4}},
            {"id": "s02", "kind": "animation", "action": "a custom scene", "code": "function setup(env) {}\nfunction draw(ctx, f, env) {}"},
            {"id": "s03", "kind": "footage", "action": "a person walks", "cast": ["A"], "place": "P", "framing": "wide shot"},
        ]
    )


def test_footage_only_prompt_unchanged():
    chat = stub(shots_reply())
    shots, codes = write_shots(chat, brief(), WINDOWS, ["footage"])
    assert chat.calls[0][0] == TODAY_SHOTS_SYSTEM and codes == {}
    assert [(s.kind, s.scene, s.action) for s in shots] == [("footage", None, "a person walks")] * 2


def test_mixed_kinds_parsed():
    chat = stub(mixed_reply())
    shots, codes = write_shots(chat, brief(), WINDOWS3, MIXED)
    assert [s.kind for s in shots] == ["animation", "animation", "footage"]
    assert shots[0].scene == {"template": "rings", "params": {"count": 4, "thickness": 0.012}}
    assert shots[1].scene == {"file": "scenes/s02.js", "params": {}}
    assert codes == {"s02": "function setup(env) {}\nfunction draw(ctx, f, env) {}"}
    assert shots[2].cast == ["A"] and shots[2].place == "P"
    system = chat.calls[0][0]
    assert "plan the shots" in system and "rings:" in system and "draw(ctx, f, env)" in system and "footage shots only" in system


def test_kind_not_allowed_fed_back():
    bad = json.loads(shots_reply())
    bad[1] = {"id": "s02", "kind": "animation", "action": "rings", "template": "rings", "params": {}}
    chat = stub(json.dumps(bad), shots_reply())
    write_shots(chat, brief(), WINDOWS, ["footage"])
    assert "s02" in chat.calls[1][1] and "animation" in chat.calls[1][1] and "s01" not in chat.calls[1][1].split("rejected")[-1]


def test_lone_kind_label_is_ignored_and_prompt_has_example():
    bad = [{"id": i, "kind": "action", "action": "dots", "template": "rings", "params": {}} for i in ("s01", "s02")]
    chat = stub(json.dumps(bad))
    shots, _ = write_shots(chat, brief(), WINDOWS, ["animation"])
    assert [s.kind for s in shots] == ["animation"] * 2 and len(chat.calls) == 1
    assert '"template": "rings"' in chat.calls[0][0]


def test_out_of_range_param_is_fed_back():
    chat = stub(mixed_reply({"count": 99999}), mixed_reply())
    shots, _ = write_shots(chat, brief(), WINDOWS3, MIXED)
    assert "s01" in chat.calls[1][1] and "count" in chat.calls[1][1].split("rejected")[-1]
    assert shots[0].scene["params"]["count"] == 4


def test_brief_without_look_gets_default():
    from celery_man.auto import write_brief
    from celery_man.project import DEFAULT_LOOK

    st = {"medium": "ink", "palette": "grey", "lighting": "soft", "camera": "static"}
    d = {"logline": "l", "style": st, "cast": [{"name": "A", "description": "d"}], "places": [], "avoid": []}
    assert write_brief(stub(json.dumps(d)), "").look == DEFAULT_LOOK
    assert write_brief(stub(json.dumps(d | {"look": "warm"})), "").look == DEFAULT_LOOK
    chat = stub(json.dumps(d | {"look": DEFAULT_LOOK | {"typeface": "comic"}}), json.dumps(d))
    write_brief(chat, "")
    assert "look.typeface" in chat.calls[1][1] and "typeface" in chat.calls[0][0]
