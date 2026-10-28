"""Tastemaker JSON API over a folder of projects (or one project), a job runner per project, and the built front end."""

import logging
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from celery_man import templates
from celery_man.project import DEFAULT_MODES, KINDS, OPEN, Project, read_json, update_json
from celery_man.steps import Run, ensure_open_shots, run_timing
from celery_man.timing import TimingError, plan_windows
from celery_man.web.jobs import Jobs
from celery_man.web.routes import make_router
from celery_man.web.state import PER_SHOT, project_state

log = logging.getLogger(__name__)
MODES = ("tastemaker", "auto")
LOCAL_HOSTS = ("127.0.0.1", "localhost")
NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_SONG = 100 * 2**20
DIST = Path(__file__).parent / "dist"
NOT_BUILT = "front end not built: npm --prefix web ci && npm --prefix web run build"
PATCHABLE = {"range", "kinds", "seed", "vision_flags", "reset_shots"}


def _inside(root: Path, path: str) -> Path:
    """The file at root/path, or 404 when it is missing or resolves (through .. or a symlink) outside root."""
    root = root.resolve()
    full = (root / path).resolve()
    if not full.is_relative_to(root) or not full.is_file():
        raise HTTPException(404)
    return full


def _is_audio(path: Path) -> bool:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                             check=True, capture_output=True, text=True, timeout=60).stdout  # fmt: skip
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return "audio" in out


def _patch(p: Project, body: dict) -> dict:
    """The project.json changes a PATCH body asks for; 422 on anything malformed or a range with no windows."""
    if not isinstance(body, dict) or not set(body) <= PATCHABLE:
        raise HTTPException(422, f"PATCH takes any of {', '.join(sorted(PATCHABLE))}")
    new = {k: v for k, v in body.items() if k != "reset_shots"}
    if "range" in new:
        r = new["range"]
        if not (
            isinstance(r, list) and len(r) == 2 and all(x is None or (isinstance(x, int | float) and not isinstance(x, bool)) for x in r)
        ):
            raise HTTPException(422, "range: expected [start, end], each a number of seconds or null")
        if (timing := read_json(p.timing_path)) is not None:
            try:
                plan_windows(timing, *r)
            except TimingError as e:
                raise HTTPException(422, str(e)) from None
    if "kinds" in new and (not isinstance(new["kinds"], list) or not new["kinds"] or any(k not in KINDS for k in new["kinds"])):
        raise HTTPException(422, f"kinds: pick at least one of {', '.join(KINDS)}")
    if "seed" in new and not isinstance(new["seed"], str):
        raise HTTPException(422, "seed: expected text")
    if "vision_flags" in new and not isinstance(new["vision_flags"], bool):
        raise HTTPException(422, "vision_flags: expected true or false")
    if "reset_shots" in body and not isinstance(body["reset_shots"], bool):
        raise HTTPException(422, "reset_shots: expected true or false")
    return new


def create_app(root: Path, gen_factory: Callable, chat_factory: Callable) -> FastAPI:
    """root holds project.json (one project, id = folder name) or is a folder of project folders."""
    root = Path(root)
    app = FastAPI(title="celery-man")
    jobs: dict[str, Jobs] = {}
    jobs_lock = threading.Lock()
    app.state.jobs = jobs

    def single() -> bool:
        return (root / "project.json").is_file()

    def folders() -> dict[str, Path]:
        if single():
            return {root.resolve().name: root}
        return {d.name: d for d in sorted(root.iterdir()) if (d / "project.json").is_file()} if root.is_dir() else {}

    def get(pid: str) -> tuple[Project, Jobs]:
        path = folders().get(pid)
        if path is None:
            raise HTTPException(404, f"no project {pid}")
        p = Project(path)
        with jobs_lock:
            if pid not in jobs:
                jobs[pid] = Jobs(p)
            return p, jobs[pid]

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

    @app.get("/api/templates")
    def template_catalogue() -> dict:
        return templates.catalogue()

    @app.get("/api/projects")
    def list_projects() -> list[dict]:
        out = []
        for pid, path in folders().items():
            p = Project(path)
            out.append({"id": pid, "song": p.config["song"], "kinds": p.kinds})
        return out

    @app.post("/api/projects")
    async def new_project(name: str = Form(""), song: UploadFile | None = None, source: str = Form(""), kinds: str = Form("")) -> dict:
        if not NAME.match(name):
            raise HTTPException(422, "name: 1 to 64 letters, digits, - or _")
        picked = tuple(kinds.split(","))
        if not kinds or any(k not in KINDS for k in picked):
            raise HTTPException(422, f"kinds: a comma list of {', '.join(KINDS)}")
        if single():
            raise HTTPException(409, "this server holds one project; serve its parent folder to add more")
        folder = root / name
        if folder.exists():
            raise HTTPException(409, f"{name} already exists")
        if song is None:
            raise HTTPException(422, "song: an audio file is required")
        data = await song.read(MAX_SONG + 1)
        if not data or len(data) > MAX_SONG:
            raise HTTPException(422, "song: an audio file of at most 100 MB")
        suffix = Path(song.filename or "").suffix.lower()
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / ("song" + (suffix if re.fullmatch(r"\.[a-z0-9]{1,5}", suffix) else ""))
            tmp.write_bytes(data)
            if not _is_audio(tmp):
                raise HTTPException(422, "song: that file has no audio")
            try:
                folder.mkdir(parents=True)
            except FileExistsError:
                raise HTTPException(409, f"{name} already exists") from None
            try:
                p = Project.create(folder, tmp, source, picked)
            except Exception:
                shutil.rmtree(folder, ignore_errors=True)
                raise
        _, j = get(name)
        j.free("timing", lambda: run_timing(Run(p, gen_factory())))
        return {"id": name}

    @app.get("/api/projects/{pid}/state")
    def state(pid: str):
        p, j = get(pid)
        try:
            return project_state(p, j)
        except ValueError as e:  # a bad range or hand-edited file (TimingError is a ValueError): show it, not a bare 500
            return JSONResponse({"error": f"{type(e).__name__}: {e}"}, 422)

    @app.patch("/api/projects/{pid}")
    async def patch_project(pid: str, request: Request) -> dict:
        p, _ = get(pid)
        body = await request.json()
        new = _patch(p, body)
        cfg = p.config
        moved = any(k in new and new[k] != (p.kinds if k == "kinds" else cfg[k]) for k in ("range", "kinds"))
        decided = any(s.kind != OPEN for s in p.load_shots() or [])
        if moved and decided and not body.get("reset_shots"):
            raise HTTPException(409, "decided shots would reopen: send reset_shots to confirm")
        update_json(p, p.root / "project.json", lambda c: c | new)
        if p.timing_path.exists():
            ensure_open_shots(p, reset=bool(body.get("reset_shots")))
        return {}

    @app.put("/api/projects/{pid}/shot-modes")
    async def shot_modes(pid: str, request: Request) -> dict:
        p, _ = get(pid)
        body = await request.json()
        sids, step, mode = body.get("shots"), body.get("step"), body.get("mode")
        if not isinstance(sids, list) or mode not in MODES or step not in (PER_SHOT if sids else DEFAULT_MODES):
            raise HTTPException(
                422, f"expected shots (a list of ids), step ({'/'.join(PER_SHOT)}, or brief with no shots) and mode ({'/'.join(MODES)})"
            )
        if unknown := set(sids) - {s.id for s in p.load_shots() or []}:
            raise HTTPException(422, f"no shot {', '.join(sorted(unknown))}")

        def put(c: dict) -> dict:
            if not sids:
                return c | {"modes": c["modes"] | {step: mode}}
            sm = c.get("shot_modes", {})
            return c | {"shot_modes": sm | {sid: sm.get(sid, {}) | {step: mode} for sid in sids}}

        update_json(p, p.root / "project.json", put)
        return {}

    app.include_router(make_router(get, gen_factory, chat_factory))

    @app.get("/api/projects/{pid}/files/{path:path}")
    def files(pid: str, path: str):
        return FileResponse(_inside(get(pid)[0].root, path))

    @app.get("/{path:path}")
    def front_end(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        if not (DIST / "index.html").is_file():
            return PlainTextResponse(NOT_BUILT, 503)
        return FileResponse(_inside(DIST, path or "index.html"))

    return app
