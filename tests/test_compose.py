import pytest

from celery_man.compose import TOKEN_LIMIT, PromptTooLong, compose, count_tokens, prompt_sha
from celery_man.project import Brief, CastMember, Place, Shot


def make(desc="a tall person", **style):
    st = {"medium": "ink", "palette": "grey", "lighting": "soft", "camera": "static"} | style
    brief = Brief("l", st, [CastMember("A", desc)], [Place("P", "a room")], ["blur", "text"])
    shot = Shot("s01", 0, 4, "x", "a person walks", ["A"], "P", "wide shot")
    return shot, brief


def test_cast_description_verbatim():
    assert "A: a tall person" in compose(*make())


def test_style_after_action_and_avoid_last():
    p = compose(*make())
    assert p.index("a person walks") < p.index("ink")
    assert p.endswith("no blur, no text")


def test_unknown_cast_raises():
    shot, brief = make()
    shot.cast = ["Z"]
    with pytest.raises(ValueError):
        compose(shot, brief)
    shot.cast, shot.place = ["A"], "nowhere"
    with pytest.raises(ValueError):
        compose(shot, brief)


def test_too_long_names_longest_field():
    shot, brief = make(desc=" ".join(["word"] * 400))
    with pytest.raises(PromptTooLong) as e:
        compose(shot, brief)
    assert e.value.fields[0][0] == "cast:A"
    assert e.value.fields[0][1] > TOKEN_LIMIT
    assert "cast:A" in str(e.value)


def test_style_change_changes_every_prompt_sha_but_no_shot_text():
    shot, brief = make()
    _, brief2 = make(palette="red")
    assert prompt_sha(shot, brief) != prompt_sha(shot, brief2)
    assert compose(shot, brief).startswith("wide shot. a person walks")
    assert count_tokens("hello") > 0
