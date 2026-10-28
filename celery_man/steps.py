"""Per-step pipeline work: decision stamps, keyframe needs, and the timing/brief/shots/keyframes/assemble runners (takes: takes.py)."""

import hashlib
import logging
import os
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from celery_man import auto, features
from celery_man.animate import SCENES, ChromiumRenderer, Renderer
from celery_man.assemble import assemble
from celery_man.compose import compose, prompt_sha
from celery_man.generate import Generator, StillJob
from celery_man.ledger import Ledger, cost, job_key
from celery_man.project import OPEN, Brief, Project, Shot, open_shot, read_json, sha12, update_json, write_json
from celery_man.timing import detect, plan_windows

log = logging.getLogger(__name__)

EST_WALL_S = 260  # per L4 draft take, and once more per run for the container load
EST_STILL_S = 20  # per FLUX still on the L40S
STILL_MODEL, TAKE_MODEL, OVERHEAD_MODEL, RESERVE_MODEL = "flux-schnell", "ltx-2.5-draft", "overhead", "reserve"
STILL_GPU, TAKE_GPU = "L40S", "L4"
SEEDS = (1, 2, 3)
IN_POINT = 0.5
MAX_ROUND = 2
QUICK_MODEL, QUICK_SEED = "quick", 1


class BudgetError(RuntimeError):
    """Raised instead of spending past the cap: by the preflight, or when even the quick fallback no longer fits."""


# --- decision stamps -------------------------------------------------------------------------------


def song_sha(p: Project) -> str:
    return hashlib.sha256(p.song.read_bytes()).hexdigest()[:12]


def brief_inputs(p: Project) -> str:
    return sha12(p.config["seed"])


def windows(p: Project) -> list[tuple[float, float, str]]:
    return plan_windows(read_json(p.timing_path), *p.range)


def shots_inputs(p: Project, brief: Brief) -> str:
    """R2: only names reach shot text, so a style or description edit leaves shots fresh."""
    parts = [windows(p), sorted(c.name for c in brief.cast), sorted(x.name for x in brief.places)]
    return sha12(parts if p.kinds == ["footage"] else [*parts, sorted(p.kinds)])


def shots_fresh(p: Project, brief: Brief, d: dict) -> bool:
    """A shots.json written before the brief existed (inputs "") is fresh while every shot in it is still open."""
    return d["inputs"] == shots_inputs(p, brief) or (d["inputs"] == "" and all(s.get("kind") == OPEN for s in d["shots"]))


def shots_problem(p: Project, shots: list[Shot]) -> str:
    """The first way a (hand-edited) shots.json departs from the planned windows or the allowed kinds, or ""."""
    want = [(f"s{i:02d}", a, b) for i, (a, b, _) in enumerate(windows(p), 1)]
    if len(shots) != len(want):
        return f"shots.json has {len(shots)} shots, the windows need {len(want)}"
    for s, (sid, a, b) in zip(shots, want):
        if (s.id, s.start, s.end) != (sid, a, b):
            return f"{s.id} is {s.start:.2f}-{s.end:.2f}s, window {sid} is {a:.2f}-{b:.2f}s"
    for s in shots:
        if not s.is_open and s.kind not in p.kinds:
            return f"{s.id} is {s.kind}; this project allows {', '.join(p.kinds)}"
    return ""


def ensure_open_shots(p: Project) -> None:
    """While no shot is decided, shots.json holds one open shot per window, rewritten when the windows (or brief names) change."""
    brief = p.load_brief()
    want = {
        "inputs": shots_inputs(p, brief) if brief else "",
        "shots": [asdict(open_shot(f"s{i:02d}", a, b, sec)) for i, (a, b, sec) in enumerate(windows(p), 1)],
    }
    update_json(p, p.shots_path, lambda d: want if d is None or all(s.get("kind") == OPEN for s in d["shots"]) else d)


def merge_entry(p: Project, path: Path, sid: str, entry: dict) -> None:
    """Write one shot's entry into a per-shot JSON map, keeping every other shot's entry as it is on disk now."""
    update_json(p, path, lambda d: (d or {}) | {sid: entry})


# --- per-shot needs --------------------------------------------------------------------------------


def kf_need(p: Project, shot: Shot, brief: Brief, entry: dict | None) -> str:
    """ "gen" (missing, stale or rejected), "judge" (approval pending) or "done"."""
    if not entry or entry["prompt_sha"] != prompt_sha(shot, brief) or entry["approved"] is False or not (p.root / entry["path"]).exists():
        return "gen"
    return "done" if entry["approved"] else "judge"


# --- runners ---------------------------------------------------------------------------------------


@dataclass
class Run:
    p: Project
    gen: Generator
    chat_text: auto.ChatFn | None = None
    chat_vision: auto.ChatFn | None = None
    renderer: Renderer | None = None
    only: set[str] | None = None  # when set, the shots, render, keyframes and takes runners act only on these shot ids
    stack: ExitStack = field(default_factory=ExitStack)
    _open: bool = False

    def __post_init__(self) -> None:
        self.led = Ledger(self.p.ledger_path, self.p.cap_usd)

    def generator(self) -> Generator:
        """One gen.session() per advance call, opened only when something is generated."""
        if not self._open:
            self.stack.enter_context(self.gen.session())
            self._open = True
        return self.gen

    def renders(self) -> Renderer:
        if self.renderer is None:
            self.renderer = ChromiumRenderer()
        return self.renderer

    def wanted(self, shots: list[Shot]) -> list[Shot]:
        return [s for s in shots if self.only is None or s.id in self.only]

    def chat(self, step: str, kind: str) -> auto.ChatFn:
        fn = self.chat_text if kind == "text" else self.chat_vision
        if fn is None:
            raise auto.AutoError(f"auto {step} needs a {kind} model configured")
        return fn


def _row(**kw) -> dict:
    return {"kind": "job", "ts": time.time()} | kw


@contextmanager
def reserved(led: Ledger, gpu: str, wall_s: float) -> Iterator[None]:
    """Charge a batch's estimate before it launches and refund it in the finally: a hard kill leaves the estimate charged,
    a finished batch nets to its actual rows."""
    row = {"key": f"reserve-{time.time_ns()}", "shot": "", "gen": 0, "round": 0, "seed": 0, "model": RESERVE_MODEL, "gpu": gpu,
           "prompt": "", "keyframe": "", "output": None}  # fmt: skip
    led.append(_row(**row, wall_s=wall_s))
    try:
        yield
    finally:
        led.append(_row(**row, wall_s=-wall_s))


def _write_bytes(dest: Path, data: bytes) -> None:
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)  # a kill never leaves a half-written file


def run_timing(run: Run) -> None:
    write_json(run.p.timing_path, detect(run.p.song))
    features.ensure(run.p)
    ensure_open_shots(run.p)


def run_brief(run: Run) -> None:
    run.p.save_brief(auto.write_brief(run.chat("brief", "text"), run.p.config["seed"]), brief_inputs(run.p))


def run_shots(run: Run) -> None:
    from celery_man import auto_scene  # auto_scene imports renders, which imports this module

    p = run.p
    brief, d = p.load_brief(), read_json(p.shots_path)
    if d is None or not shots_fresh(p, brief, d) or shots_problem(p, p.load_shots()):
        chat = run.chat("shots", "text")
        shots, codes = auto.write_shots(chat, brief, windows(p), p.kinds)
        p.save_shots([auto_scene.settle(chat, run, s, brief, codes[s.id]) if s.id in codes else s for s in shots], shots_inputs(p, brief))
        return
    todo = [s for s in run.wanted(p.load_shots()) if s.is_open and p.mode("shots", s.id) == "auto"]
    if not todo:
        return
    chat = run.chat("shots", "text")
    shots, codes = auto.write_shots(chat, brief, [(s.start, s.end, s.section) for s in todo], p.kinds, [s.id for s in todo])
    new = {s.id: asdict(auto_scene.settle(chat, run, s, brief, codes[s.id]) if s.id in codes else s) for s in shots}
    stamp = shots_inputs(p, brief)
    # only windows still open on disk are filled: a person may have decided one meanwhile
    update_json(
        p, p.shots_path, lambda d: {"inputs": stamp, "shots": [new.get(x["id"], x) if x["kind"] == OPEN else x for x in d["shots"]]}
    )


def _stills(run: Run, kfs: dict, jobs: list[tuple[Shot, int]], brief: Brief, approved: bool | None, note: str = "") -> list[Shot]:
    """Generate keyframes into keys/ and keyframes.json; returns the shots whose still failed."""
    if not jobs:
        return []
    p, led, failed = run.p, run.led, []
    prompts = [compose(s, brief) for s, _ in jobs]
    p.keys_dir.mkdir(exist_ok=True)
    with led.locked():
        if not led.can_afford(cost(len(jobs), EST_STILL_S, STILL_GPU)):
            raise BudgetError(f"{len(jobs)} keyframes would pass the ${p.cap_usd} cap (spent ${led.spent_usd():.2f})")
        with reserved(led, STILL_GPU, EST_STILL_S * (len(jobs) + 1)):
            results = run.generator().stills([StillJob(s.id, pr, seed) for (s, seed), pr in zip(jobs, prompts)])
            for (s, seed), pr, res in zip(jobs, prompts, results, strict=True):
                row = {"key": job_key(s.id, seed, pr, b""), "shot": s.id, "round": 0, "seed": seed, "model": STILL_MODEL, "prompt": pr}
                if isinstance(res, BaseException):
                    log.error("keyframe %s failed: %r", s.id, res)
                    led.append(_row(**row, keyframe="", output=None, gpu=STILL_GPU, wall_s=EST_STILL_S, failed=True, error=repr(res)))
                    failed.append(s)
                    continue
                dest = p.keys_dir / f"{s.id}-{hashlib.sha256(res['png']).hexdigest()[:12]}.png"
                _write_bytes(dest, res["png"])
                led.append(_row(**row, keyframe="", output=str(dest.resolve()), gpu=res["gpu"], wall_s=res["wall_s"]))
                kfs[s.id] = {
                    "path": str(dest.relative_to(p.root)),
                    "seed": seed,
                    "prompt_sha": sha12(pr),
                    "approved": approved,
                    "note": note,
                }
                merge_entry(p, p.keyframes_path, s.id, kfs[s.id])  # each still is recorded as it lands, so a kill keeps the finished ones
    return failed


def run_keyframes(run: Run) -> None:
    p = run.p
    brief, shots, kfs = p.load_brief(), run.wanted(p.load_footage()), read_json(p.keyframes_path) or {}
    auto_shots = [s for s in shots if p.mode("keyframes", s.id) == "auto"]
    vision = run.chat("keyframes", "vision") if any(kf_need(p, s, brief, kfs.get(s.id)) != "done" for s in auto_shots) else None
    regen = [(s, kfs[s.id]["seed"] + 1 if s.id in kfs else 1) for s in shots if kf_need(p, s, brief, kfs.get(s.id)) == "gen"]
    _stills(run, kfs, regen, brief, approved=None)
    for s in auto_shots:
        if kf_need(p, s, brief, kfs.get(s.id)) != "judge":
            continue
        ok, why = auto.check_keyframe(vision, s, brief, p.root / kfs[s.id]["path"])
        if ok:
            kfs[s.id] |= {"approved": True, "note": why}
            merge_entry(p, p.keyframes_path, s.id, kfs[s.id])
            continue
        log.info("keyframe %s rejected (%s); regenerating once and accepting", s.id, why)
        if _stills(run, kfs, [(s, kfs[s.id]["seed"] + 1)], brief, approved=True, note=f"accepted after a rejected first still: {why}"):
            kfs[s.id] |= {"approved": False, "note": why}  # the retry failed: the next advance regenerates
            merge_entry(p, p.keyframes_path, s.id, kfs[s.id])


def run_assemble(run: Run) -> None:
    p = run.p
    shots, picks, ren = p.load_shots(), read_json(p.picks_path) or {}, read_json(p.renders_path) or {}
    src = {s.id: (ren[s.id]["clip"], 0) if s.kind == "animation" else (picks[s.id]["clip"], picks[s.id]["in"]) for s in shots}
    cuts = [{"clip": str(p.root / src[s.id][0]), "in": src[s.id][1], "start": s.start, "end": s.end} for s in shots]
    p.out_dir.mkdir(exist_ok=True)
    tmp = p.out_dir / "video.tmp.mp4"
    assemble(cuts, p.song, tmp)
    os.replace(tmp, p.out_dir / "video.mp4")
    write_report(p, run.led)
    write_provenance(p)


def write_report(p: Project, led: Ledger) -> None:
    shots, picks, kfs, m = p.load_shots(), read_json(p.picks_path) or {}, read_json(p.keyframes_path) or {}, p.modes
    jobs = [r for r in led.rows() if r.get("kind") == "job"]
    lines = ["# Report", "", f"Decided by: brief {m['brief']}, shots {m['shots']}, keyframes {m['keyframes']}, takes {m['takes']}.", ""]
    ren = read_json(p.renders_path) or {}
    for s in shots:
        if s.kind == "animation":
            sc, key = s.scene, ren.get(s.id, {}).get("key", "-")
            what = f"template `{sc['template']}`" if "template" in sc else f"file `{sc['file']}`"
            lines.append(f"- **{s.id}** (animation): {what}; render `{key}`" + (f"; note: {sc['note']}" if sc.get("note") else ""))
            continue
        pk, fails = picks.get(s.id, {}), sum(1 for r in jobs if r["shot"] == s.id and r.get("failed"))
        takes = [r for r in jobs if r["shot"] == s.id and r.get("model") == TAKE_MODEL]
        g = max((r["gen"] for r in takes), default=0)
        rounds = sorted({r["round"] for r in takes if r["gen"] == g}) or "-"
        how = " (quick)" if pk.get("quick") else f" (round {MAX_ROUND}, forced)" if pk.get("round") == MAX_ROUND else ""
        pick = f"`{pk['key']}`{how}" if "key" in pk else "none"
        note = kfs.get(s.id, {}).get("note") or "-"
        lines.append(f"- **{s.id}** (footage): gen {g or '-'} rounds {rounds}; pick {pick}; failures {fails}; keyframe note: {note}")
    if fell_back := [s.id for s in shots if picks.get(s.id, {}).get("quick")]:
        lines += ["", "## Quick fallback (cap reached)", "", "One unreviewed quick take each: " + ", ".join(fell_back)]
    if fell_back := [s.id for s in shots if s.scene and s.scene.get("note", "").startswith("fallback:")]:
        lines += ["", "## Scene fallbacks", "", "Custom scene failed, template used instead: " + ", ".join(fell_back)]
    lines += ["", f"Spend: ${led.spent_usd():.2f} of ${p.cap_usd:.2f} cap (ledger estimate; the Modal dashboard is the true spend).", ""]
    (p.out_dir / "report.md").write_text("\n".join(lines))


def write_provenance(p: Project) -> None:
    from celery_man.gen_modal import DIFFUSERS_SHA, FLUX, LTX

    fonts = "".join(
        f"- Font `{f.stem.removeprefix('OFL-')}`: {f.read_text().splitlines()[0]}, SIL Open Font License 1.1.\n"
        for f in sorted((SCENES / "fonts").glob("OFL-*.txt"))
    )
    (p.root / "PROVENANCE.md").write_text(
        "# Provenance\n\n"
        f"- Clips: `{LTX}`, LTX-2.x Community License.\n"
        f"- Keyframes: `{FLUX}`, Apache-2.0.\n"
        f"- Song: {p.config['song_source']}\n"
        f"- diffusers commit: `{DIFFUSERS_SHA}`\n" + fonts
    )
