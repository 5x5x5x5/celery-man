"""Pipeline: hash-stamped decisions, per-step staleness, and advance in either mode."""

import logging
from dataclasses import dataclass

from celery_man import steps
from celery_man.generate import Generator
from celery_man.ledger import Ledger, cost
from celery_man.project import Project, read_json

log = logging.getLogger(__name__)

STEPS = ("timing", "brief", "shots", "keyframes", "takes", "assemble")
RUNNERS = {
    "timing": steps.run_timing,
    "brief": steps.run_brief,
    "shots": steps.run_shots,
    "keyframes": steps.run_keyframes,
    "takes": steps.run_takes,
    "assemble": steps.run_assemble,
}
MAX_PASSES = 20


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
    open_ = {sid: n for sid, n in needs.items() if n != "done"}
    detail = ", ".join(f"{sid} {n}" for sid, n in open_.items())
    if not open_:
        return StepState(step, "done")
    if p.modes[step] == "tastemaker" and set(open_.values()) <= wait_on:
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
    needs = {s.id: steps.take_plan(p, led, s, brief, kfs.get(s.id), picks.get(s.id)).need for s in shots}
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
    n_takes = sum(steps.take_plan(p, led, s, brief, kfs.get(s.id), picks.get(s.id)).missing for s in shots)
    return cost(n_kf, steps.EST_STILL_S, steps.STILL_GPU) + (cost(n_takes + 1, steps.EST_WALL_S, steps.TAKE_GPU) if n_takes else 0.0)


def advance(p: Project, gen: Generator, chat_text=None, chat_vision=None) -> list[StepState]:
    """Run steps in order until everything is done, a tastemaker decision is missing, or a step cannot finish."""
    run = steps.Run(p, gen, chat_text, chat_vision)
    with run.stack:
        sts = status(p)
        for _ in range(MAX_PASSES):
            i = next((i for i, s in enumerate(sts) if s.state != "done"), None)
            if i is None or sts[i].state == "waiting":
                return sts
            log.info("advance: %s (%s)", sts[i].step, sts[i].state)
            RUNNERS[sts[i].step](run)
            sts = status(p)
            # stop on failures instead of retrying (and re-billing) in the same call; an upstream step going stale is progress
            if sts[i].state not in ("done", "waiting") and all(s.state == "done" for s in sts[:i]):
                log.warning("advance: %s did not finish: %s", sts[i].step, sts[i].detail)
                return sts
    raise RuntimeError(f"advance did not settle after {MAX_PASSES} passes")
