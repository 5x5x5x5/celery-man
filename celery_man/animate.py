"""Render Canvas2D scenes to mp4 in a sandboxed headless Chromium, one child process per shot."""

import base64
import dataclasses
import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

log = logging.getLogger(__name__)
SCENES = Path(__file__).parent / "scenes"
ORIGIN = "http://scene.invalid/"
FRAME_LIMIT_S = 2.0
STARTUP_S = 30.0


class SceneError(RuntimeError):
    pass


@dataclass(frozen=True)
class RenderJob:
    shot_id: str
    source: str
    params: dict
    look: dict
    frames: list[dict]
    fps: int
    width: int
    height: int
    seed: int


class Renderer(Protocol):
    def render(self, job: RenderJob, out: Path) -> Path: ...


def runtime_sha() -> str:
    h = hashlib.sha256()
    for p in [SCENES / "index.html", SCENES / "runtime.js", *sorted((SCENES / "fonts").glob("*.ttf"))]:
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def _encoder(out: Path, fps: int) -> subprocess.Popen:
    return subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", str(out)],
        stdin=subprocess.PIPE,
    )  # fmt: skip


def _serve(source: str):
    def handle(route) -> None:
        url = urlsplit(route.request.url)
        name = url.path.lstrip("/")
        if f"{url.scheme}://{url.netloc}/" != ORIGIN:
            return route.abort()
        if name in ("index.html", "runtime.js"):
            return route.fulfill(path=SCENES / name)
        if name == "scene.js":
            return route.fulfill(body=source, content_type="text/javascript")
        if name.startswith("fonts/") and "/" not in name[6:] and (SCENES / name).is_file():
            return route.fulfill(path=SCENES / name, content_type="font/ttf")
        return route.abort()

    return handle


def render_in_browser(job: RenderJob, out: Path, frame_limit_ms: float) -> Path:
    """Child side: draw every frame in Chromium, encode through a .part file, write the middle frame as poster."""
    from playwright.sync_api import Error, sync_playwright

    part = out.with_suffix(".part.mp4")
    ff = _encoder(part, job.fps)
    errors: list[str] = []

    def check(where: str) -> None:
        if errors:
            raise SceneError(f"{where}: {errors[0]}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            # route on the context, not the page, so popups (window.open, target=_blank) are sandboxed too
            context = browser.new_context(viewport={"width": job.width, "height": job.height}, service_workers="block")
            context.route("**/*", _serve(job.source))
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            payload = {k: getattr(job, k) for k in ("params", "look", "width", "height", "fps", "seed")}
            try:
                page.goto(ORIGIN + "index.html")
                page.evaluate("p => window.__init(p)", payload | {"duration": len(job.frames) / job.fps, "frame_limit_ms": frame_limit_ms})
            except Error as e:
                raise SceneError(f"setup: {e.message}") from e
            check("setup")
            for i, f in enumerate(job.frames):
                try:
                    url = page.evaluate("([i, f]) => window.__frame(i, f)", [i, f])
                except Error as e:
                    raise SceneError(f"frame {i}: {e.message}") from e
                check(f"frame {i}")
                jpg = base64.b64decode(url.split(",", 1)[1])
                ff.stdin.write(jpg)
                if i == len(job.frames) // 2:
                    out.with_suffix(".jpg").write_bytes(jpg)
            browser.close()
        ff.stdin.close()
        if ff.wait() != 0:
            raise SceneError("ffmpeg failed to encode the scene")
    except BaseException:
        ff.kill()
        part.unlink(missing_ok=True)
        raise
    os.replace(part, out)
    return out


class ChromiumRenderer:
    def render(self, job: RenderJob, out: Path) -> Path:
        timeout = STARTUP_S + len(job.frames) * FRAME_LIMIT_S
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "job.json"
            spec.write_text(json.dumps(dataclasses.asdict(job) | {"frame_limit_ms": FRAME_LIMIT_S * 1000}))
            # own session so a timeout can kill Chromium along with the child
            child = subprocess.Popen(
                [sys.executable, "-m", "celery_man.animate", str(spec), str(out)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, start_new_session=True,
            )  # fmt: skip
            try:
                _, err = child.communicate(timeout=timeout)
            except BaseException as e:  # timeout, Ctrl-C or anything else: the child's own session never sees our SIGINT
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                out.with_suffix(".part.mp4").unlink(missing_ok=True)
                if isinstance(e, subprocess.TimeoutExpired):
                    raise SceneError(f"scene did not finish in {timeout:.0f}s (stuck loop?)") from None
                raise
        if child.returncode != 0:
            raise SceneError("\n".join(err.strip().splitlines()[-20:]) or f"scene render exited {child.returncode}")
        return out


class FakeRenderer:
    """ffmpeg testsrc of the right size, rate and length; raises when the source contains `throw`."""

    def __init__(self) -> None:
        self.jobs: list[RenderJob] = []

    def render(self, job: RenderJob, out: Path) -> Path:
        self.jobs.append(job)
        if "throw" in job.source:
            raise SceneError(f"{job.shot_id}: scene threw")
        src = f"testsrc=size={job.width}x{job.height}:rate={job.fps}"
        for target, n in ((out, len(job.frames)), (out.with_suffix(".jpg"), 1)):
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src, "-frames:v", str(n), "-pix_fmt", "yuv420p", str(target)],
                check=True,
            )  # fmt: skip
        return out


def main(argv: list[str] | None = None) -> None:
    """Child entry: python -m celery_man.animate JOB.json OUT.mp4"""
    spec, out = argv if argv is not None else sys.argv[1:]
    d = json.loads(Path(spec).read_text())
    limit = d.pop("frame_limit_ms")
    try:
        render_in_browser(RenderJob(**d), Path(out), limit)
    except SceneError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
