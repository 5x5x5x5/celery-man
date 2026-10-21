"""Pipeline: hash-stamped decisions, per-step staleness, and advance in either mode."""

import fcntl
import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from celery_man import steps, takes
from celery_man.assemble import assemble
from celery_man.auto import AutoError, ChatFn
from celery_man.generate import Generator
from celery_man.ledger import Ledger, cost
from celery_man.project import Brief, Project, Shot, read_json, write_json
from celery_man.steps import BudgetError

log = logging.getLogger(__name__)

STEPS = ("timing", "brief", "shots", "keyframes", "takes", "assemble")
RUNNERS = {
    "timing": steps.run_timing,
    "brief": steps.run_brief,
    "shots": steps.run_shots,
    "keyframes": steps.run_keyframes,
    "takes": takes.run_takes,
    "assemble": steps.run_assemble,
}
MAX_PASSES = 20
SPENDING = ("keyframes", "takes")


class WaitingError(RuntimeError):
    """quick() needs a tastemaker decision (brief or shots) that is missing or stale (R4; the CLI exits 2)."""

    def __init__(self, step: str) -> None:
        super().__init__(f"waiting on the {step} decision")
        self.step = step


class BusyError(RuntimeError):
    """Another run (CLI or web) holds the project's run.lock (the CLI exits 2)."""


class Declined(RuntimeError):
    """quick()'s confirm callback refused the cost at the spend gate; nothing was spent (the CLI exits 1)."""


@contextmanager
def run_lock(p: Project) -> Iterator[None]:
    """Exclusive, non-blocking lock on root/run.lock for a whole advance or quick, across processes."""
    with (p.root / "run.lock").open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BusyError(f"another run is going in {p.root}") from None
        yield


def run_busy(p: Project) -> bool:
    try:
        with run_lock(p):
            return False
    except BusyError:
        return True


@dataclass
class StepState:
    step: str
    state: str  # done | stale | missing | waiting
    detail: str = ""


def _decision(p: Project, step: str, fresh: bool | None) -> StepState:
    state = "missing" if fresh is None else "done" if fresh else "stale"
    if state != "done" and p.modes[step] == "tastemaker":
        return StepState(step, "waiting", state)
    return StepState(step, state)


def _per_shot(p: Project, step: str, needs: dict[str, str], wait_on: set[str], have_file: bool) -> StepState:
    """wait_on: needs only a person can clear in tastemaker mode; "limit" waits in either mode (R6)."""
    open_ = {sid: n for sid, n in needs.items() if n != "done"}
    detail = ", ".join(f"{sid}: pick a round-2 take" if n == "limit" else f"{sid} {n}" for sid, n in open_.items())
    if not open_:
        return StepState(step, "done")
    if set(open_.values()) <= ({"limit"} | (wait_on if p.modes[step] == "tastemaker" else set())):
        return StepState(step, "waiting", detail)
    return StepState(step, "stale" if have_file else "missing", detail)


def status(p: Project) -> list[StepState]:
    timing, brief_d = read_json(p.timing_path), read_json(p.brief_path)
    out = [StepState("timing", "missing" if timing is None else "done" if timing["song_sha"] == steps.song_sha(p) else "stale")]
    out.append(_decision(p, "brief", None if brief_d is None else brief_d["inputs"] == steps.brief_inputs(p)))
    shots_d = read_json(p.shots_path)
    brief = p.load_brief()
    fresh = None if shots_d is None or brief is None or timing is None else shots_d["inputs"] == steps.shots_inputs(p, brief)
    out.append(_decision(p, "shots", fresh))
    shots = p.load_shots()
    if brief is None or not shots:
        return out + [StepState(s, "missing") for s in STEPS[3:]]
    kfs, picks, led = read_json(p.keyframes_path) or {}, read_json(p.picks_path) or {}, Ledger(p.ledger_path)
    out.append(_per_shot(p, "keyframes", {s.id: steps.kf_need(p, s, brief, kfs.get(s.id)) for s in shots}, {"judge"}, bool(kfs)))
    needs = {s.id: takes.take_plan(p, led, s, brief, kfs.get(s.id), picks.get(s.id)).need for s in shots}
    out.append(_per_shot(p, "takes", needs, {"judge", "edit"}, p.picks_path.exists()))
    video = p.out_dir / "video.mp4"
    if not video.exists():
        out.append(StepState("assemble", "missing"))
    elif out[-1].state != "done" or video.stat().st_mtime < p.picks_path.stat().st_mtime:
        out.append(StepState("assemble", "stale"))
    else:
        out.append(StepState("assemble", "done"))
    return out


def estimate(p: Project) -> float:
    """USD for the keyframes and takes advance would generate now (shots awaiting a rewrite are not counted)."""
    brief, shots = p.load_brief(), p.load_shots()
    if brief is None or not shots:
        return 0.0
    kfs, picks, led = read_json(p.keyframes_path) or {}, read_json(p.picks_path) or {}, Ledger(p.ledger_path)
    n_kf = sum(steps.kf_need(p, s, brief, kfs.get(s.id)) == "gen" for s in shots)
    n_takes = sum(takes.take_plan(p, led, s, brief, kfs.get(s.id), picks.get(s.id)).missing for s in shots)
    return cost(n_kf, steps.EST_STILL_S, steps.STILL_GPU) + (cost(n_takes + 1, steps.EST_WALL_S, steps.TAKE_GPU) if n_takes else 0.0)


def _quick_price(p: Project, led: Ledger, shots: list[Shot], brief: Brief, kfs: dict) -> float:
    """Only what a quick pass would actually generate: missing/stale stills, takes not in the ledger, one load if any take."""
    stills = {s.id for s in shots if steps.kf_need(p, s, brief, kfs.get(s.id)) == "gen"}
    n_takes = sum(s.id in stills or not led.done(takes.quick_key(p, s, brief, kfs[s.id])) for s in shots)
    return cost(len(stills), steps.EST_STILL_S, steps.STILL_GPU) + (cost(n_takes + 1, steps.EST_WALL_S, steps.TAKE_GPU) if n_takes else 0.0)


def quick_cost(p: Project) -> float:
    """USD for a quick pass over the shots that have no valid pick: the floor every advance must be able to pay."""
    brief, shots = p.load_brief(), p.load_shots()
    if brief is None or not shots:
        return 0.0
    kfs, picks, led = read_json(p.keyframes_path) or {}, read_json(p.picks_path) or {}, Ledger(p.ledger_path)
    # "judge" and "limit" shots have their takes paid for; they only wait on a pick
    open_ = [s for s in shots if takes.take_plan(p, led, s, brief, kfs.get(s.id), picks.get(s.id)).need not in ("done", "judge", "limit")]
    return _quick_price(p, led, open_, brief, kfs)


def _preflight(p: Project, led: Ledger, need: float) -> None:
    if need and led.spent_usd() + need > p.cap_usd:
        raise BudgetError(f"a quick pass over the range needs ${need:.2f}, ${p.cap_usd - led.spent_usd():.2f} left")


Confirm = Callable[[float], bool]


def quick(p: Project, gen: Generator, chat_text: ChatFn | None = None, confirm: Confirm | None = None) -> Path:
    """Test mode (R3): one unreviewed seed-1 take per shot, cut to out/quick.mp4. Never writes picks.json; keyframe and
    take modes are ignored, but its stills land in keyframes.json unapproved (note "quick") so a judged run reviews them.
    confirm gets the quick cost once the brief and shots exist, before any spend; False raises Declined."""
    run = steps.Run(p, gen, chat_text)
    with run_lock(p), run.stack:
        for i, step in enumerate(STEPS[:3]):
            st = status(p)[i]
            if st.state == "waiting":
                raise WaitingError(step)
            if st.state != "done":
                RUNNERS[step](run)
                if (st := status(p)[i]).state != "done":
                    raise WaitingError(step) if st.state == "waiting" else AutoError(f"quick: {step} is still {st.state} after running it")
        brief, shots, kfs = p.load_brief(), p.load_shots(), read_json(p.keyframes_path) or {}
        price = _quick_price(p, run.led, shots, brief, kfs)
        if confirm and not confirm(price):
            raise Declined(f"quick pass (${price:.2f}) declined; nothing spent")
        _preflight(p, run.led, price)
        regen = [(s, steps.QUICK_SEED) for s in shots if steps.kf_need(p, s, brief, kfs.get(s.id)) == "gen"]
        failed = {s.id for s in steps._stills(run, kfs, regen, brief, approved=None, note="quick")}
        keys = takes.quick_takes(run, [s for s in shots if s.id not in failed], brief, kfs)
    if missing := [s.id for s in shots if s.id not in keys]:
        raise RuntimeError(f"quick: no take for {', '.join(missing)}; rerun to retry them")
    timeline = [{"shot": s.id, "key": keys[s.id], "clip": f"{p.takes_dir.name}/{keys[s.id]}.mp4", "in": steps.IN_POINT,
                 "start": s.start, "end": s.end} for s in shots]  # fmt: skip
    p.out_dir.mkdir(exist_ok=True)
    write_json(p.out_dir / "quick_timeline.json", timeline)
    tmp, dest = p.out_dir / "quick.tmp.mp4", p.out_dir / "quick.mp4"
    assemble([{"clip": str(p.root / c["clip"]), "in": c["in"], "start": c["start"], "end": c["end"]} for c in timeline], p.song, tmp)
    os.replace(tmp, dest)
    return dest


def advance(
    p: Project, gen: Generator, chat_text: ChatFn | None = None, chat_vision: ChatFn | None = None, confirm: Confirm | None = None
) -> list[StepState]:
    """Run steps in order until everything is done, a tastemaker decision is missing, or a step cannot finish.
    confirm gets the estimate once, at the first spending step (the brief and shots exist by then); False stops there."""
    run = steps.Run(p, gen, chat_text, chat_vision)
    with run_lock(p), run.stack:
        sts, checked = status(p), False
        for _ in range(MAX_PASSES):
            i = next((i for i, s in enumerate(sts) if s.state != "done"), None)
            if i is None or sts[i].state == "waiting":
                return sts
            log.info("advance: %s (%s)", sts[i].step, sts[i].state)
            if sts[i].step in SPENDING and not checked:  # once per call, before the first spend
                checked = True
                if confirm and not confirm(est := estimate(p)):
                    log.info("advance: stopped before spending ($%.2f declined)", est)
                    return sts
                _preflight(p, run.led, quick_cost(p))
            RUNNERS[sts[i].step](run)
            sts = status(p)
            # stop on failures instead of retrying (and re-billing) in the same call; an upstream step going stale is progress
            if sts[i].state not in ("done", "waiting") and all(s.state == "done" for s in sts[:i]):
                log.warning("advance: %s did not finish: %s", sts[i].step, sts[i].detail)
                return sts
    raise RuntimeError(f"advance did not settle after {MAX_PASSES} passes")
