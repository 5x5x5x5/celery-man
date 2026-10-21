"""Tastemaker web page: the app, the overview (status, modes, run) and the project file server. Decision pages: pages.py."""

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from celery_man.ledger import Ledger
from celery_man.pipeline import advance, estimate, status
from celery_man.project import DEFAULT_MODES, Project, write_json
from celery_man.web.pages import render, router

log = logging.getLogger(__name__)
MODES = ("tastemaker", "auto")


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

    @app.get("/")
    def overview(request: Request):
        p, runner = project, app.state.runner
        try:
            sts, est, err = status(p), estimate(p), ""
        except Exception as e:  # noqa: BLE001 - show a broken decision file instead of a 500
            sts, est, err = [], 0.0, f"{type(e).__name__}: {e}"
        spent = Ledger(p.ledger_path, p.cap_usd).spent_usd()
        return render(request, "overview.html", steps=sts, est=est, spent=spent, cap=p.cap_usd, modes=p.modes,
                      choices=MODES, running=runner.running, error=runner.error or err)  # fmt: skip

    @app.post("/modes")
    async def modes(request: Request):
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
