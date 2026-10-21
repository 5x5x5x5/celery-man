"""Keyframe and clip generators: Modal (FLUX + LTX) for real runs, an ffmpeg/pillow fake for tests."""

import contextlib
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager as ContextManager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol

Result = dict | BaseException  # {"png"|"mp4": bytes, "wall_s": float, "gpu": str}, or the exception that job raised
W, H = 960, 544


@dataclass(frozen=True)
class StillJob:
    shot_id: str
    prompt: str
    seed: int


@dataclass(frozen=True)
class ClipJob:
    shot_id: str
    key: str
    prompt: str
    seed: int
    keyframe: bytes


class Generator(Protocol):
    """stills and clips yield one Result per job, in job order, as each finishes, so callers record work as it lands."""

    def session(self) -> ContextManager: ...
    def stills(self, jobs: list[StillJob]) -> Iterator[Result]: ...
    def clips(self, jobs: list[ClipJob]) -> Iterator[Result]: ...


class FakeGenerator:
    """No Modal, no network: solid-colour PNGs and ffmpeg test-pattern clips."""

    def __init__(self, fail: frozenset[str] | set[str] = frozenset()) -> None:
        self.fail = set(fail)

    def session(self) -> ContextManager:
        return contextlib.nullcontext()

    def _one(self, job: StillJob | ClipJob) -> Result:
        if job.shot_id in self.fail:
            return RuntimeError(f"fake failure for {job.shot_id}")
        if isinstance(job, StillJob):
            from PIL import Image

            buf = BytesIO()
            Image.new("RGB", (W, H), ((job.seed * 67) % 256, (job.seed * 131) % 256, (job.seed * 197) % 256)).save(buf, "PNG")
            return {"png": buf.getvalue(), "wall_s": 1.0, "gpu": "L40S"}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "clip.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    f"testsrc2=size={W}x{H}:rate=24",
                    "-frames:v",
                    "121",
                    "-pix_fmt",
                    "yuv420p",
                    str(out),
                ],
                check=True,
            )
            return {"mp4": out.read_bytes(), "wall_s": 1.0, "gpu": "L4"}

    def stills(self, jobs: list[StillJob]) -> Iterator[Result]:
        return (self._one(j) for j in jobs)

    def clips(self, jobs: list[ClipJob]) -> Iterator[Result]:
        return (self._one(j) for j in jobs)


class ModalGenerator:
    """Real generation. modal and gen_modal are imported lazily so importing this module stays fast."""

    @contextlib.contextmanager
    def session(self) -> Iterator[None]:
        import modal

        from celery_man.gen_modal import app

        with modal.enable_output(), app.run():
            yield

    def stills(self, jobs: list[StillJob]) -> Iterator[Result]:
        from celery_man.gen_modal import Keyframer

        # starmap gives no per-call timing for stills; with one container each still costs the time since the previous result
        t = time.monotonic()
        for r in Keyframer().still.starmap([(j.prompt, j.seed) for j in jobs], return_exceptions=True):
            now = time.monotonic()
            yield r if isinstance(r, BaseException) else {"png": r, "wall_s": now - t, "gpu": "L40S"}
            t = now

    def clips(self, jobs: list[ClipJob]) -> Iterator[Result]:
        from celery_man.gen_modal import ANIMATOR_GPU, Animator

        for r in Animator().clip.starmap([(j.keyframe, j.prompt, j.seed) for j in jobs], return_exceptions=True):
            yield r if isinstance(r, BaseException) else {"mp4": r["mp4"], "wall_s": r["wall_s"], "gpu": ANIMATOR_GPU}
