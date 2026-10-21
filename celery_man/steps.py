"""Per-step pipeline work: decision stamps, keyframe needs, and the timing/brief/shots/keyframes/assemble runners (takes: takes.py)."""

import hashlib
import logging
import os
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from celery_man import auto
from celery_man.assemble import assemble
from celery_man.compose import compose, prompt_sha
from celery_man.generate import Generator, StillJob
from celery_man.ledger import Ledger, cost, job_key
from celery_man.project import Brief, Project, Shot, read_json, sha12, write_json
from celery_man.timing import detect, plan_windows

log = logging.getLogger(__name__)

EST_WALL_S = 260  # per L4 draft take, and once more per run for the container load
EST_STILL_S = 20  # per FLUX still on the L40S
STILL_MODEL, TAKE_MODEL, OVERHEAD_MODEL = "flux-schnell", "ltx-2.5-draft", "overhead"
STILL_GPU, TAKE_GPU = "L40S", "L4"
SEEDS = (1, 2, 3)
IN_POINT = 0.5
MAX_ROUND = 2


class OverBudget(RuntimeError):
    """Raised before spending past the cap. Task 9 turns this into the quick-take fallback."""


# --- decision stamps -------------------------------------------------------------------------------


def song_sha(p: Project) -> str:
    return hashlib.sha256(p.song.read_bytes()).hexdigest()[:12]


def brief_inputs(p: Project) -> str:
    return sha12(p.config["seed"])


def windows(p: Project) -> list[tuple[float, float, str]]:
    return plan_windows(read_json(p.timing_path), *p.range)


def shots_inputs(p: Project, brief: Brief) -> str:
    """R2: only names reach shot text, so a style or description edit leaves shots fresh."""
    return sha12([windows(p), sorted(c.name for c in brief.cast), sorted(x.name for x in brief.places)])


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

    def chat(self, step: str, kind: str) -> auto.ChatFn:
        fn = self.chat_text if kind == "text" else self.chat_vision
        if fn is None:
            raise auto.AutoError(f"auto {step} needs a {kind} model configured")
        return fn


def _row(**kw) -> dict:
    return {"kind": "job", "ts": time.time()} | kw


def _write_bytes(dest: Path, data: bytes) -> None:
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)  # a kill never leaves a half-written file


def run_timing(run: Run) -> None:
    write_json(run.p.timing_path, detect(run.p.song))


def run_brief(run: Run) -> None:
    run.p.save_brief(auto.write_brief(run.chat("brief", "text"), run.p.config["seed"]), brief_inputs(run.p))


def run_shots(run: Run) -> None:
    brief = run.p.load_brief()
    run.p.save_shots(auto.write_shots(run.chat("shots", "text"), brief, windows(run.p)), shots_inputs(run.p, brief))


def _stills(run: Run, kfs: dict, jobs: list[tuple[Shot, int]], brief: Brief, approved: bool | None, note: str = "") -> list[Shot]:
    """Generate keyframes into keys/ and keyframes.json; returns the shots whose still failed."""
    if not jobs:
        return []
    p, led, failed = run.p, run.led, []
    prompts = [compose(s, brief) for s, _ in jobs]
    p.keys_dir.mkdir(exist_ok=True)
    with led.locked():
        if not led.can_afford(cost(len(jobs), EST_STILL_S, STILL_GPU)):
            raise OverBudget(f"{len(jobs)} keyframes would pass the ${p.cap_usd} cap (spent ${led.spent_usd():.2f})")
        results = run.generator().stills([StillJob(s.id, pr, seed) for (s, seed), pr in zip(jobs, prompts)])
        try:
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
        finally:
            write_json(p.keyframes_path, kfs)  # finished stills stay recorded even if a later one breaks the loop
    return failed


def run_keyframes(run: Run) -> None:
    p = run.p
    brief, shots, kfs = p.load_brief(), p.load_shots(), read_json(p.keyframes_path) or {}
    auto_mode = p.modes["keyframes"] == "auto"
    vision = run.chat("keyframes", "vision") if auto_mode else None
    regen = [(s, kfs[s.id]["seed"] + 1 if s.id in kfs else 1) for s in shots if kf_need(p, s, brief, kfs.get(s.id)) == "gen"]
    _stills(run, kfs, regen, brief, approved=None)
    if not auto_mode:
        return
    for s in shots:
        if kf_need(p, s, brief, kfs.get(s.id)) != "judge":
            continue
        ok, why = auto.check_keyframe(vision, s, brief, p.root / kfs[s.id]["path"])
        if ok:
            kfs[s.id] |= {"approved": True, "note": why}
            write_json(p.keyframes_path, kfs)
            continue
        log.info("keyframe %s rejected (%s); regenerating once and accepting", s.id, why)
        if _stills(run, kfs, [(s, kfs[s.id]["seed"] + 1)], brief, approved=True, note=f"accepted after a rejected first still: {why}"):
            kfs[s.id] |= {"approved": False, "note": why}  # the retry failed: the next advance regenerates
            write_json(p.keyframes_path, kfs)


def run_assemble(run: Run) -> None:
    p = run.p
    shots, picks = p.load_shots(), read_json(p.picks_path)
    cuts = [{"clip": str(p.root / picks[s.id]["clip"]), "in": picks[s.id]["in"], "start": s.start, "end": s.end} for s in shots]
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
    for s in shots:
        pk, fails = picks.get(s.id, {}), sum(1 for r in jobs if r["shot"] == s.id and r.get("failed"))
        takes = [r for r in jobs if r["shot"] == s.id and r.get("model") == TAKE_MODEL]
        g = max((r["gen"] for r in takes), default=0)
        rounds = sorted({r["round"] for r in takes if r["gen"] == g}) or "-"
        pick = f"`{pk['key']}`" + (" (quick)" if pk.get("quick") else "") if "key" in pk else "none"
        note = kfs.get(s.id, {}).get("note") or "-"
        lines.append(f"- **{s.id}**: gen {g or '-'} rounds {rounds}; pick {pick}; failures {fails}; keyframe note: {note}")
    lines += ["", f"Spend: ${led.spent_usd():.2f} of ${p.cap_usd:.2f} cap (ledger estimate; the Modal dashboard is the true spend).", ""]
    (p.out_dir / "report.md").write_text("\n".join(lines))


def write_provenance(p: Project) -> None:
    from celery_man.gen_modal import DIFFUSERS_SHA, FLUX, LTX

    (p.root / "PROVENANCE.md").write_text(
        "# Provenance\n\n"
        f"- Clips: `{LTX}`, LTX-2.x Community License.\n"
        f"- Keyframes: `{FLUX}`, Apache-2.0.\n"
        f"- Song: {p.config['song_source']}\n"
        f"- diffusers commit: `{DIFFUSERS_SHA}`\n"
    )
