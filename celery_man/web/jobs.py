"""One job runner per project: paid jobs one at a time in FIFO order holding run.lock, free jobs on a small pool."""

import functools
import logging
import queue
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from celery_man.pipeline import held_run_lock
from celery_man.project import Project

log = logging.getLogger(__name__)
FREE_WORKERS = 4
KEEP_ERRORS = 10


class Jobs:
    def __init__(self, p: Project) -> None:
        self.p = p
        self._lock = threading.Lock()
        self._todo: queue.Queue = queue.Queue()
        self._running: dict | None = None
        self._queued: list[dict] = []
        self._free: list[dict] = []
        self._errors: deque[dict] = deque(maxlen=KEEP_ERRORS)
        self._pool = ThreadPoolExecutor(max_workers=FREE_WORKERS, thread_name_prefix="free-job")
        threading.Thread(target=self._paid_worker, daemon=True, name="paid-job").start()

    def paid(self, name: str, fn: Callable[[], object]) -> str:
        job = self._job(name)
        with self._lock:
            self._queued.append(job)
        self._todo.put((job, fn))
        return job["id"]

    def free(self, name: str, fn: Callable[[], object]) -> str:
        job = self._job(name)
        with self._lock:
            self._free.append(job)

        def go() -> None:
            job["started"] = time.time()
            try:
                self._run(job, fn)
            finally:
                with self._lock:
                    self._free.remove(job)

        self._pool.submit(go)
        return job["id"]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": dict(self._running) if self._running else None,
                "queued": [dict(j) for j in self._queued],
                "free": [dict(j) for j in self._free],
                "errors": list(self._errors),
            }

    @staticmethod
    def _job(name: str) -> dict:
        return {"id": uuid.uuid4().hex[:12], "name": name, "started": None}

    def _paid_worker(self) -> None:
        while True:
            job, fn = self._todo.get()
            with self._lock:
                self._queued.remove(job)
                job["started"], self._running = time.time(), job
            try:
                self._run(job, functools.partial(self._locked, fn))
            finally:
                with self._lock:
                    self._running = None

    def _locked(self, fn: Callable[[], object]) -> None:
        with held_run_lock(self.p):  # BusyError while a CLI run holds it: recorded like any failure
            fn()

    def _run(self, job: dict, fn: Callable[[], object]) -> None:
        try:
            fn()
        except Exception as e:  # a failed job must never take its worker (or the server) down
            log.exception("job %s (%s) failed", job["name"], job["id"])
            with self._lock:
                self._errors.append({"id": job["id"], "name": job["name"], "error": f"{type(e).__name__}: {e}"})
