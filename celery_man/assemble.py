"""Trim generated clips to beat-snapped windows, hard-cut them, and lay them under the song."""

import itertools
import logging
import subprocess
import tempfile
import textwrap
from pathlib import Path

log = logging.getLogger(__name__)

FONTS = Path(__file__).parent / "scenes" / "fonts"


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
        # what the trim below takes: whole frames, so a clip of exactly the window's frame count fits
        have, need = probe_duration(c["clip"]), c["in"] + (round(c["end"] * fps) - round(c["start"] * fps)) / fps
        if have < need - 1e-3:
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


def _filler(vf: str, seconds: float, out: Path, source: list[str], fps: int = 24) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp.mp4")
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", *source, "-vf", vf, "-frames:v", str(round(seconds * fps)), "-r", str(fps),
         "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp)],
        check=True,
    )  # fmt: skip
    tmp.replace(out)
    return out


def still_clip(png: Path, seconds: float, out: Path, fps: int = 24, w: int = 960, h: int = 544) -> Path:
    """A slow push-in from 1.0 to 1.06 over a still."""
    n = max(round(seconds * fps), 1)
    vf = f"scale={w * 2}:{h * 2},zoompan=z='1+0.06*on/{n}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={n}:s={w}x{h}:fps={fps},setsar=1"
    return _filler(vf, seconds, out, ["-i", str(png)], fps)


def card_clip(text: str, look: dict, seconds: float, out: Path, fps: int = 24, w: int = 960, h: int = 544) -> Path:
    """The look's background with the text centred in its first colour; empty text is a blank card."""
    source = ["-f", "lavfi", "-i", f"color=c={look['background']}:s={w}x{h}:r={fps}"]
    if not text.strip():
        return _filler("setsar=1", seconds, out, source, fps)
    with tempfile.TemporaryDirectory() as tmp:
        textfile = Path(tmp) / "text.txt"
        textfile.write_text(textwrap.fill(text, 36))  # a textfile, never inline: quotes and colons need no escaping
        vf = (f"drawtext=fontfile={FONTS / (look['typeface'] + '.ttf')}:textfile={textfile}:expansion=none:fontsize=34:"
              f"fontcolor={look['colors'][0]}:x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=10,setsar=1")  # fmt: skip
        return _filler(vf, seconds, out, source, fps)
