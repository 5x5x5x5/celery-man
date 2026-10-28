"""The State the front end reads: one JSON snapshot of a project. Its field names are the TypeScript State, keep them identical."""

from dataclasses import asdict
from pathlib import Path

from celery_man import takes
from celery_man.cut import cut_plan
from celery_man.ledger import Ledger
from celery_man.pipeline import status
from celery_man.project import Brief, Project, Shot, read_json
from celery_man.renders import render_need
from celery_man.spend import prices, summary
from celery_man.steps import TAKE_MODEL, kf_need, windows
from celery_man.uploads import changed_since_upload
from celery_man.web.jobs import Jobs

PER_SHOT = ("shots", "keyframes", "takes")


def _project(p: Project) -> dict:
    c = p.config
    return {"id": p.root.resolve().name, "song": c["song"], "song_source": c["song_source"], "range": c["range"], "kinds": p.kinds,
            "cap_usd": c["cap_usd"], "modes": c["modes"], "shot_modes": p.shot_modes, "seed": c["seed"],
            "vision_flags": bool(c.get("vision_flags"))}  # fmt: skip


def _timing(p: Project) -> dict | None:
    t = read_json(p.timing_path)
    if t is None:
        return None
    wins = [{"id": f"s{i:02d}", "start": a, "end": b, "section": sec} for i, (a, b, sec) in enumerate(windows(p), 1)]
    return {"duration": t["duration"], "windows": wins}


def _takes(p: Project, led: Ledger, rows: list[dict], s: Shot, brief: Brief, kf: dict | None, pick: dict | None) -> dict:
    """takes: the landed clips of the shot's latest round (the ones a pick is made from)."""
    plan = takes.take_plan(p, led, s, brief, kf, pick)
    landed = {
        r["key"]: r
        for r in rows
        if r.get("shot") == s.id and r.get("model") == TAKE_MODEL and r.get("output") and Path(r["output"]).exists()
    }
    latest = max(((r["gen"], r["round"]) for r in landed.values()), default=None)
    shown = [{"key": k, "clip": f"takes/{k}.mp4", "sheet": f"sheets/{k}.jpg" if (p.sheets_dir / f"{k}.jpg").exists() else None, "seed": r["seed"]}
             for k, r in landed.items() if (r["gen"], r["round"]) == latest]  # fmt: skip
    return {"need": plan.need, "round": plan.round, "cap": takes.round_cap(p, s.id), "takes": shown, "pick": pick,
            "changed_since_upload": changed_since_upload(p, s, brief, pick)}  # fmt: skip


def _shot(p: Project, s: Shot, brief: Brief | None, led: Ledger, rows: list[dict], kfs: dict, picks: dict, ren: dict) -> dict:
    out = {
        "shot": asdict(s),
        "open": s.is_open,
        "modes": {k: p.mode(k, s.id) for k in PER_SHOT},
        "render": None,
        "keyframe": None,
        "takes": None,
    }
    if brief is None or s.is_open:
        return out
    if s.kind == "animation":
        e = ren.get(s.id) or {}
        out["render"] = {
            "need": render_need(p, s, brief.look, ren.get(s.id)),
            "clip": e.get("clip"),
            "poster": e.get("poster"),
            "error": e.get("error"),
        }
        return out
    kf, pick = kfs.get(s.id), picks.get(s.id)
    need = "done" if (pick or {}).get("uploaded") else kf_need(p, s, brief, kf)  # as status() counts an uploaded take
    out["keyframe"] = {"need": need, "entry": kf, "changed_since_upload": changed_since_upload(p, s, brief, kf)}
    out["takes"] = _takes(p, led, rows, s, brief, kf, pick)
    return out


def _candidates(p: Project) -> dict:
    d = p.root / "candidates"

    def each(kind: str) -> dict:
        return {f.stem: read_json(f) for f in sorted((d / kind).glob("*.json"))}

    return {"brief": read_json(d / "brief.json"), "shots": each("shots"), "keyframes": each("keyframes")}


def project_state(p: Project, jobs: Jobs) -> dict:
    brief, shots = p.load_brief(), p.load_shots()
    led, kfs, picks, ren = (
        Ledger(p.ledger_path),
        read_json(p.keyframes_path) or {},
        read_json(p.picks_path) or {},
        read_json(p.renders_path) or {},
    )
    rows = [r for r in led.rows() if r.get("kind") == "job"]
    return {
        "project": _project(p),
        "timing": _timing(p),
        "brief": None if brief is None else asdict(brief),
        "shots": [_shot(p, s, brief, led, rows, kfs, picks, ren) for s in shots or []],
        "steps": [{"step": s.step, "state": s.state, "detail": s.detail} for s in status(p)],
        "spend": summary(p),
        "prices": prices(p),
        "candidates": _candidates(p),
        "jobs": jobs.snapshot(),
        "cut": [] if shots is None else cut_plan(p),
    }
