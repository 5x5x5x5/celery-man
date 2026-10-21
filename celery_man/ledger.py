"""Append-only JSONL ledger of generation jobs: resume cache, audit trail, budget guard."""

import fcntl
import hashlib
import json
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

# USD per second from `modal billing rates` (2026-10-17). L4 includes its 88 GiB of RAM ($0.008/GiB/h), which is half its bill.
GPU_PRICE = {"H100": 0.001097, "L40S": 0.000542, "L4": (0.80 + 88 * 0.008) / 3600}
DEFAULT_CAP_USD = 25.0
FIXED_ALLOWANCE_USD = 3.0  # weight download plus FLUX time


def cost(n_jobs: int, est_wall_s: float, gpu: str) -> float:
    return n_jobs * est_wall_s * GPU_PRICE[gpu]


def job_key(shot_id: str, seed: int, prompt: str, keyframe: bytes) -> str:
    return f"{shot_id}-{seed}-{hashlib.sha256(prompt.encode() + keyframe).hexdigest()[:12]}"


class Ledger:
    def __init__(self, path: Path, cap_usd: float = DEFAULT_CAP_USD) -> None:
        self.path = Path(path)
        self.cap_usd = cap_usd

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Exclusive cross-process lock: serialises the can_afford check through the last append of a round."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def append(self, row: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # a kill mid-write can leave a truncated last line; rows() skips it and we start a fresh line here.
        # Our rows are pure ASCII (json.dumps ensure_ascii), so errors="replace" in rows() only matters for foreign bytes.
        lead = ""
        if self.path.exists() and self.path.stat().st_size:
            with self.path.open("rb") as r:
                r.seek(-1, os.SEEK_END)
                lead = "" if r.read(1) == b"\n" else "\n"
        with self.path.open("a") as f:
            f.write(lead + json.dumps(row) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("skipping unparseable ledger line in %s: %r", self.path, line[:60])
        return out

    def done(self, key: str) -> dict | None:
        """Latest job row for key whose output exists. Output paths resolve against the cwd; callers write absolute paths."""
        hits = [r for r in self.rows() if r.get("kind") == "job" and r["key"] == key and r.get("output") and Path(r["output"]).exists()]
        return hits[-1] if hits else None

    def spent_usd(self) -> float:
        jobs = (r for r in self.rows() if r.get("kind") == "job")
        return FIXED_ALLOWANCE_USD + sum(r["wall_s"] * GPU_PRICE[r["gpu"]] for r in jobs)

    def can_afford(self, cost_usd: float) -> bool:
        return self.spent_usd() + cost_usd <= self.cap_usd

    def rounds(self, shot_id: str) -> list[int]:
        """Judged-take rounds for a shot; keyframes, quick takes, overhead and reserve rows are not rounds."""
        skip = {"flux-schnell", "quick", "overhead", "reserve"}
        return sorted(
            {
                r["round"]
                for r in self.rows()
                if r.get("kind") == "job" and r.get("shot") == shot_id and r.get("model") not in skip and r.get("round", 0) >= 1
            }
        )

    def verdict(self, key: str, ok: bool, note: str) -> None:
        self.append({"kind": "verdict", "key": key, "ok": ok, "note": note, "ts": time.time()})

    def verdicts(self) -> dict[str, dict]:
        return {r["key"]: r for r in self.rows() if r.get("kind") == "verdict"}
