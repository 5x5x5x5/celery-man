"""Takes: per-shot take plans (the 2-round lineage cap), the take batch runner, contact sheets and the takes step."""

import logging
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from celery_man import auto
from celery_man.assemble import contact_sheet
from celery_man.compose import compose
from celery_man.generate import ClipJob
from celery_man.ledger import Ledger, cost, job_key
from celery_man.project import Brief, Project, Shot, read_json, update_json
from celery_man.steps import (
    EST_STILL_S,
    EST_WALL_S,
    IN_POINT,
    MAX_ROUND,
    OVERHEAD_MODEL,
    QUICK_MODEL,
    QUICK_SEED,
    SEEDS,
    STILL_GPU,
    TAKE_GPU,
    TAKE_MODEL,
    BudgetError,
    Run,
    _row,
    _write_bytes,
    kf_need,
    merge_entry,
    reserved,
    shots_inputs,
)

log = logging.getLogger(__name__)


@dataclass
class TakePlan:
    need: str  # done | run | judge | edit | limit | fallback (past the last round with no usable take: a quick take ships)
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


def pick_ok(p: Project, led: Ledger, pick: dict | None, prompt: str, kf: bytes) -> bool:
    if not pick or pick.get("rejected"):
        return False
    if pick.get("uploaded"):
        return (p.root / pick["clip"]).is_file()  # pinned: no ledger row, whatever the prompt says now
    row = led.done(pick["key"])
    return bool(row) and row["prompt"] == prompt and Path(row["keyframe"]).is_file() and Path(row["keyframe"]).read_bytes() == kf


def round_cap(p: Project, sid: str) -> int | None:
    """Auto shots stop at MAX_ROUND; a tastemaker decides when to stop paying for rounds."""
    return MAX_ROUND if p.mode("takes", sid) == "auto" else None


def take_plan(p: Project, led: Ledger, shot: Shot, brief: Brief, entry: dict | None, pick: dict | None) -> TakePlan:
    """Next takes for a shot. The 2-round cap is per pick lineage ("gen", R5), read from the current gen's take rows:

    - the current prompt+keyframe hash matches the latest round's rows and that round was not rejected: resume that round;
    - that round was rejected: next round of the same gen (any edit made after a rejection is the rewrite);
      round 2 with an unchanged hash is "edit" (not_rewritten), past round 2 is "limit";
    - otherwise the shot changed (brief, shot or keyframe edit) without a rejection: a fresh gen at round 1.
    """
    if pick and pick.get("uploaded") and pick_ok(p, led, pick, "", b""):
        return TakePlan("done")
    if kf_need(p, shot, brief, entry) != "done":
        return TakePlan("run", missing=len(SEEDS))
    prompt, kf_path = compose(shot, brief), p.root / entry["path"]
    kf = kf_path.read_bytes()
    if pick_ok(p, led, pick, prompt, kf):
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
    cap = round_cap(p, shot.id)
    if cap and r > cap:
        last = [x["key"] for x in cur if x["round"] == cap]
        plan.need, plan.missing = ("limit", 0) if any(led.done(k) and sheet(p, k) for k in last) else ("fallback", 1)
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
    """Paper-machine's pattern: each clip's file then its ledger row as it lands, failures billed at EST_WALL_S, the batch's
    estimate reserved up front, overhead row in finally. Sheets come after the batch, so one bad clip never loses the rest."""
    p, led, written = run.p, run.led, []
    p.takes_dir.mkdir(exist_ok=True)
    with led.locked():
        if not led.can_afford(cost(len(jobs) + 1, EST_WALL_S, TAKE_GPU)):  # +1 reserves the run's container load
            raise BudgetError(f"{len(jobs)} takes would pass the ${p.cap_usd} cap (spent ${led.spent_usd():.2f})")
        in_call, t0 = 0.0, time.monotonic()
        with reserved(led, TAKE_GPU, EST_WALL_S * (len(jobs) + 1)):
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
    brief, shots, kfs, picks = (
        p.load_brief(),
        run.wanted(p.load_footage()),
        read_json(p.keyframes_path) or {},
        read_json(p.picks_path) or {},
    )
    return brief, shots, picks, {s.id: take_plan(p, run.led, s, brief, kfs.get(s.id), picks.get(s.id)) for s in shots}


def _rewrite_rejected(run: Run) -> bool:
    """Auto shots: rewrite every one whose rejected round is unchanged; its keyframe goes stale, so advance loops back."""
    p = run.p
    brief, shots, picks, plans = _plans(run)
    edits = [s for s in shots if plans[s.id].need == "edit" and p.mode("takes", s.id) == "auto"]
    stamp = shots_inputs(p, brief)
    for s in edits:
        new = auto.rewrite_shot(run.chat("takes", "text"), s, brief, picks.get(s.id, {}).get("reason", ""))
        if new == s:
            raise auto.AutoError(f"rewrite produced the same shot ({s.id})")
        update_json(
            p,
            p.shots_path,
            lambda d, new=new: d | {"inputs": stamp, "shots": [asdict(new) if x["id"] == new.id else x for x in d["shots"]]},
        )
    return bool(edits)


def _quick_picks(run: Run, shots: list[Shot], brief: Brief, plans: dict[str, TakePlan], picks: dict) -> None:
    for sid, key in quick_takes(run, shots, brief, read_json(run.p.keyframes_path)).items():
        picks[sid] = {"key": key, "clip": f"{run.p.takes_dir.name}/{key}.mp4", "in": IN_POINT, "round": 0, "gen": plans[sid].gen,
                      "quick": True}  # fmt: skip
        merge_entry(run.p, run.p.picks_path, sid, picks[sid])


def _no_usable_take(run: Run) -> None:
    """Either mode: a round with no readable take is rejected as unusable (then rewritten or edited, as any rejection);
    in the last round, or once past it, the shot ships one quick take instead."""
    brief, shots, picks, plans = _plans(run)
    dead = [s for s in shots if plans[s.id].need == "judge" and not any(sheet(run.p, k) for k, _ in plans[s.id].keys)]
    for s in dead:
        log.error("%s: no usable take in gen %d round %d", s.id, plans[s.id].gen, plans[s.id].round)
        cap = round_cap(run.p, s.id)
        if cap is None or plans[s.id].round < cap:
            picks[s.id] = {"rejected": True, "round": plans[s.id].round, "gen": plans[s.id].gen, "reason": "all takes unusable"}
            merge_entry(run.p, run.p.picks_path, s.id, picks[s.id])
    if last := [
        s for s in shots if plans[s.id].need == "fallback" or (s in dead and (cap := round_cap(run.p, s.id)) and plans[s.id].round >= cap)
    ]:
        _quick_picks(run, last, brief, plans, picks)


def run_takes(run: Run) -> None:
    p = run.p
    brief, shots, picks, plans = _plans(run)
    auto_ids = {s.id for s in shots if p.mode("takes", s.id) == "auto"}
    vision = run.chat("takes", "vision") if any(plans[sid].todo or plans[sid].need == "judge" for sid in auto_ids) else None
    if _rewrite_rejected(run):
        return
    for s in shots:
        if plans[s.id].need == "limit":  # R6: per shot; the rest still run
            log.warning("%s: round %d refused; pick a round-2 take", s.id, plans[s.id].round)
    jobs = [(s, k, seed, plans[s.id]) for s in shots if plans[s.id].need == "run" for k, seed in plans[s.id].todo]
    fallback = list({s.id: s for s, *_ in jobs}.values())
    # a round launches only if the quick fallback stays payable after it: one quick take per shot plus a load, and in
    # auto mode one still per shot for a rewrite after a rejection (otherwise a rejection can strand the video)
    reserve = cost(len(fallback) + 1, EST_WALL_S, TAKE_GPU) + cost(sum(s.id in auto_ids for s in fallback), EST_STILL_S, STILL_GPU)
    if jobs and not run.led.can_afford(cost(len(jobs) + 1, EST_WALL_S, TAKE_GPU) + reserve):
        # the round won't fit: each unfinished shot gets one quick take, used as is, so the video still ships
        log.warning("cap reached: quick takes instead of the round for %s", ", ".join(s.id for s in fallback))
        _quick_picks(run, fallback, brief, plans, picks)
    elif jobs:
        _run_takes(run, jobs)
    _no_usable_take(run)
    brief, shots, picks, plans = _plans(run)
    for s in shots:
        pl = plans[s.id]
        if pl.need != "judge" or s.id not in auto_ids:
            continue
        usable = [(k, sh) for k, _ in pl.keys if (sh := sheet(p, k))]  # never empty: _no_usable_take handled those
        idx, why = auto.pick_take(vision, s, brief, [sh for _, sh in usable], must_pick=pl.round == round_cap(p, s.id))
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
        merge_entry(p, p.picks_path, s.id, picks[s.id])
    _rewrite_rejected(run)
