"""Tastemaker web page: the app, the overview (status, modes, run) and the project file server. Decision pages: pages.py."""

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from celery_man.ledger import Ledger
from celery_man.pipeline import advance, estimate, status
from celery_man.project import DEFAULT_MODES, Project, write_json
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
            cap, modes = p.cap_usd, p.modes
            sts, est, spent, err = status(p), estimate(p), Ledger(p.ledger_path, cap).spent_usd(), ""
        except Exception as e:  # noqa: BLE001 - show a broken project file instead of a 500
            sts, est, spent, cap, modes, err = [], 0.0, 0.0, 0.0, {}, f"{type(e).__name__}: {e}"
        return render(request, "overview.html", steps=sts, est=est, spent=spent, cap=cap, modes=modes, choices=MODES,
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

    @app.post("/run")
    def run():
        if not app.state.runner.start(lambda: advance(project, gen_factory(), *chat_factory(project))):
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
