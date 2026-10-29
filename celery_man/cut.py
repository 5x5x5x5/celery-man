"""The cut plan: one item per window, naming what fills it. Assemble and the browser's rough cut both read this."""

from celery_man import takes
from celery_man.ledger import Ledger
from celery_man.project import Project, read_json
from celery_man.renders import render_need
from celery_man.steps import kf_need


def cut_plan(p: Project) -> list[dict]:
    """kind is take, render, still, card (decided, nothing to show yet) or blank (open window); src is relative to the project root."""
    brief, picks, kfs = p.load_brief(), read_json(p.picks_path) or {}, read_json(p.keyframes_path) or {}
    ren, led = read_json(p.renders_path) or {}, Ledger(p.ledger_path)
    items = []
    for s in p.load_shots():
        kind, src, at = "blank" if s.is_open else "card", None, 0
        if brief is not None and not s.is_open:
            if s.kind == "animation":
                if render_need(p, s, brief.look, ren.get(s.id)) == "done":
                    kind, src = "render", ren[s.id]["clip"]
            elif takes.take_plan(p, led, s, brief, kfs.get(s.id), picks.get(s.id)).need == "done":
                kind, src, at = "take", picks[s.id]["clip"], picks[s.id]["in"]
            elif kf_need(p, s, brief, kfs.get(s.id)) == "done":
                kind, src = "still", kfs[s.id]["path"]
        text = s.action if kind == "card" else ""
        items.append({"shot": s.id, "kind": kind, "src": src, "text": text, "in": at, "start": s.start, "end": s.end})
    return items
