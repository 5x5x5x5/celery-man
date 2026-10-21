"""Tastemaker web page: the app, the overview (status, modes, run) and the project file server. Decision pages: pages.py."""

import logging
import math
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from celery_man.ledger import Ledger
from celery_man.pipeline import advance, estimate, run_busy, status
from celery_man.project import DEFAULT_MODES, KINDS, Project, write_json
from celery_man.web.pages import guard_run, render, router

log = logging.getLogger(__name__)
MODES = ("tastemaker", "auto")
LOCAL_HOSTS = ("127.0.0.1", "localhost")


class Runner:
    """One advance at a time, in a background thread; the last error is kept for the overview."""

    def __init__(self) -> None:
        self.lock, self.thread, self.error = threading.Lock(), None, ""

    @property
    def running(self) -> bool:
        return self.lock.locked()

    def start(self, work: Callable[[], object]) -> bool:
        if not self.lock.acquire(blocking=False):
            return False
        self.error = ""

        def go() -> None:
            try:
                work()
            except Exception as e:  # a failed run must never take the server down
                log.exception("run failed")
                self.error = f"{type(e).__name__}: {e}"
            finally:
                self.lock.release()

        self.thread = threading.Thread(target=go, daemon=True)
        self.thread.start()
        return True


def create_app(project: Project, gen_factory: Callable, chat_factory: Callable) -> FastAPI:
    app = FastAPI(title="celery-man")
    app.state.p, app.state.runner = project, Runner()
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    app.include_router(router)

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        """Any web page can make the browser POST to localhost (and start paid runs): accept only our own pages."""
        host = request.headers.get("host", "")
        if urlsplit(f"//{host}").hostname not in LOCAL_HOSTS:
            return PlainTextResponse("unexpected Host", 400)
        src = request.headers.get("origin") or request.headers.get("referer")
        if request.method == "POST" and src is not None and urlsplit(src).netloc != host:
            return PlainTextResponse("cross-site request refused", 403)
        return await call_next(request)

    @app.get("/")
    def overview(request: Request):
        p, runner = project, app.state.runner
        try:
            cap, modes, allowed = p.cap_usd, p.modes, p.kinds
            est = estimate(p) if p.shots_path.exists() else None  # None: known only once the shots are written
            sts, spent, err = status(p), Ledger(p.ledger_path, cap).spent_usd(), ""
        except Exception as e:  # noqa: BLE001 - show a broken project file instead of a 500
            sts, est, spent, cap, modes, allowed, err = [], 0.0, 0.0, 0.0, {}, [], f"{type(e).__name__}: {e}"
        return render(request, "overview.html", steps=sts, est=est, spent=spent, cap=cap, modes=modes, choices=MODES, kinds=KINDS, allowed=allowed,
                      error=runner.error or err)  # fmt: skip

    @app.post("/modes")
    async def modes(request: Request):
        guard_run(request)
        form = await request.form()
        new = {k: form[k] for k in DEFAULT_MODES if k in form}
        if any(v not in MODES for v in new.values()):
            raise HTTPException(400, f"modes are {', '.join(MODES)}")
        write_json(project.root / "project.json", project.config | {"modes": project.modes | new})
        return RedirectResponse("/", 303)

    @app.post("/kinds")
    async def kinds(request: Request):
        guard_run(request)
        picked = [k for k in KINDS if k in (await request.form()).getlist("kinds")]
        if not picked:
            raise HTTPException(422, f"pick at least one kind of shot: {', '.join(KINDS)}")
        write_json(project.root / "project.json", project.config | {"kinds": picked})
        return RedirectResponse("/", 303)

    @app.post("/run")
    async def run(request: Request):
        raw, runner = (await request.form()).get("shown", ""), app.state.runner
        try:
            shown = float(raw) if raw else math.nan  # nan: no estimate was shown, so the run stops at the spend gate
        except ValueError:
            raise HTTPException(400, "shown must be a number") from None
        if math.isinf(shown):
            raise HTTPException(400, "shown must be finite")

        def confirm(cost: float) -> bool:
            """The spend gate: go on only within a cent of the estimate the page showed when Run was pressed."""
            if cost <= shown + 0.01:
                return True
            runner.error = f"stopped before spending: the estimate is now ${cost:.2f}; run again to accept it"
            return False

        if run_busy(project) or not runner.start(lambda: advance(project, gen_factory(), *chat_factory(project), confirm=confirm)):
            raise HTTPException(409, "a run is already going")
        return RedirectResponse("/", 303)

    @app.get("/files/{path:path}")
    def files(path: str):
        root = project.root.resolve()
        full = (root / path).resolve()
        if not full.is_relative_to(root) or not full.is_file():
            raise HTTPException(404)
        return FileResponse(full)

    return app
