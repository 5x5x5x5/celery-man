"""Project folder model: file contracts, atomic JSON, validated decision records."""

import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

STYLE_KEYS = ("medium", "palette", "lighting", "camera")
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


def brief_from_dict(d) -> Brief:
    _exact(d, ("logline", "style", "cast", "places", "avoid"), "brief")
    _is(d["logline"], str, "logline")
    _exact(d["style"], STYLE_KEYS, "style")
    for k in STYLE_KEYS:
        _is(d["style"][k], str, f"style.{k}")
    _is(d["avoid"], list, "avoid (a list of strings)")
    for a in d["avoid"]:
        _is(a, str, "avoid")
    return Brief(
        d["logline"], dict(d["style"]), _named(d["cast"], CastMember, "cast"), _named(d["places"], Place, "places"), list(d["avoid"])
    )


def shot_from_dict(d) -> Shot:
    _exact(d, ("id", "start", "end", "section", "action", "cast", "place", "framing"), "shot")
    for k in ("id", "section", "action", "framing"):
        _is(d[k], str, k)
    for k in ("start", "end"):
        _is(d[k], (int, float), k)
    _is(d["cast"], list, "cast")
    for c in d["cast"]:
        _is(c, str, "cast")
    if d["place"] is not None:
        _is(d["place"], str, "place")
    return Shot(d["id"], float(d["start"]), float(d["end"]), d["section"], d["action"], list(d["cast"]), d["place"], d["framing"])


@dataclass
class Project:
    root: Path
    _cfg: dict = field(default=None, init=False, repr=False)

    def __post_init__(self):
        self.root = Path(self.root)

    @classmethod
    def create(cls, root: Path, song: Path, song_source: str) -> "Project":
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

    def load_brief(self) -> Brief | None:
        d = read_json(self.brief_path)
        return None if d is None else brief_from_dict({k: v for k, v in d.items() if k != "inputs"})

    def save_brief(self, b: Brief, inputs: str) -> None:
        write_json(self.brief_path, {"inputs": inputs, **asdict(b)})

    def load_shots(self) -> list[Shot] | None:
        d = read_json(self.shots_path)
        return None if d is None else [shot_from_dict(s) for s in d["shots"]]

    def save_shots(self, shots: list[Shot], inputs: str) -> None:
        write_json(self.shots_path, {"inputs": inputs, "shots": [asdict(s) for s in shots]})
