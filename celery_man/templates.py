"""Scene template catalogue: each name has a .json spec (family, description, params) and a .js drawing function."""

import json
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent / "scenes" / "templates"
FALLBACK_TEMPLATE = "rings"


def catalogue() -> dict[str, dict]:
    return {f.stem: json.loads(f.read_text()) for f in sorted(TEMPLATES_DIR.glob("*.json"))}


def source(name: str) -> str:
    return (TEMPLATES_DIR / f"{name}.js").read_text()


def _check(name: str, spec: dict, v):
    t = spec["type"]
    ok = {
        "number": isinstance(v, (int, float)) and not isinstance(v, bool),
        "integer": isinstance(v, int) and not isinstance(v, bool),
        "string": isinstance(v, str),
        "choice": isinstance(v, str),
        "bool": isinstance(v, bool),
    }[t]
    if not ok:
        raise ValueError(f"param {name}: expected {t}, got {type(v).__name__}")
    if t in ("number", "integer") and not (spec.get("min", v) <= v <= spec.get("max", v)):
        raise ValueError(f"param {name}: {v} is outside {spec.get('min')}..{spec.get('max')}")
    if t == "choice" and v not in spec["choices"]:
        raise ValueError(f"param {name}: {v!r} is not one of {spec['choices']}")
    if t == "string" and len(v) > spec.get("max_len", len(v)):
        raise ValueError(f"param {name}: longer than {spec['max_len']} characters")


def validate_params(name: str, params: dict) -> dict:
    """The params with defaults filled in; ValueError naming the first bad one."""
    cat = catalogue()
    if name not in cat:
        raise ValueError(f"unknown template {name!r}; known: {sorted(cat)}")
    specs = cat[name]["params"]
    for k in params:
        if k not in specs:
            raise ValueError(f"param {k}: not a parameter of {name} (has {sorted(specs)})")
    out = {}
    for k, spec in specs.items():
        out[k] = params.get(k, spec["default"])
        _check(k, spec, out[k])
    return out
