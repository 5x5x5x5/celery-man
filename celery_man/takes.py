"""Takes: per-shot take plans (the 2-round lineage cap), the take batch runner, contact sheets and the takes step."""

import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from celery_man import auto
from celery_man.assemble import contact_sheet
from celery_man.compose import compose
from celery_man.generate import ClipJob
from celery_man.ledger import Ledger, cost, job_key
from celery_man.project import Brief, Project, Shot, read_json, write_json
from celery_man.steps import (
    EST_WALL_S,
    IN_POINT,
    MAX_ROUND,
    OVERHEAD_MODEL,
    QUICK_MODEL,
    QUICK_SEED,
    SEEDS,
    TAKE_GPU,
    TAKE_MODEL,
    BudgetError,
    Run,
    _row,
    _write_bytes,
    kf_need,
    shots_inputs,
)

log = logging.getLogger(__name__)


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


def _run_takes(run: Run, jobs: list[tuple[Shot, str, int, TakePlan]], model: str = TAKE_MODEL) -> None:
    """Paper-machine's pattern: file first then its ledger row, failures billed at EST_WALL_S, overhead row in finally.
    Sheets come after every file and row is written, so one bad clip never loses the rest of the batch."""
    p, led, written = run.p, run.led, []
    p.takes_dir.mkdir(exist_ok=True)
    with led.locked():
        if not led.can_afford(cost(len(jobs) + 1, EST_WALL_S, TAKE_GPU)):  # +1 reserves the run's container load
            raise BudgetError(f"{len(jobs)} takes would pass the ${p.cap_usd} cap (spent ${led.spent_usd():.2f})")
        in_call, t0 = 0.0, time.monotonic()
        try:
            results = run.generator().clips([ClipJob(s.id, key, pl.prompt, seed, pl.kf) for s, key, seed, pl in jobs])
            for (s, key, seed, pl), res in zip(jobs, results, strict=True):
                row = {"key": key, "shot": s.id, "gen": pl.gen, "round": pl.round, "seed": seed, "model": model, "prompt": pl.prompt}
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


def quick_key(p: Project, shot: Shot, brief: Brief, entry: dict) -> str:
    return job_key(shot.id, QUICK_SEED, compose(shot, brief), (p.root / entry["path"]).read_bytes())


def quick_takes(run: Run, shots: list[Shot], brief: Brief, kfs: dict) -> dict[str, str]:
    """One unreviewed seed-1 take per shot (ledger model "quick", gen 0, round 0), reusing finished ones.
    Returns {shot_id: key} for the shots that have their take; failures are logged by _run_takes."""
    keys = {s.id: quick_key(run.p, s, brief, kfs[s.id]) for s in shots}
    jobs = []
    for s in shots:
        if not run.led.done(keys[s.id]):
            kf_path = run.p.root / kfs[s.id]["path"]
            jobs.append(
                (s, keys[s.id], QUICK_SEED, TakePlan("run", prompt=compose(s, brief), kf=kf_path.read_bytes(), kf_path=kf_path, gen=0))
            )
    if jobs:
        _run_takes(run, jobs, QUICK_MODEL)
    return {sid: k for sid, k in keys.items() if run.led.done(k)}


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
    if jobs and not run.led.can_afford(cost(len(jobs) + 1, EST_WALL_S, TAKE_GPU)):
        # the round won't fit: each unfinished shot gets one quick take, used as is, so the video still ships
        kfs = read_json(p.keyframes_path)
        fallback = list({s.id: s for s, *_ in jobs}.values())
        log.warning("cap reached: quick takes instead of the round for %s", ", ".join(s.id for s in fallback))
        for sid, key in quick_takes(run, fallback, brief, kfs).items():
            picks[sid] = {
                "key": key,
                "clip": f"{p.takes_dir.name}/{key}.mp4",
                "in": IN_POINT,
                "round": 0,
                "gen": plans[sid].gen,
                "quick": True,
            }
        write_json(p.picks_path, picks)
    elif jobs:
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
