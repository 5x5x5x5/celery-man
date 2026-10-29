"""Every tastemaker decision and every paid action under /api/projects/{pid}. Paid routes re-check the price the UI showed."""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import asdict

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute

from celery_man import auto, features, suggest, templates, uploads
from celery_man.assemble import probe_duration
from celery_man.compose import compose
from celery_man.generate import FPS
from celery_man.ledger import Ledger
from celery_man.pipeline import advance, estimate
from celery_man.project import Project, Shot, brief_from_dict, read_json, shot_from_dict, update_json
from celery_man.spend import OverReserve, PriceChanged, check, prices
from celery_man.steps import Run, brief_inputs, kf_need, merge_entry, run_assemble, shots_inputs
from celery_man.takes import take_plan
from celery_man.web.preview import preview_html

log = logging.getLogger(__name__)
IDEAS_PER_WINDOW = 3


class _Route(APIRoute):
    """Bad input (ValueError, including PromptTooLong and UploadError, or an unknown id) is 422 {error}; a refused spend is 409."""

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def run(request: Request):
            try:
                return await handler(request)
            except PriceChanged as e:
                return JSONResponse({"price": e.price}, 409)
            except OverReserve as e:
                return JSONResponse({"reason": e.reason}, 409)
            except (ValueError, LookupError) as e:
                return JSONResponse({"error": str(e.args[0]) if e.args else type(e).__name__}, 422)

        return run


async def _body(request: Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("expected a JSON object")  # noqa: TRY004 - bad input is a 422, like every other
    return body


def _get(body: dict, key: str, t: type | tuple, default=...):
    if key not in body and default is not ...:
        return default
    v = body.get(key)
    if not isinstance(v, t) or (isinstance(v, bool) and t is not bool):
        raise ValueError(f"{key}: expected {getattr(t, '__name__', 'a number')}")
    return v


def _price(body: dict) -> float:
    return _get(body, "price", (int, float))


def _shot(p: Project, sid: str, kind: str | None = None) -> Shot:
    s = next((s for s in p.load_shots() or [] if s.id == sid), None)
    if s is None or (kind and s.kind != kind):
        raise LookupError(f"no {kind or ''} shot {sid}".replace("  ", " "))
    return s


def _brief(p: Project):
    if (b := p.load_brief()) is None:
        raise ValueError("there is no brief yet")
    return b


MAX_CODE = 200_000


def _code(body: dict) -> str | None:
    c = _get(body, "code", str, None)
    if c is not None and not 0 < len(c) <= MAX_CODE:
        raise ValueError(f"code: expected 1 to {MAX_CODE} characters")
    return c


def _save_shot(p: Project, sid: str, d, expect: Shot | None = None, code: str | None = None) -> bool:
    """A tastemaker's edit to one shot: same id and window, a kind the project makes, a prompt that fits.
    expect: write only while the shot on disk is still this one; returns whether it was written."""
    if code is not None:
        if not isinstance(d, dict) or d.get("kind") != "animation":
            raise ValueError("code: only animation shots have scene code")
        # content-addressed, so the render key changes with the code
        name = f"scenes/{sid}-{hashlib.sha256(code.encode()).hexdigest()[:8]}.js"
        d = d | {"scene": {"file": name, "params": (d.get("scene") or {}).get("params", {})}}
    new, now, brief = shot_from_dict(d), _shot(p, sid), _brief(p)
    if (new.id, new.start, new.end, new.section) != (now.id, now.start, now.end, now.section):
        raise ValueError(f"{sid}: the shot id and window cannot change")
    if new.kind not in p.kinds:
        raise ValueError(f"kind: this project makes {', '.join(p.kinds)}")
    if new.kind == "footage":
        compose(new, brief)  # PromptTooLong or an unknown name reaches the user
    stamp, wrote = shots_inputs(p, brief), []
    if code is not None:  # only after validation passed
        (p.root / new.scene["file"]).parent.mkdir(exist_ok=True)
        (p.root / new.scene["file"]).write_text(code)

    def put(x: dict) -> dict:
        if expect is not None and next(s for s in x["shots"] if s["id"] == sid) != asdict(expect):
            return x
        wrote.append(True)
        return x | {"inputs": stamp, "shots": [asdict(new) if s["id"] == sid else s for s in x["shots"]]}

    update_json(p, p.shots_path, put)
    return bool(wrote)


def _judged_plan(p: Project, s: Shot):
    """The round of takes on show, read as if nothing were picked yet so a pick or rejection can be changed."""
    plan = take_plan(p, Ledger(p.ledger_path, p.cap_usd), s, _brief(p), (read_json(p.keyframes_path) or {}).get(s.id), None)
    if plan.need != "judge":
        raise ValueError(f"{s.id}: there is no round of takes to judge")
    return plan


def make_router(get: Callable, gen_factory: Callable, chat_factory: Callable) -> APIRouter:
    """get(pid) -> (Project, Jobs); the factories are create_app's."""
    r = APIRouter(prefix="/api/projects/{pid}", route_class=_Route)

    def run(p: Project) -> Run:
        return Run(p, gen_factory(), *chat_factory(p))

    def queued(job: str) -> JSONResponse:
        return JSONResponse({"job": job}, 202)

    def paid(p: Project, j, name: str, shown: float, price: Callable[[], float], fn: Callable[[], object]) -> JSONResponse:
        """Checked at the click, and again when the job starts: jobs queued behind it may have spent meanwhile."""
        check(p, shown, price())

        def go() -> None:
            check(p, shown, price())  # refused here it is a job error, with nothing spent
            fn()

        return queued(j.paid(name, go))

    @r.get("/preview")
    def preview(pid: str, sid: str, candidate: str | None = None) -> HTMLResponse:
        p, _ = get(pid)
        shot, code = _shot(p, sid), None
        if candidate is not None:
            items = (read_json(p.root / "candidates" / "shots" / f"{sid}.json") or {}).get("items", [])
            item = next((i for i in items if i["id"] == candidate), None)
            if item is None:
                raise LookupError(f"no candidate {candidate} for {sid}")
            shot, code = shot_from_dict(item["shot"]), item.get("code")
        if not shot.scene:
            raise ValueError(f"{sid}: this is not an animation shot")
        if code is None:
            code = templates.source(shot.scene["template"]) if "template" in shot.scene else (p.root / shot.scene["file"]).read_text()
        return HTMLResponse(preview_html(code, shot.scene["params"], _brief(p).look, 1))

    @r.get("/frames")
    def frames(pid: str, start: float, end: float) -> list[dict]:
        p, _ = get(pid)
        return features.frame_data(read_json(p.timing_path), features.ensure(p), start, end, FPS)

    @r.put("/brief")
    async def put_brief(pid: str, request: Request) -> dict:
        p, _ = get(pid)
        p.save_brief(brief_from_dict(_get(await _body(request), "brief", dict)), brief_inputs(p))
        return {}

    @r.post("/suggest/brief")
    async def suggest_brief(pid: str, request: Request):
        p, j = get(pid)
        steer = _get(await _body(request), "steer", str, "")
        return queued(j.free("suggest brief", lambda: suggest.briefs(run(p), steer)))

    @r.post("/brief/use")
    async def use_brief(pid: str, request: Request) -> dict:
        p, _ = get(pid)
        suggest.use_brief(p, _get(await _body(request), "id", str))
        return {}

    @r.put("/shots/{sid}")
    async def put_shot(pid: str, sid: str, request: Request) -> dict:
        p, _ = get(pid)
        b = await _body(request)
        _save_shot(p, sid, _get(b, "shot", dict), code=_code(b))
        return {}

    @r.post("/preview")
    async def preview_code(pid: str, request: Request) -> HTMLResponse:
        """Unsaved scene text, for the code editor."""
        p, _ = get(pid)
        b = await _body(request)
        code = _code(b)
        if code is None:
            raise ValueError("code: required")
        return HTMLResponse(preview_html(code, _get(b, "params", dict, {}), _brief(p).look, 1))

    @r.post("/suggest/shots")
    async def suggest_shots(pid: str, request: Request):
        p, j = get(pid)
        b = await _body(request)
        sids, steer, shown = _get(b, "shots", list), _get(b, "steer", str, ""), _price(b)
        if not sids:
            raise ValueError("shots: pick at least one window")
        for sid in sids:
            _shot(p, sid)
        _brief(p)

        def go() -> None:
            ru = run(p)
            with ru.stack:  # closes the generator session the stills opened
                for sid in sids:
                    suggest.shots(ru, sid, steer)

        # every idea priced as a footage still
        return paid(p, j, f"suggest shots {', '.join(sids)}", shown, lambda: prices(p)["still"] * IDEAS_PER_WINDOW * len(sids), go)

    @r.post("/shots/{sid}/use")
    async def use_shot(pid: str, sid: str, request: Request) -> dict:
        p, _ = get(pid)
        item = suggest.shot_idea(p, sid, _get(await _body(request), "id", str))
        _save_shot(p, sid, item["shot"], code=item["code"])
        return {}

    @r.post("/suggest/keyframes/{sid}")
    async def suggest_keyframes(pid: str, sid: str, request: Request):
        p, j = get(pid)
        b = await _body(request)
        near, shown = _get(b, "near_seed", (int, type(None)), None), _price(b)
        compose(_shot(p, sid, "footage"), _brief(p))  # a prompt that is too long is refused before any spend

        def go() -> None:
            ru = run(p)
            with ru.stack:
                suggest.keyframes(ru, sid, near)

        return paid(p, j, f"suggest keyframes {sid}", shown, lambda: prices(p)["keyframe_set"], go)

    @r.post("/keyframes/{sid}/use")
    async def use_keyframe(pid: str, sid: str, request: Request) -> dict:
        p, _ = get(pid)
        suggest.use_keyframe(p, sid, _get(await _body(request), "id", str))
        return {}

    def judge_keyframe(p: Project, sid: str, approved: bool) -> dict:
        """The tastemaker's verdict on a generated still awaiting approval; a rejected one is regenerated by the next Continue."""
        s = _shot(p, sid, "footage")
        kf = (read_json(p.keyframes_path) or {}).get(sid)
        if kf_need(p, s, _brief(p), kf) != "judge":
            raise ValueError(f"{sid}: there is no still awaiting approval")
        merge_entry(p, p.keyframes_path, sid, kf | {"approved": approved})
        return {}

    @r.post("/keyframes/{sid}/approve")
    def approve_keyframe(pid: str, sid: str) -> dict:
        return judge_keyframe(get(pid)[0], sid, True)

    @r.post("/keyframes/{sid}/redo")
    def redo_keyframe(pid: str, sid: str) -> dict:
        return judge_keyframe(get(pid)[0], sid, False)

    @r.post("/keyframes/{sid}/none")
    async def no_keyframe(pid: str, sid: str, request: Request) -> dict:
        p, _ = get(pid)
        note = _get(await _body(request), "note", str)
        _shot(p, sid, "footage")
        suggest.reopen_note(p, sid, note)
        path = p.root / "candidates" / "keyframes" / f"{sid}.json"
        if path.exists():
            update_json(p, path, lambda d: d | {"items": []})
        return {}

    # uploads and the ffmpeg/ffprobe work run in FastAPI's threadpool (plain def, or run_in_threadpool), never on the event loop
    @r.post("/uploads/keyframe/{sid}")
    def upload_keyframe(pid: str, sid: str, file: UploadFile) -> dict:
        p, _ = get(pid)
        return uploads.import_keyframe(p, sid, file.file.read(uploads.MAX_IMAGE + 1))

    @r.post("/takes/{sid}/pick")
    async def pick_take(pid: str, sid: str, request: Request) -> dict:
        p, _ = get(pid)
        b = await _body(request)
        key, in_point = _get(b, "key", str), _get(b, "in", (int, float))

        def pick() -> dict:
            s = _shot(p, sid, "footage")
            plan = _judged_plan(p, s)
            clip = p.takes_dir / f"{key}.mp4"
            if key not in dict(plan.keys) or not clip.is_file():
                raise ValueError(f"{key}: not a take of {sid}'s current round")
            top = probe_duration(clip) - (s.end - s.start)
            if not 0 <= in_point <= top:
                raise ValueError(f"in: expected 0 to {max(top, 0):.2f}s for this take")
            entry = {
                "key": key,
                "clip": f"{p.takes_dir.name}/{key}.mp4",
                "in": in_point,
                "round": plan.round,
                "gen": plan.gen,
                "quick": False,
            }
            merge_entry(p, p.picks_path, sid, entry)
            return {}

        return await run_in_threadpool(pick)

    @r.post("/takes/{sid}/reject")
    async def reject_takes(pid: str, sid: str, request: Request):
        p, j = get(pid)
        b = await _body(request)
        reason, edited = _get(b, "reason", str), _get(b, "shot", (dict, type(None)), None)
        s = _shot(p, sid, "footage")
        plan = await run_in_threadpool(_judged_plan, p, s)
        if edited is not None:
            _save_shot(p, sid, edited)  # first: a bad edit is refused with nothing written
        merge_entry(p, p.picks_path, sid, {"rejected": True, "round": plan.round, "gen": plan.gen, "reason": reason})
        if edited is not None:
            return {}

        def rewrite() -> None:
            new = auto.rewrite_shot(chat_factory(p)[0], s, _brief(p), reason)
            if not _save_shot(p, sid, asdict(new), expect=s):
                log.info("%s: edited since the rejection; the edit is the rewrite", sid)

        return queued(j.free(f"rewrite {sid}", rewrite))

    @r.post("/uploads/take/{sid}")
    def upload_take(pid: str, sid: str, file: UploadFile, in_point: float = Form(0.0, alias="in")) -> dict:
        p, _ = get(pid)
        return uploads.import_take(p, sid, file.file.read(uploads.MAX_CLIP + 1), in_point)

    @r.post("/continue")
    async def continue_(pid: str, request: Request):
        p, j = get(pid)
        b = await _body(request)
        shown, only = _price(b), _get(b, "only", (list, type(None)), None)
        if not all(isinstance(x, str) for x in only or ()):
            raise ValueError("only: expected a list of shot ids")
        if unknown := set(only or ()) - {s.id for s in p.load_shots() or []}:
            raise ValueError(f"no shot {', '.join(sorted(map(str, unknown)))}")
        text, vision, ids = *chat_factory(p), set(only or ()) or None
        go = lambda: advance(p, gen_factory(), text, vision, confirm=lambda c: c <= shown + 0.01, only=ids)
        return await run_in_threadpool(paid, p, j, "continue", shown, lambda: estimate(p, ids), go)

    @r.post("/assemble")
    async def assemble(pid: str):
        p, j = get(pid)
        return queued(j.free("assemble", lambda: run_assemble(Run(p, gen_factory()))))

    return r
