"""Auto custom scenes: the scene contract as prompt text, and the render / fix / template-fallback loop."""

import json
import logging
from dataclasses import replace

from celery_man import renders, templates
from celery_man.auto import JSON_ONLY, AutoError, ChatFn, _brief_text, ask_json
from celery_man.project import Brief, Shot
from celery_man.steps import Run

log = logging.getLogger(__name__)
TRIES = 3

CONTRACT = (
    "Scene contract: a scene is plain JavaScript that defines two globals, no imports. setup(env) runs once (may be async). "
    "draw(ctx, f, env) draws one frame on a 2D canvas context; the canvas is NOT cleared between frames, so paint the background "
    "yourself. env: params (object), look ({background: hex, colors: [hex], typeface: a font family for ctx.font}), width, height "
    "(960x544), fps (24), duration (seconds), random() (seeded, Math.random is the same). f: t (s since shot start), song_t (s in "
    "the song), progress (0..1), beat, bar (indexes), beat_phase, bar_phase (0..1), on_beat, on_downbeat (bool), level, low, mid, "
    "high (0..1 loudness and band energy). Date.now and performance.now follow song time. Never use new Date(), crypto, fetch or any "
    "network. Each frame must draw in under 2 s. Draw with env.look colours."
)


def catalogue_text() -> str:
    """One terse line per template: name, description, params with range/choices and default."""

    def param(k: str, s: dict) -> str:
        rng = {"choice": "|".join(s.get("choices", [])), "string": f"text<={s.get('max_len', '')}", "bool": "bool"}
        r = rng.get(s["type"], f"{s['type']} {s.get('min')}..{s.get('max')}")
        return f"{k} {r} ={json.dumps(s['default'])}"

    return "\n".join(
        f"{n}: {c['description']} params: {', '.join(param(k, s) for k, s in c['params'].items())}"
        for n, c in templates.catalogue().items()
    )


def fix_code(chat_fn: ChatFn, shot: Shot, brief: Brief, code: str, error: str) -> str:
    system = f'You fix a broken animation scene for one shot of a music video. {CONTRACT} {JSON_ONLY} Reply JSON: {{"code": str}}.'
    user = f"Brief: {_brief_text(brief)}\nShot: {shot.id}, {shot.action}\nCode:\n{code}\n\nRender error: {error}"

    def validate(d) -> str:
        if not isinstance(d, dict) or not isinstance(d.get("code"), str) or not d["code"].strip():
            raise ValueError("code must be a non-empty string")
        return d["code"]

    return ask_json(chat_fn, system, user, [], validate)


def pick_template(chat_fn: ChatFn, shot: Shot, brief: Brief) -> dict:
    system = (
        f'You pick a scene template for one animation shot of a music video. {JSON_ONLY} Reply JSON: {{"template": name, "params": {{}}}}.'
    )
    user = f"Brief: {_brief_text(brief)}\nShot: {shot.id}, {shot.action}\nTemplates:\n{catalogue_text()}"

    def validate(d) -> dict:
        if not isinstance(d, dict) or not isinstance(d.get("template"), str) or not isinstance(params := d.get("params") or {}, dict):
            raise TypeError("template must be a name and params an object")
        return {"template": d["template"], "params": templates.validate_params(d["template"], params)}

    return ask_json(chat_fn, system, user, [], validate)


def settle(chat_fn: ChatFn, run: Run, shot: Shot, brief: Brief, code: str) -> Shot:
    """Test-render the custom scene in full, feeding errors back up to TRIES renders; then fall back to a template."""
    path = run.p.root / shot.scene["file"]
    path.parent.mkdir(exist_ok=True)
    err = ""
    for n in range(TRIES):
        if n:
            code = fix_code(chat_fn, shot, brief, code, err)
        path.write_text(code)
        if not (err := renders.render_shot(run, shot, brief)):
            return replace(shot, scene={**shot.scene, "note": ""})
    try:
        scene = pick_template(chat_fn, shot, brief)
    except AutoError as e:
        log.warning("%s: template pick failed (%s); using %s", shot.id, e, templates.FALLBACK_TEMPLATE)
        scene = {"template": templates.FALLBACK_TEMPLATE, "params": {}}
    log.warning("%s: custom scene failed %d times, fell back to %s", shot.id, TRIES, scene["template"])
    return replace(shot, scene={**scene, "note": f"fallback: {err.splitlines()[0]}"})
