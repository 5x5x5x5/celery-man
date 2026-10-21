import json

import pytest

from celery_man import templates

DEMO = {
    "family": "f",
    "description": "d",
    "params": {
        "size": {"type": "number", "default": 0.5, "min": 0, "max": 1, "doc": "s"},
        "text": {"type": "string", "default": "", "max_len": 10, "doc": "t"},
        "shape": {"type": "choice", "default": "a", "choices": ["a", "b"], "doc": "c"},
    },
}


@pytest.fixture(autouse=True)
def demo(tmp_path, monkeypatch):
    (tmp_path / "demo.json").write_text(json.dumps(DEMO))
    monkeypatch.setattr(templates, "TEMPLATES_DIR", tmp_path)


def test_defaults_filled():
    assert templates.validate_params("demo", {"size": 1}) == {"size": 1, "text": "", "shape": "a"}
    assert "demo" in templates.catalogue()


def test_out_of_range_rejected_naming_param():
    with pytest.raises(ValueError, match="size"):
        templates.validate_params("demo", {"size": 2})


def test_unknown_param_rejected():
    with pytest.raises(ValueError, match="nope"):
        templates.validate_params("demo", {"nope": 1})


def test_wrong_type_rejected():
    with pytest.raises(ValueError, match="size"):
        templates.validate_params("demo", {"size": True})
    with pytest.raises(ValueError, match="shape"):
        templates.validate_params("demo", {"shape": "z"})


def test_unknown_template_rejected():
    with pytest.raises(ValueError, match="other"):
        templates.validate_params("other", {})


def test_string_too_long_rejected():
    with pytest.raises(ValueError, match="text"):
        templates.validate_params("demo", {"text": "x" * 11})


def test_missing_dir_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(templates, "TEMPLATES_DIR", tmp_path / "none")
    assert templates.catalogue() == {}
