"""Trim generated clips to beat-snapped windows, hard-cut them, and lay them under the song."""

import itertools
import logging
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


class ClipTooShort(ValueError):
    pass


def probe_duration(path: Path | str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    return float(out)


def cut_points(cuts: list[dict]) -> list[float]:
    return [c["start"] for c in cuts] + [cuts[-1]["end"]]


def assemble(cuts: list[dict], song: Path, out: Path, fps: int = 24, w: int = 960, h: int = 544) -> Path:
    if not cuts:
        raise ValueError("no cuts to assemble")
    for i, (a, b) in enumerate(itertools.pairwise(cuts)):
        if abs(a["end"] - b["start"]) > 1e-6:
            raise ValueError(f"cuts must be contiguous and in order: cut {i} ends {a['end']} but cut {i + 1} starts {b['start']}")
    for i, c in enumerate(cuts):
        have, need = probe_duration(c["clip"]), c["in"] + (c["end"] - c["start"])
        if have < need:
            raise ClipTooShort(f"cut {i}: clip is {have:.2f}s, needs {need:.2f}s (in {c['in']} + window {c['end'] - c['start']:.2f})")
    vf = f"fps={fps},scale={w}:{h},setsar=1"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        segs = []
        for i, c in enumerate(cuts):
            seg = Path(tmp) / f"{i}.mp4"
            log.info("trim cut %d: %s from %.2fs for %.2fs", i, c["clip"], c["in"], c["end"] - c["start"])
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-y", "-i", c["clip"], "-ss", str(c["in"]), "-frames:v", str(round(c["end"] * fps) - round(c["start"] * fps)),
                 "-an", "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(seg)],
                check=True,
            )  # fmt: skip
            segs.append(seg)
        listing = Path(tmp) / "list.txt"
        listing.write_text("".join(f"file '{p}'\n" for p in segs))
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-ss", str(cuts[0]["start"]), "-to", str(cuts[-1]["end"]), "-i", str(song),
             "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest", str(out)],
            check=True,
        )  # fmt: skip
    return out


def contact_sheet(clip: Path | str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-vf", "fps=2,scale=480:-1,tile=6x2", "-frames:v", "1", str(dest)],
        check=True,
    )  # fmt: skip
    return dest
