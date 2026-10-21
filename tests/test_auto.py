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
    shots = write_shots(chat, brief(), WINDOWS)
    assert [s.id for s in shots] == ["s01", "s02"]
    assert "unknown cast 'B' (known: A)" in chat.calls[1][1]


def test_write_shots_keeps_windows():
    shots = write_shots(stub(shots_reply(start=99, end=100)), brief(), WINDOWS)
    assert [(s.start, s.end, s.section) for s in shots] == [(0.0, 4.0, "x"), (4.0, 8.0, "y")]


def test_pick_take_must_pick_rejects_null():
    shot = write_shots(stub(shots_reply()), brief(), WINDOWS)[0]
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
    shots = write_shots(chat, b, ws)
    assert all(i in chat.calls[1][1] for i in ("s01", "s02", "s03"))
    assert shots[3].cast == ["A"] and shots[3].place is None


def test_pick_take_empty_sheets():
    shot = write_shots(stub(shots_reply()), brief(), WINDOWS)[0]
    with pytest.raises(ValueError):
        pick_take(stub(), shot, brief(), [], must_pick=False)


def test_write_brief_is_lenient_and_strict_feedback():
    from celery_man.auto import write_brief

    st = {"medium": ["ink", "paper"], "palette": "grey", "lighting": "soft", "camera": "static"}
    sloppy = {"logline": "l", "style": st, "cast": {"name": "A", "description": "d"}, "places": [], "avoid": "blur, text;  ,glare"}
    b = write_brief(stub(json.dumps(sloppy)), "")
    assert b.avoid == ["blur", "text", "glare"] and b.style["medium"] == "ink, paper" and [c.name for c in b.cast] == ["A"]
    chat = stub(json.dumps(sloppy | {"avoid": 5}), json.dumps(sloppy))
    write_brief(chat, "")
    assert "avoid (a list of strings): expected list, got int" in chat.calls[1][1]
