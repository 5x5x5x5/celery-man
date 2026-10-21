import json
import math
import re
from pathlib import Path

import pytest
from PIL import Image

from celery_man import features, templates
from celery_man.animate import ChromiumRenderer, RenderJob
from celery_man.generate import FPS, H, W
from celery_man.project import DEFAULT_LOOK

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


# --- the real library (these tests undo the demo fixture) ---
REAL = Path(templates.__file__).parent / "scenes" / "templates"
NAMES = ["words", "title", "grid", "rings", "bars", "drift", "burst"]
TYPE = ("words", "title")


@pytest.fixture
def real(monkeypatch):
    monkeypatch.setattr(templates, "TEMPLATES_DIR", REAL)


def test_catalogue_is_complete(real):
    cat = templates.catalogue()
    assert set(NAMES) <= set(cat)
    assert {c["family"] for c in cat.values()} == {"type", "geometric", "particles"}
    for name in cat:
        templates.validate_params(name, {})
        assert (REAL / f"{name}.js").is_file()
    for js in REAL.glob("*.js"):
        assert js.with_suffix(".json").is_file()


def test_templates_are_theme_neutral(real):
    for js in REAL.glob("*.js"):
        assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgb\(", js.read_text()), js.name
    for name, spec in templates.catalogue().items():
        for k, p in spec["params"].items():
            if p["type"] == "string":
                assert p["default"] == "", (name, k)


@pytest.mark.slow
@pytest.mark.parametrize("gap", [0.5, 0.8])
@pytest.mark.parametrize("name", NAMES)
def test_every_template_renders_at_two_tempos(real, tmp_path, name, gap):
    beats = [i * gap for i in range(int(2 / gap) + 2)]
    timing = {"beats": beats, "downbeats": beats[::4]}
    n = 2 * FPS + 2
    feats = {k: [0.2 + 0.6 * abs(math.sin(i / 7 + j)) for i in range(n)] for j, k in enumerate(("level", "low", "mid", "high"))}
    params = templates.validate_params(name, {"text": "one two three"} if name in TYPE else {})
    job = RenderJob(name, templates.source(name), params, DEFAULT_LOOK, features.frame_data(timing, feats, 0, 2, FPS), FPS, W, H, 1)
    out = ChromiumRenderer().render(job, tmp_path / "s.mp4")
    assert out.exists()
    assert any(lo != hi for lo, hi in Image.open(out.with_suffix(".jpg")).convert("RGB").getextrema())
