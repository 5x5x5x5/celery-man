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
    assert "Expecting" in chat.calls[1][1] or "JSON" in chat.calls[1][1]


def test_ask_json_gives_up_after_three():
    chat = stub("x", "y", "z")
    with pytest.raises(AutoError):
        ask_json(chat, "s", "u", [], lambda d: d)
    assert len(chat.calls) == 3


def test_write_shots_rejects_unknown_cast_then_accepts():
    chat = stub(shots_reply("B"), shots_reply("A"))
    shots = write_shots(chat, brief(), WINDOWS)
    assert [s.id for s in shots] == ["s01", "s02"]
    assert "unknown cast 'B'; known: A" in chat.calls[1][1]


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
