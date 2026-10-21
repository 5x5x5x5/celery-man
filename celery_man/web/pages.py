"""The four decision pages: brief, shots, keyframes, takes. Every save goes through the same save_*/write_json the auto steps use."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from celery_man.compose import TOKEN_LIMIT, PromptTooLong, compose, count_tokens
from celery_man.ledger import Ledger
from celery_man.project import STYLE_KEYS, Project, Shot, brief_from_dict, read_json, write_json
from celery_man.steps import IN_POINT, MAX_ROUND, TAKE_MODEL, brief_inputs, kf_need, shots_inputs, windows
from celery_man.takes import TakePlan, sheet, take_plan

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter()
BLANK_ROWS = 3


def render(request: Request, name: str, code: int = 200, **ctx):
    ctx = ctx | {"limit": TOKEN_LIMIT, "running": request.app.state.runner.running}
    return templates.TemplateResponse(request, name, ctx, status_code=code)


def _p(request: Request) -> Project:
    return request.app.state.p


def guard_run(request: Request) -> None:
    """Every decision write: a run rewrites these files from what it read at its start, so a save now would be lost."""
    if request.app.state.runner.running:
        raise HTTPException(409, "a run is going; save when it finishes")


def _editable(request: Request, step: str) -> None:
    guard_run(request)
    if _p(request).modes[step] != "tastemaker":
        raise HTTPException(409, f"{step} is set by auto; switch it to tastemaker to edit")


# --- brief -----------------------------------------------------------------------------------------


def _brief_page(request: Request, d: dict | None, error: str = "", code: int = 200):
    p = _p(request)
    d = d or {"logline": "", "style": dict.fromkeys(STYLE_KEYS, ""), "cast": [], "places": [], "avoid": []}
    blank = [{"name": "", "description": ""}] * BLANK_ROWS
    return render(request, "brief.html", code, b=d, cast=d["cast"] + blank, places=d["places"] + blank, style_keys=STYLE_KEYS,
                  auto=p.modes["brief"] == "auto", error=error)  # fmt: skip


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


def _shots_page(request: Request, shots: list[Shot] | None, code: int = 200):
    p = _p(request)
    brief, ctx = p.load_brief(), {"auto": p.modes["shots"] == "auto", "rows": [], "brief": None}
    if not p.timing_path.exists() or brief is None:
        return render(request, "shots.html", code, timing=p.timing_path.exists(), **ctx)
    if shots is None:
        old = {s.id: s for s in p.load_shots() or []}
        shots = [old.get(f"s{i:02d}", Shot(f"s{i:02d}", a, z, sec, "", [], None, "")) for i, (a, z, sec) in enumerate(windows(p), 1)]
    rows = [{"s": s} | _check(s, brief) for s in shots]
    return render(request, "shots.html", code, timing=True, **ctx | {"rows": rows, "brief": brief})


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
    shots = []
    for i, (a, z, sec) in enumerate(windows(p), 1):
        sid = f"s{i:02d}"
        place = form.get(f"place_{sid}") or None
        shots.append(Shot(sid, a, z, sec, form.get(f"action_{sid}", "").strip(), form.getlist(f"cast_{sid}"), place,
                          form.get(f"framing_{sid}", "").strip()))  # fmt: skip
    if any(_check(s, brief)["error"] for s in shots):
        return _shots_page(request, shots, 422)
    p.save_shots(shots, shots_inputs(p, brief))
    return RedirectResponse("/shots", 303)


# --- keyframes -------------------------------------------------------------------------------------


@router.get("/keyframes")
def keyframes_get(request: Request):
    p = _p(request)
    brief, shots, kfs = p.load_brief(), p.load_shots() or [], read_json(p.keyframes_path) or {}
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
    brief, shots = p.load_brief(), {s.id: s for s in p.load_shots() or []}
    if brief is None or sid not in shots:
        raise HTTPException(404)
    return take_plan(p, led, shots[sid], brief, (read_json(p.keyframes_path) or {}).get(sid), (read_json(p.picks_path) or {}).get(sid))


@router.get("/takes")
def takes_get(request: Request):
    p = _p(request)
    led, rows = Ledger(p.ledger_path, p.cap_usd), []
    picks, shots = read_json(p.picks_path) or {}, (p.load_shots() or []) if p.load_brief() else []
    for s in shots:
        plan = _plan(p, led, s.id)
        rnd, keys = _choices(led, s.id, plan)
        sheets = [(k, sh.relative_to(p.root) if (sh := sheet(p, k)) else None) for k in keys]
        rows.append({"s": s, "plan": plan, "round": rnd, "sheets": sheets, "pick": picks.get(s.id), "max_round": MAX_ROUND})
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
    plan = _plan(p, Ledger(p.ledger_path, p.cap_usd), sid)
    if plan.need != "judge" or plan.round >= MAX_ROUND or not reason:
        raise HTTPException(400, f"reject all needs a reason, and round {MAX_ROUND} must pick a take")
    picks = read_json(p.picks_path) or {}
    picks[sid] = {"rejected": True, "round": plan.round, "gen": plan.gen, "reason": reason}
    write_json(p.picks_path, picks)
    return RedirectResponse("/takes", 303)
