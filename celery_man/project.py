"""Project folder model: file contracts, atomic JSON, validated decision records."""

import hashlib
import json
import math
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

STYLE_KEYS = ("medium", "palette", "lighting", "camera")
KINDS = ("footage", "animation")
TYPEFACES = ("serif", "sans", "mono", "display")
DEFAULT_LOOK = {"background": "#f4efe6", "colors": ["#1d1b18"], "typeface": "serif"}
SCENE_FILE = re.compile(r"^scenes/[A-Za-z0-9_-]+\.js$")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_MODES = {"brief": "tastemaker", "shots": "tastemaker", "keyframes": "tastemaker", "takes": "tastemaker"}


def sha12(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]


def write_json(path: Path, obj) -> None:
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, path)


def read_json(path: Path):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else None


@dataclass
class CastMember:
    name: str
    description: str


@dataclass
class Place:
    name: str
    description: str


@dataclass
class Brief:
    logline: str
    style: dict
    cast: list[CastMember]
    places: list[Place]
    avoid: list[str]
    look: dict = field(default_factory=lambda: dict(DEFAULT_LOOK))


@dataclass
class Shot:
    id: str
    start: float
    end: float
    section: str
    action: str
    cast: list[str]
    place: str | None
    framing: str
    kind: str = "footage"
    scene: dict | None = None


@dataclass
class ModelCfg:
    base_url: str
    model: str
    api_key_env: str


def _exact(d, keys: tuple, what: str) -> None:
    if not isinstance(d, dict) or set(d) != set(keys):
        raise ValueError(f"{what}: expected keys {sorted(keys)}, got {sorted(d) if isinstance(d, dict) else type(d).__name__}")


def _is(v, t, what: str) -> None:
    ok = isinstance(v, t) and not isinstance(v, bool) if t in (int, float, (int, float)) else isinstance(v, t)
    if not ok:
        want = "/".join(x.__name__ for x in (t if isinstance(t, tuple) else (t,)))
        raise ValueError(f"{what}: expected {want}, got {type(v).__name__}")


def _named(items, cls, what: str) -> list:
    _is(items, list, f"{what} (a list of objects with name and description)")
    out = []
    for it in items:
        _exact(it, ("name", "description"), what)
        _is(it["name"], str, what)
        _is(it["description"], str, what)
        out.append(cls(**it))
    return out


def look_from_dict(d) -> dict:
    _exact(d, tuple(DEFAULT_LOOK), "look")
    if not isinstance(d["background"], str) or not HEX.match(d["background"]):
        raise ValueError(f"look.background: expected #rrggbb, got {d['background']!r}")
    _is(d["colors"], list, "look.colors")
    if not 1 <= len(d["colors"]) <= 5:
        raise ValueError(f"look.colors: expected 1 to 5 colours, got {len(d['colors'])}")
    for c in d["colors"]:
        if not isinstance(c, str) or not HEX.match(c):
            raise ValueError(f"look.colors: expected #rrggbb, got {c!r}")
    if d["typeface"] not in TYPEFACES:
        raise ValueError(f"look.typeface: expected one of {TYPEFACES}, got {d['typeface']!r}")
    return {"background": d["background"], "colors": list(d["colors"]), "typeface": d["typeface"]}


def brief_from_dict(d) -> Brief:
    if isinstance(d, dict) and "look" not in d:
        d = {**d, "look": dict(DEFAULT_LOOK)}
    _exact(d, ("logline", "style", "cast", "places", "avoid", "look"), "brief")
    _is(d["logline"], str, "logline")
    _exact(d["style"], STYLE_KEYS, "style")
    for k in STYLE_KEYS:
        _is(d["style"][k], str, f"style.{k}")
    _is(d["avoid"], list, "avoid (a list of strings)")
    for a in d["avoid"]:
        _is(a, str, "avoid")
    return Brief(
        d["logline"],
        dict(d["style"]),
        _named(d["cast"], CastMember, "cast"),
        _named(d["places"], Place, "places"),
        list(d["avoid"]),
        look_from_dict(d["look"]),
    )


def _scene(d) -> dict:
    from celery_man import templates

    if not isinstance(d, dict) or not {"params"} <= set(d) <= {"template", "file", "params", "note"} or ("template" in d) == ("file" in d):
        raise ValueError('scene: expected an object with "params" and exactly one of "template" or "file" (optional "note")')
    _is(d["params"], dict, "scene.params")
    if "note" in d:
        _is(d["note"], str, "scene.note")
    if "file" in d:
        if not isinstance(d["file"], str) or not SCENE_FILE.match(d["file"]):
            raise ValueError(f"scene.file: expected scenes/<name>.js, got {d['file']!r}")
        return dict(d)
    _is(d["template"], str, "scene.template")
    return {**d, "params": templates.validate_params(d["template"], d["params"])}


def shot_from_dict(d) -> Shot:
    if isinstance(d, dict):
        d = {"kind": "footage", "scene": None} | d
    _exact(d, ("id", "start", "end", "section", "action", "cast", "place", "framing", "kind", "scene"), "shot")
    if d["kind"] not in KINDS:
        raise ValueError(f"kind: expected one of {KINDS}, got {d['kind']!r}")
    if (d["kind"] == "animation") != (d["scene"] is not None):
        raise ValueError("scene: required for animation shots and not allowed on footage shots")
    scene = None if d["scene"] is None else _scene(d["scene"])
    for k in ("id", "section", "action", "framing"):
        _is(d[k], str, k)
    for k in ("start", "end"):
        _is(d[k], (int, float), k)
        if not math.isfinite(d[k]):
            raise ValueError(f"{k}: expected a finite number, got {d[k]}")
    _is(d["cast"], list, "cast")
    for c in d["cast"]:
        _is(c, str, "cast")
    if d["place"] is not None:
        _is(d["place"], str, "place")
    return Shot(
        d["id"], float(d["start"]), float(d["end"]), d["section"], d["action"], list(d["cast"]), d["place"], d["framing"], d["kind"], scene
    )


class NoProject(FileNotFoundError):
    """The folder holds no project.json."""


@dataclass
class Project:
    root: Path
    _cfg: dict = field(default=None, init=False, repr=False)

    def __post_init__(self):
        self.root = Path(self.root)
        if not (self.root / "project.json").is_file():
            raise NoProject(f"no project in {self.root} (no project.json); create one with `celery-man new`")

    @classmethod
    def create(cls, root: Path, song: Path, song_source: str, kinds: tuple[str, ...] = ("footage",)) -> "Project":
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(song, root / Path(song).name)
        local = {"base_url": "http://127.0.0.1:8080/v1", "model": "local", "api_key_env": ""}
        write_json(
            root / "project.json",
            {
                "song": Path(song).name,
                "song_source": song_source,
                "range": [None, None],
                "cap_usd": 25,
                "modes": dict(DEFAULT_MODES),
                "seed": "",
                "kinds": list(kinds),
                "models": {"text": dict(local), "vision": dict(local)},
            },
        )
        return cls(root)

    @property
    def config(self) -> dict:
        return read_json(self.root / "project.json")

    @property
    def song(self) -> Path:
        return self.root / self.config["song"]

    @property
    def modes(self) -> dict:
        return self.config["modes"]

    @property
    def cap_usd(self) -> float:
        return self.config["cap_usd"]

    @property
    def range(self) -> list:
        return self.config["range"]

    @property
    def kinds(self) -> list[str]:
        return self.config.get("kinds", ["footage"])

    @property
    def models(self) -> dict:
        return {k: ModelCfg(**v) for k, v in self.config["models"].items()}

    timing_path = property(lambda self: self.root / "timing.json")
    brief_path = property(lambda self: self.root / "brief.json")
    shots_path = property(lambda self: self.root / "shots.json")
    keyframes_path = property(lambda self: self.root / "keyframes.json")
    picks_path = property(lambda self: self.root / "picks.json")
    ledger_path = property(lambda self: self.root / "ledger.jsonl")
    keys_dir = property(lambda self: self.root / "keys")
    takes_dir = property(lambda self: self.root / "takes")
    sheets_dir = property(lambda self: self.root / "sheets")
    out_dir = property(lambda self: self.root / "out")
    renders_path = property(lambda self: self.root / "renders.json")
    renders_dir = property(lambda self: self.root / "renders")
    scenes_dir = property(lambda self: self.root / "scenes")
    features_path = property(lambda self: self.root / "features.json")

    def load_brief(self) -> Brief | None:
        d = read_json(self.brief_path)
        return None if d is None else brief_from_dict({k: v for k, v in d.items() if k != "inputs"})

    def save_brief(self, b: Brief, inputs: str) -> None:
        write_json(self.brief_path, {"inputs": inputs, **asdict(b)})

    def load_shots(self) -> list[Shot] | None:
        d = read_json(self.shots_path)
        if d is None:
            return None
        if not isinstance(d, dict) or not isinstance(d.get("shots"), list):
            raise ValueError(f'{self.shots_path}: expected an object with a "shots" list')  # noqa: TRY004 - a bad file, like the others
        return [shot_from_dict(s) for s in d["shots"]]

    def save_shots(self, shots: list[Shot], inputs: str) -> None:
        write_json(self.shots_path, {"inputs": inputs, "shots": [asdict(s) for s in shots]})

    def load_footage(self) -> list[Shot] | None:
        shots = self.load_shots()
        return None if shots is None else [s for s in shots if s.kind == "footage"]

    def load_animation(self) -> list[Shot] | None:
        shots = self.load_shots()
        return None if shots is None else [s for s in shots if s.kind == "animation"]
