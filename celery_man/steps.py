"""Per-step pipeline work: decision stamps, per-shot needs, and the timing/brief/shots/keyframes/takes/assemble runners."""

import hashlib
import logging
import os
import subprocess
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from celery_man import auto
from celery_man.assemble import assemble, contact_sheet
from celery_man.compose import compose, prompt_sha
from celery_man.generate import ClipJob, Generator, StillJob
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


@dataclass
class TakePlan:
    need: str  # done | run | judge | edit | limit
    missing: int = 0
    round: int = 0
    prompt: str = ""
    kf: bytes = b""
    kf_path: Path | None = None
    keys: list[tuple[str, int]] = field(default_factory=list)  # (key, seed) for the round
    todo: list[tuple[str, int]] = field(default_factory=list)  # the keys not done yet
    gen: int = 1


def _h(key: str) -> str:
    return key.rsplit("-", 1)[1]  # the prompt+keyframe hash part of a job key; seeds differ per round


def pick_ok(led: Ledger, pick: dict | None, prompt: str, kf: bytes) -> bool:
    if not pick or pick.get("rejected"):
        return False
    row = led.done(pick["key"])
    return bool(row) and row["prompt"] == prompt and Path(row["keyframe"]).is_file() and Path(row["keyframe"]).read_bytes() == kf


def take_plan(p: Project, led: Ledger, shot: Shot, brief: Brief, entry: dict | None, pick: dict | None) -> TakePlan:
    """Next takes for a shot. The 2-round cap is per pick lineage ("gen", R5), read from the current gen's take rows:

    - the current prompt+keyframe hash matches the latest round's rows and that round was not rejected: resume that round;
    - that round was rejected: next round of the same gen (any edit made after a rejection is the rewrite);
      round 2 with an unchanged hash is "edit" (not_rewritten), past round 2 is "limit";
    - otherwise the shot changed (brief, shot or keyframe edit) without a rejection: a fresh gen at round 1.
    """
    if kf_need(p, shot, brief, entry) != "done":
        return TakePlan("run", missing=len(SEEDS))
    prompt, kf_path = compose(shot, brief), p.root / entry["path"]
    kf = kf_path.read_bytes()
    if pick_ok(led, pick, prompt, kf):
        return TakePlan("done")
    rows = [r for r in led.rows() if r.get("kind") == "job" and r.get("shot") == shot.id and r.get("model") == TAKE_MODEL]
    h = _h(job_key(shot.id, 0, prompt, kf))
    g = max((x["gen"] for x in rows), default=1)
    cur = [x for x in rows if x["gen"] == g]
    r = max((x["round"] for x in cur), default=1)
    same = any(x["round"] == r and _h(x["key"]) == h for x in cur)
    rejected = bool(pick and pick.get("rejected") and pick.get("gen", g) == g and pick["round"] == r)
    if rejected:
        r += 1
    elif cur and not same:
        g, r = g + 1, 1
    plan = TakePlan("run", 0, r, prompt, kf, kf_path, gen=g)
    if r > MAX_ROUND:
        plan.need = "limit"
    elif r > 1 and h in {_h(x["key"]) for x in cur if x["round"] < r}:  # not_rewritten, from paper-machine direct.py
        plan.need = "edit"
    else:
        seeds = [g * 1000 + r * 100 + n for n in SEEDS]  # unique across gens and rounds
        plan.keys = [(job_key(shot.id, sd, prompt, kf), sd) for sd in seeds]
        plan.todo = [(k, sd) for k, sd in plan.keys if not led.done(k)]
        plan.need, plan.missing = ("run", len(plan.todo)) if plan.todo else ("judge", 0)
    return plan


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


def sheet(p: Project, key: str) -> Path | None:
    """The take's contact sheet, built on demand. None (logged) when the clip is unreadable: that take is never judged."""
    dest = p.sheets_dir / f"{key}.jpg"
    if dest.exists():
        return dest
    tmp = dest.with_name(f"{key}.tmp.jpg")
    try:
        contact_sheet(p.takes_dir / f"{key}.mp4", tmp)
    except subprocess.CalledProcessError as e:
        log.error("take %s is unusable, no contact sheet: %s", key, e)
        tmp.unlink(missing_ok=True)
        return None
    os.replace(tmp, dest)
    return dest


def _run_takes(run: Run, jobs: list[tuple[Shot, str, int, TakePlan]]) -> None:
    """Paper-machine's pattern: file first then its ledger row, failures billed at EST_WALL_S, overhead row in finally.
    Sheets come after every file and row is written, so one bad clip never loses the rest of the batch."""
    p, led, written = run.p, run.led, []
    p.takes_dir.mkdir(exist_ok=True)
    with led.locked():
        if not led.can_afford(cost(len(jobs) + 1, EST_WALL_S, TAKE_GPU)):  # +1 reserves the run's container load
            raise OverBudget(f"{len(jobs)} takes would pass the ${p.cap_usd} cap (spent ${led.spent_usd():.2f})")
        in_call, t0 = 0.0, time.monotonic()
        try:
            results = run.generator().clips([ClipJob(s.id, key, pl.prompt, seed, pl.kf) for s, key, seed, pl in jobs])
            for (s, key, seed, pl), res in zip(jobs, results, strict=True):
                row = {"key": key, "shot": s.id, "gen": pl.gen, "round": pl.round, "seed": seed, "model": TAKE_MODEL, "prompt": pl.prompt}
                row["keyframe"] = str(pl.kf_path.resolve())
                if isinstance(res, BaseException):
                    log.error("take %s failed: %r", key, res)
                    led.append(_row(**row, output=None, gpu=TAKE_GPU, wall_s=EST_WALL_S, failed=True, error=repr(res)))
                    continue
                dest = (p.takes_dir / f"{key}.mp4").resolve()
                _write_bytes(dest, res["mp4"])
                led.append(_row(**row, output=str(dest), gpu=res["gpu"], wall_s=res["wall_s"]))
                in_call += res["wall_s"]
                written.append(key)
        finally:
            led.append(_row(key=f"run-{int(t0)}", shot="", gen=0, round=0, seed=0, model=OVERHEAD_MODEL, gpu=TAKE_GPU, prompt="",
                            keyframe="", output=None, wall_s=max(0.0, time.monotonic() - t0 - in_call)))  # fmt: skip
    for key in written:
        sheet(p, key)


def _plans(run: Run) -> tuple[Brief, list[Shot], dict, dict[str, TakePlan]]:
    p = run.p
    brief, shots, kfs, picks = p.load_brief(), p.load_shots(), read_json(p.keyframes_path) or {}, read_json(p.picks_path) or {}
    return brief, shots, picks, {s.id: take_plan(p, run.led, s, brief, kfs.get(s.id), picks.get(s.id)) for s in shots}


def _rewrite_rejected(run: Run) -> bool:
    """Auto: rewrite every shot whose rejected round is unchanged; its keyframe goes stale, so advance loops back."""
    brief, shots, picks, plans = _plans(run)
    edits = {s.id for s in shots if plans[s.id].need == "edit"}
    if not edits:
        return False
    out = []
    for s in shots:
        if s.id in edits:
            new = auto.rewrite_shot(run.chat("takes", "text"), s, brief, picks.get(s.id, {}).get("reason", ""))
            if new == s:
                raise auto.AutoError(f"rewrite produced the same shot ({s.id})")
            s = new
        out.append(s)
    run.p.save_shots(out, shots_inputs(run.p, brief))
    return True


def run_takes(run: Run) -> None:
    p = run.p
    auto_mode = p.modes["takes"] == "auto"
    vision = run.chat("takes", "vision") if auto_mode else None
    if auto_mode and _rewrite_rejected(run):
        return
    brief, shots, picks, plans = _plans(run)
    for s in shots:
        if plans[s.id].need == "limit":  # R6: per shot; the rest still run
            log.warning("%s: round %d refused; pick a round-2 take", s.id, plans[s.id].round)
    jobs = [(s, k, seed, plans[s.id]) for s in shots if plans[s.id].need == "run" for k, seed in plans[s.id].todo]
    if jobs:
        _run_takes(run, jobs)
    if not auto_mode:
        return
    brief, shots, picks, plans = _plans(run)
    for s in shots:
        pl = plans[s.id]
        if pl.need != "judge":
            continue
        usable = [(k, sh) for k, _ in pl.keys if (sh := sheet(p, k))]
        if not usable:
            log.error("%s: no usable take in gen %d round %d", s.id, pl.gen, pl.round)
            continue
        idx, why = auto.pick_take(vision, s, brief, [sh for _, sh in usable], must_pick=pl.round == MAX_ROUND)
        if idx is None:
            picks[s.id] = {"rejected": True, "gen": pl.gen, "round": pl.round, "reason": why}
        else:
            key = usable[idx][0]
            picks[s.id] = {
                "key": key,
                "clip": f"{p.takes_dir.name}/{key}.mp4",
                "in": IN_POINT,
                "gen": pl.gen,
                "round": pl.round,
                "quick": False,
            }
        write_json(p.picks_path, picks)
    _rewrite_rejected(run)


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
