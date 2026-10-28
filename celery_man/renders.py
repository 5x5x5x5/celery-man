"""Render step: each animation shot to renders/<shot>-<key>.mp4, keyed by everything that changes its frames."""

import logging

from celery_man import animate, features, templates
from celery_man.animate import RenderJob, SceneError
from celery_man.generate import FPS, H, W
from celery_man.project import Brief, Project, Shot, read_json, sha12, write_json
from celery_man.steps import Run

log = logging.getLogger(__name__)


def scene_source(p: Project, shot: Shot) -> str:
    if "template" in shot.scene:
        return templates.source(shot.scene["template"])
    path = p.root / shot.scene["file"]
    if not path.is_file():
        raise SceneError(f"missing {shot.scene['file']}")
    return path.read_text()


def render_key(p: Project, shot: Shot, look: dict, feats: dict) -> str:
    try:
        source = scene_source(p, shot)
    except SceneError:
        source = None  # a missing file keys as its own state: its failure is kept until the file appears
    parts = [source, shot.scene["params"], look, shot.id, shot.start, shot.end, FPS, W, H, feats["song_sha"], animate.runtime_sha()]
    return sha12(parts)


def render_need(p: Project, shot: Shot, look: dict, entry: dict | None) -> str:
    """ "done" (fresh clip), "failed" (fresh key, deterministic error: not retried) or "gen"."""
    feats = read_json(p.features_path)
    if not entry or feats is None or entry["key"] != render_key(p, shot, look, feats):
        return "gen"
    if entry["error"]:
        return "failed"
    return "done" if (p.root / entry["clip"]).exists() else "gen"


def render_shot(run: Run, shot: Shot, brief: Brief) -> str:
    """Render one shot and record it in renders.json; returns "" or the error. A fresh entry is reused as is."""
    p = run.p
    entry = (read_json(p.renders_path) or {}).get(shot.id)
    if render_need(p, shot, brief.look, entry) != "gen":
        return entry["error"]
    feats = features.ensure(p)
    key = render_key(p, shot, brief.look, feats)
    entry = {"key": key, "clip": f"renders/{shot.id}-{key}.mp4", "poster": f"renders/{shot.id}-{key}.jpg", "error": ""}
    try:
        frames = features.frame_data(read_json(p.timing_path), feats, shot.start, shot.end, FPS)
        job = RenderJob(shot.id, scene_source(p, shot), shot.scene["params"], brief.look, frames, FPS, W, H, int(key[:8], 16))
        p.renders_dir.mkdir(exist_ok=True)
        run.renders().render(job, p.root / entry["clip"])
    except SceneError as e:
        log.error("render %s failed: %s", shot.id, e)
        entry["error"] = str(e) or "scene failed"
    renders = read_json(p.renders_path) or {}
    renders[shot.id] = entry
    write_json(p.renders_path, renders)  # each render is recorded as it lands, so a crash keeps the finished ones
    return entry["error"]


def run_render(run: Run) -> None:
    p = run.p
    features.ensure(p)
    brief, renders = p.load_brief(), read_json(p.renders_path) or {}
    for s in run.wanted(p.load_animation()):
        if render_need(p, s, brief.look, renders.get(s.id)) == "gen":
            render_shot(run, s, brief)
