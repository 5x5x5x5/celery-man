import pytest

from celery_man.project import Project, Shot, brief_from_dict, read_json, sha12, shot_from_dict, write_json

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
