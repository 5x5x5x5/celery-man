import threading
from dataclasses import asdict

import pytest

from celery_man.project import Project, Shot, brief_from_dict, open_shot, read_json, sha12, shot_from_dict, update_json, write_json

BRIEF = {
    "logline": "a person walks",
    "style": {"medium": "m", "palette": "p", "lighting": "l", "camera": "c"},
    "cast": [{"name": "A", "description": "d"}],
    "places": [{"name": "P", "description": "d"}],
    "avoid": ["x"],
}
SHOT = {"id": "s01", "start": 0.0, "end": 2.0, "section": "a", "action": "a person walks", "cast": ["A"], "place": None, "framing": "wide"}


def test_write_json_atomic_roundtrip(tmp_path):
    p = tmp_path / "x.json"
    write_json(p, {"a": [1, 2]})
    assert read_json(p) == {"a": [1, 2]}
    assert not list(tmp_path.glob("*.tmp"))
    assert read_json(tmp_path / "missing.json") is None


def test_sha12_key_order_independent():
    assert sha12({"a": 1, "b": 2}) == sha12({"b": 2, "a": 1})
    assert len(sha12([1])) == 12


def test_create_copies_song_and_defaults(tmp_path):
    song = tmp_path / "s.wav"
    song.write_bytes(b"x")
    p = Project.create(tmp_path / "proj", song, "src")
    assert p.song.exists() and p.song.parent == p.root
    assert p.modes == {"brief": "tastemaker", "shots": "tastemaker", "keyframes": "tastemaker", "takes": "tastemaker"}
    assert p.cap_usd == 25
    assert p.range == [None, None]
    assert set(p.models) == {"text", "vision"}
    assert p.models["text"].base_url == "http://127.0.0.1:8080/v1"


def test_brief_from_dict_rejects_extra_and_missing():
    assert brief_from_dict(BRIEF).style["medium"] == "m"
    with pytest.raises(ValueError):
        brief_from_dict({**BRIEF, "extra": 1})
    with pytest.raises(ValueError):
        brief_from_dict({k: v for k, v in BRIEF.items() if k != "avoid"})
    with pytest.raises(ValueError):
        brief_from_dict({**BRIEF, "style": {"medium": "m"}})
    with pytest.raises(ValueError):
        brief_from_dict({**BRIEF, "cast": [{"name": "A"}]})


def test_shot_from_dict_rejects_bad_types():
    assert shot_from_dict(SHOT).id == "s01"
    with pytest.raises(ValueError):
        shot_from_dict({**SHOT, "start": "x"})
    with pytest.raises(ValueError):
        shot_from_dict({**SHOT, "extra": 1})
    with pytest.raises(ValueError):
        shot_from_dict({**SHOT, "place": 3})


def test_save_load_shots_roundtrip(tmp_path):
    p = Project.create(tmp_path / "proj", _song(tmp_path), "")
    assert p.load_shots() is None and p.load_brief() is None
    shots = [shot_from_dict(SHOT)]
    p.save_shots(shots, "abc")
    assert p.load_shots() == shots
    assert read_json(p.shots_path)["inputs"] == "abc"
    p.save_brief(brief_from_dict(BRIEF), "def")
    assert p.load_brief() == brief_from_dict(BRIEF)


def _song(tmp_path):
    s = tmp_path / "s.wav"
    s.write_bytes(b"x")
    return s


def test_shot_is_dataclass():
    assert Shot(**SHOT).cast == ["A"]


def test_brief_from_dict_stays_strict_on_string_avoid():
    with pytest.raises(ValueError, match="expected list, got str"):
        brief_from_dict({**BRIEF, "avoid": "blur, text"})


def test_shot_from_dict_rejects_nan_and_inf():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="finite"):
            shot_from_dict({**SHOT, "end": bad})


def test_missing_project_and_shotless_file_raise_clearly(tmp_path):
    from celery_man.project import NoProject

    with pytest.raises(NoProject, match=str(tmp_path / "nope")):
        Project(tmp_path / "nope")
    p = Project.create(tmp_path / "p", _song(tmp_path), "t")
    write_json(p.shots_path, {"inputs": "x"})
    with pytest.raises(ValueError, match="shots"):
        p.load_shots()


def test_old_files_load_and_stay_fresh(tmp_path, song):
    from celery_man import steps
    from celery_man.project import DEFAULT_LOOK
    from tests.test_pipeline import make

    p = make(tmp_path, song)
    write_json(p.shots_path, {"inputs": "x", "shots": [SHOT]})
    s = p.load_shots()[0]
    assert s.kind == "footage" and s.scene is None
    write_json(p.brief_path, {"inputs": "x", **BRIEF})
    brief = p.load_brief()
    assert brief.look == DEFAULT_LOOK
    write_json(p.root / "project.json", {k: v for k, v in p.config.items() if k != "kinds"})
    assert p.kinds == ["footage"]
    assert steps.shots_inputs(p, brief) == sha12([steps.windows(p), ["A"], ["P"]])


def test_animation_shot_round_trips(tmp_path, monkeypatch):
    from celery_man import templates

    (tmp_path / "t.json").write_text('{"family": "f", "description": "d", "params": {}}')
    monkeypatch.setattr(templates, "TEMPLATES_DIR", tmp_path)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "project.json").write_text("{}")
    p = Project(root)
    t = {"template": "t", "params": {}}
    f = {"file": "scenes/s01.js", "params": {"x": 1}, "note": "n"}
    p.save_shots(
        [shot_from_dict({**SHOT, "kind": "animation", "scene": t}), shot_from_dict({**SHOT, "id": "s02", "kind": "animation", "scene": f})],
        "i",
    )
    assert [s.scene for s in p.load_shots()] == [t, f]
    assert [s.id for s in p.load_animation()] == ["s01", "s02"] and p.load_footage() == []


def test_bad_scenes_rejected():
    ok = {"template": "t", "params": {}}
    anim = {**SHOT, "kind": "animation"}
    for bad in (
        {**SHOT, "scene": ok},
        anim,
        {**anim, "scene": {**ok, "file": "scenes/a.js"}},
        {**anim, "scene": {"params": {}}},
        {**anim, "scene": {"file": "../x.js", "params": {}}},
        {**anim, "scene": {"file": "scenes/a/b.js", "params": {}}},
        {**SHOT, "kind": "cartoon"},
    ):
        with pytest.raises(ValueError):
            shot_from_dict(bad)


def test_look_validation():
    from celery_man.project import DEFAULT_LOOK, look_from_dict

    assert look_from_dict(DEFAULT_LOOK) == DEFAULT_LOOK
    for bad in (
        {**DEFAULT_LOOK, "background": "red"},
        {**DEFAULT_LOOK, "colors": ["#12345"]},
        {**DEFAULT_LOOK, "colors": []},
        {**DEFAULT_LOOK, "colors": ["#000000"] * 6},
        {**DEFAULT_LOOK, "typeface": "comic"},
        {**DEFAULT_LOOK, "extra": 1},
    ):
        with pytest.raises(ValueError):
            look_from_dict(bad)


def test_kinds_enter_shots_stamp_only_when_not_default(tmp_path, song):
    from celery_man import steps
    from tests.test_pipeline import make

    p = make(tmp_path, song)
    brief = brief_from_dict(BRIEF)
    old = sha12([steps.windows(p), ["A"], ["P"]])
    write_json(p.root / "project.json", p.config | {"kinds": ["footage"]})
    assert steps.shots_inputs(p, brief) == old
    write_json(p.root / "project.json", p.config | {"kinds": ["footage", "animation"]})
    assert steps.shots_inputs(p, brief) != old


def test_open_shot_round_trips():
    s = open_shot("s01", 0.0, 2.5, "intro")
    assert s.is_open and shot_from_dict(asdict(s)) == s


def test_open_shot_rejects_content():
    with pytest.raises(ValueError):
        shot_from_dict(asdict(open_shot("s01", 0, 1, "x")) | {"action": "a person walks"})


def test_mode_prefers_shot_override(tmp_path, song):
    p = Project.create(tmp_path / "p", song, "src")
    write_json(p.root / "project.json", p.config | {"shot_modes": {"s02": {"takes": "auto"}}})
    assert p.mode("takes") == "tastemaker" and p.mode("takes", "s02") == "auto" and p.mode("takes", "s03") == "tastemaker"


def test_update_json_serialises_writers(tmp_path, song):
    p = Project.create(tmp_path / "p", song, "src")
    path = p.root / "k.json"
    threads = [threading.Thread(target=update_json, args=(p, path, lambda d, i=i: (d or {}) | {str(i): i})) for i in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(read_json(path)) == 20
