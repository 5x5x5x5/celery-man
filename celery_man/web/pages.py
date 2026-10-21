"""The four decision pages: brief, shots, keyframes, takes. Every save goes through the same save_*/write_json the auto steps use."""

from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from celery_man import templates as scene_templates
from celery_man.compose import TOKEN_LIMIT, PromptTooLong, compose, count_tokens
from celery_man.ledger import Ledger
from celery_man.pipeline import run_busy
from celery_man.project import DEFAULT_LOOK, STYLE_KEYS, TYPEFACES, Project, Shot, brief_from_dict, read_json, shot_from_dict, write_json
from celery_man.steps import IN_POINT, MAX_ROUND, TAKE_MODEL, brief_inputs, kf_need, shots_inputs, windows
from celery_man.takes import TakePlan, sheet, take_plan

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter()
BLANK_ROWS = 3


def render(request: Request, name: str, code: int = 200, **ctx):
    ctx = ctx | {"limit": TOKEN_LIMIT, "running": _running(request)}
    return templates.TemplateResponse(request, name, ctx, status_code=code)


def _p(request: Request) -> Project:
    return request.app.state.p


def _running(request: Request) -> bool:
    """This server's run, or one from another process (the CLI) holding the project's run.lock."""
    return request.app.state.runner.running or run_busy(_p(request))


def guard_run(request: Request) -> None:
    """Every decision write: a run rewrites these files from what it read at its start, so a save now would be lost."""
    if _running(request):
        raise HTTPException(409, "a run is going; save when it finishes")


def _editable(request: Request, step: str) -> None:
    guard_run(request)
    if _p(request).modes[step] != "tastemaker":
        raise HTTPException(409, f"{step} is set by auto; switch it to tastemaker to edit")


# --- brief -----------------------------------------------------------------------------------------


def _brief_page(request: Request, d: dict | None, error: str = "", code: int = 200):
    p = _p(request)
    d = d or {"logline": "", "style": dict.fromkeys(STYLE_KEYS, ""), "cast": [], "places": [], "avoid": [], "look": DEFAULT_LOOK}
    blank = [{"name": "", "description": ""}] * BLANK_ROWS
    return render(request, "brief.html", code, b=d, cast=d["cast"] + blank, places=d["places"] + blank, style_keys=STYLE_KEYS,
                  look=d.get("look") or DEFAULT_LOOK, typefaces=TYPEFACES, auto=p.modes["brief"] == "auto", error=error)  # fmt: skip


def _named(form, kind: str) -> list[dict]:
    names, descs = form.getlist(f"{kind}_name"), form.getlist(f"{kind}_description")
    if len(names) != len(descs):
        raise HTTPException(422, f"{kind}: every row needs a name and a description field")
    rows = zip(names, descs, strict=True)
    return [{"name": n.strip(), "description": d.strip()} for n, d in rows if n.strip()]


@router.get("/brief")
def brief_get(request: Request):
    d = read_json(_p(request).brief_path)
    return _brief_page(request, d and {k: v for k, v in d.items() if k != "inputs"})


@router.post("/brief")
async def brief_post(request: Request):
    p = _p(request)
    _editable(request, "brief")
    form = await request.form()
    d = {
        "logline": form.get("logline", "").strip(),
        "style": {k: form.get(k, "").strip() for k in STYLE_KEYS},
        "cast": _named(form, "cast"),
        "places": _named(form, "place"),
        "avoid": [a.strip() for a in form.get("avoid", "").splitlines() if a.strip()],
        "look": {
            "background": form.get("background", DEFAULT_LOOK["background"]),
            "colors": [form[k] for i in range(1, 6) if f"unused_{i}" not in form and (k := f"color_{i}") in form]
            if "color_1" in form
            else DEFAULT_LOOK["colors"],
            "typeface": form.get("typeface", DEFAULT_LOOK["typeface"]),
        },
    }
    try:
        b = brief_from_dict(d)
    except ValueError as e:
        return _brief_page(request, d, str(e), 422)
    p.save_brief(b, brief_inputs(p))
    return RedirectResponse("/brief", 303)


# --- shots -----------------------------------------------------------------------------------------


def _check(shot: Shot, brief) -> dict:
    """The row's token count and any error compose raises, without saving anything."""
    try:
        return {"tokens": count_tokens(compose(shot, brief)), "error": ""}
    except PromptTooLong as e:
        return {"tokens": e.total, "error": str(e)}
    except ValueError as e:
        return {"tokens": None, "error": str(e)}


def _row(p: Project, s: Shot, brief) -> dict:
    """One shots-page row: footage rows get compose's token check; animation rows get the scene form and the last render."""
    if s.kind == "footage":
        return {"s": s} | _check(s, brief)
    r = {"s": s, "tokens": None, "error": "", "tpl": s.scene.get("template", "custom") if s.scene else "custom", "specs": {}, "code": ""}
    cat = scene_templates.catalogue()
    if r["tpl"] in cat:
        r["specs"] = cat[r["tpl"]]["params"]
    elif s.scene and "file" in s.scene and (f := p.root / s.scene["file"]).is_file():
        r["code"] = f.read_text()
    entry = (read_json(p.renders_path) or {}).get(s.id) or {}
    poster = entry.get("poster")
    return r | {"render_error": entry.get("error", ""), "poster": poster if poster and (p.root / poster).is_file() else ""}


def _default_scene() -> dict:
    return {"template": scene_templates.FALLBACK_TEMPLATE, "params": {}}


def _shots_page(request: Request, shots: list[Shot] | None, code: int = 200, errors: dict | None = None, codes: dict | None = None):
    p = _p(request)
    brief, ctx = p.load_brief(), {"auto": p.modes["shots"] == "auto", "rows": [], "brief": None}
    if not p.timing_path.exists() or brief is None:
        return render(request, "shots.html", code, timing=p.timing_path.exists(), **ctx)
    if shots is None:
        old = {s.id: s for s in p.load_shots() or []}
        kind = p.kinds[0] if len(p.kinds) == 1 else "footage"
        scene = None if kind == "footage" else _default_scene()
        shots = [
            old.get(f"s{i:02d}") or Shot(f"s{i:02d}", a, z, sec, "", [], None, "", kind, scene)
            for i, (a, z, sec) in enumerate(windows(p), 1)
        ]
    rows = [_row(p, s, brief) for s in shots]
    for r in rows:
        r["error"] = (errors or {}).get(r["s"].id) or r["error"]
        r["code"] = (codes or {}).get(r["s"].id, r["code"]) if r["s"].kind == "animation" else ""
    return render(request, "shots.html", code, timing=True, **ctx | {"rows": rows, "brief": brief, "both": len(p.kinds) > 1,
                  "templates": sorted(scene_templates.catalogue())})  # fmt: skip


def _param(spec: dict, raw):
    """The form value as the spec's type; a value that does not parse is passed on as the raw string so validation names it."""
    try:
        return {"integer": int, "number": float}.get(spec["type"], str)(raw)
    except ValueError:
        return raw


def _scene(form, sid: str, old: Shot | None) -> tuple[dict, str | None]:
    """The scene dict and (for a custom scene) the code to save. Params start at defaults unless the template is unchanged."""
    choice = form.get(f"scene_{sid}") or "custom"
    prev = old.scene if old and old.scene else {}
    note = {"note": prev["note"]} if "note" in prev else {}
    if choice == "custom":
        code = form.get(f"code_{sid}")
        if code is None:  # just switched to custom: start from the template that was there
            code = scene_templates.source(
                prev["template"] if prev.get("template") in scene_templates.catalogue() else scene_templates.FALLBACK_TEMPLATE
            )
        return {"file": f"scenes/{sid}.js", "params": prev["params"] if "file" in prev else {}} | note, code
    params = {}
    if prev.get("template") == choice:
        for k, spec in scene_templates.catalogue().get(choice, {}).get("params", {}).items():
            if spec["type"] == "bool":
                params[k] = f"param_{sid}_{k}" in form
            elif f"param_{sid}_{k}" in form:
                params[k] = _param(spec, form[f"param_{sid}_{k}"])
    return {"template": choice, "params": params} | note, None


@router.get("/shots")
def shots_get(request: Request):
    return _shots_page(request, None)


@router.post("/shots")
async def shots_post(request: Request):
    p = _p(request)
    _editable(request, "shots")
    brief, form = p.load_brief(), await request.form()
    if brief is None or not p.timing_path.exists():
        raise HTTPException(409, "save the brief and run once to analyse the song first")
    old = {s.id: s for s in p.load_shots() or []}
    shots, errors, codes = [], {}, {}
    for i, (a, z, sec) in enumerate(windows(p), 1):
        sid = f"s{i:02d}"
        kind = form.get(f"kind_{sid}") or (p.kinds[0] if len(p.kinds) == 1 else old[sid].kind if sid in old else "footage")
        place = form.get(f"place_{sid}") or None
        scene, code = (None, None) if kind == "footage" else _scene(form, sid, old.get(sid))
        s = Shot(sid, a, z, sec, form.get(f"action_{sid}", "").strip(), form.getlist(f"cast_{sid}"), place,
                 form.get(f"framing_{sid}", "").strip(), kind, scene)  # fmt: skip
        if code is not None:
            codes[sid] = code
        try:
            s = shot_from_dict(asdict(s))
            if code is not None and not code.strip():
                raise ValueError("custom scene: the code is empty")
        except ValueError as e:
            errors[sid] = f"{sid}: {e}"
        if kind == "footage" and (err := _check(s, brief)["error"]):
            errors[sid] = err
        shots.append(s)
    if errors:
        return _shots_page(request, shots, 422, errors, codes)
    p.scenes_dir.mkdir(exist_ok=True)
    for sid, code in codes.items():
        (p.scenes_dir / f"{sid}.js").write_text(code)
    p.save_shots(shots, shots_inputs(p, brief))
    return RedirectResponse("/shots", 303)


# --- keyframes -------------------------------------------------------------------------------------


@router.get("/keyframes")
def keyframes_get(request: Request):
    p = _p(request)
    brief, shots, kfs = p.load_brief(), p.load_footage() or [], read_json(p.keyframes_path) or {}
    rows = [{"s": s, "kf": kfs.get(s.id), "need": kf_need(p, s, brief, kfs.get(s.id))} for s in shots] if brief else []
    return render(request, "keyframes.html", rows=rows, auto=p.modes["keyframes"] == "auto")


@router.post("/keyframes/{sid}")
async def keyframes_post(request: Request, sid: str):
    p = _p(request)
    _editable(request, "keyframes")
    form, kfs = await request.form(), read_json(p.keyframes_path) or {}
    if sid not in kfs or form.get("verdict") not in ("approve", "reject"):
        raise HTTPException(404 if sid not in kfs else 400)
    kfs[sid] |= {"approved": form["verdict"] == "approve", "note": form.get("note", "").strip()}
    write_json(p.keyframes_path, kfs)
    return RedirectResponse("/keyframes", 303)


# --- takes -----------------------------------------------------------------------------------------


def _choices(led: Ledger, sid: str, plan: TakePlan) -> tuple[int, list[str]]:
    """(round, takes shown): the round being judged, or round 2 of the lineage once it is at the limit (R6)."""
    if plan.need == "judge":
        return plan.round, [k for k, _ in plan.keys if led.done(k)]
    if plan.need == "limit":
        rows = [r for r in led.rows() if r.get("kind") == "job" and r.get("shot") == sid and r.get("model") == TAKE_MODEL]
        return MAX_ROUND, [r["key"] for r in rows if r["gen"] == plan.gen and r["round"] == MAX_ROUND and led.done(r["key"])]
    return plan.round, []


def _plan(p: Project, led: Ledger, sid: str) -> TakePlan:
    brief, shots = p.load_brief(), {s.id: s for s in p.load_footage() or []}
    if brief is None or sid not in shots:
        raise HTTPException(404)
    return take_plan(p, led, shots[sid], brief, (read_json(p.keyframes_path) or {}).get(sid), (read_json(p.picks_path) or {}).get(sid))


@router.get("/takes")
def takes_get(request: Request):
    p = _p(request)
    led, rows = Ledger(p.ledger_path, p.cap_usd), []
    picks, shots = read_json(p.picks_path) or {}, (p.load_footage() or []) if p.load_brief() else []
    for s in shots:
        plan = _plan(p, led, s.id)
        rnd, keys = _choices(led, s.id, plan)
        sheets = [(k, sh.relative_to(p.root) if (sh := sheet(p, k)) else None) for k in keys]
        rows.append({"s": s, "plan": plan, "round": rnd, "sheets": sheets, "pick": picks.get(s.id), "max_round": MAX_ROUND,
                     "usable": any(sh for _, sh in sheets)})  # fmt: skip
    return render(request, "takes.html", rows=rows, auto=p.modes["takes"] == "auto")


@router.post("/takes/{sid}/pick")
async def takes_pick(request: Request, sid: str):
    p = _p(request)
    _editable(request, "takes")
    led, key = Ledger(p.ledger_path, p.cap_usd), (await request.form()).get("key")
    plan = _plan(p, led, sid)
    rnd, keys = _choices(led, sid, plan)
    if key not in keys or not sheet(p, key):  # a take without a contact sheet is unreadable: never pickable
        raise HTTPException(400, "not a take of this round")
    picks = read_json(p.picks_path) or {}
    picks[sid] = {"key": key, "clip": f"{p.takes_dir.name}/{key}.mp4", "in": IN_POINT, "round": rnd, "gen": plan.gen, "quick": False}
    write_json(p.picks_path, picks)
    return RedirectResponse("/takes", 303)


@router.post("/takes/{sid}/reject")
async def takes_reject(request: Request, sid: str):
    p = _p(request)
    _editable(request, "takes")
    reason = (await request.form()).get("reason", "").strip()
    led = Ledger(p.ledger_path, p.cap_usd)
    plan = _plan(p, led, sid)
    # the last round must pick, unless no take in it is readable: then the next run ships a quick take for the shot
    last_with_usable = plan.round >= MAX_ROUND and any(sheet(p, k) for k in _choices(led, sid, plan)[1])
    if plan.need != "judge" or last_with_usable or not reason:
        raise HTTPException(400, f"reject all needs a reason, and round {MAX_ROUND} must pick a take")
    picks = read_json(p.picks_path) or {}
    picks[sid] = {"rejected": True, "round": plan.round, "gen": plan.gen, "reason": reason}
    write_json(p.picks_path, picks)
    return RedirectResponse("/takes", 303)
