"""Tastemaker-supplied keyframes and takes: validated by content at the boundary, converted to the pipeline size, never replaced."""

import hashlib
import io
import os
import subprocess
from dataclasses import asdict

from PIL import Image, ImageOps

from celery_man.assemble import probe_duration
from celery_man.compose import prompt_sha, prompt_sha_or_none
from celery_man.generate import FPS, H, W
from celery_man.project import Brief, Project, Shot, sha12
from celery_man.steps import merge_entry

MAX_IMAGE, MAX_CLIP = 20 * 2**20, 200 * 2**20


class UploadError(ValueError):
    """The message is shown to the user."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _shot_and_brief(p: Project, sid: str) -> tuple[Shot, Brief]:
    brief = p.load_brief()
    shot = next((s for s in p.load_footage() or [] if s.id == sid), None)
    if shot is None or brief is None:
        raise UploadError(f"there is no footage shot {sid} to attach this to")
    return shot, brief


def import_keyframe(p: Project, sid: str, data: bytes) -> dict:
    shot, brief = _shot_and_brief(p, sid)
    if not data or len(data) > MAX_IMAGE:
        raise UploadError("the image must be a PNG or JPEG of at most 20 MB")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ("PNG", "JPEG"):
                raise UploadError("the image must be a PNG or JPEG")
            out = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H))
    except UploadError:
        raise
    except Exception as e:
        raise UploadError("that file is not a readable PNG or JPEG image") from e
    p.keys_dir.mkdir(exist_ok=True)
    rel = f"keys/{sid}-{_digest(data)}.png"
    tmp = p.root / (rel + ".tmp")
    out.save(tmp, "PNG")
    os.replace(tmp, p.root / rel)
    entry = {"path": rel, "seed": 0, "prompt_sha": prompt_sha(shot, brief), "approved": True, "note": "uploaded", "uploaded": True}
    merge_entry(p, p.keyframes_path, sid, entry)
    return entry


def import_take(p: Project, sid: str, data: bytes, in_point: float = 0.0) -> dict:
    shot, brief = _shot_and_brief(p, sid)
    if not data or len(data) > MAX_CLIP:
        raise UploadError("the clip must be a video of at most 200 MB")
    key = f"upload-{_digest(data)}"
    p.takes_dir.mkdir(exist_ok=True)
    src, tmp = p.takes_dir / f"{key}.src.tmp", p.takes_dir / f"{key}.tmp.mp4"
    try:
        src.write_bytes(data)
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(src)],
                check=True, capture_output=True, text=True,
            ).stdout  # fmt: skip
            if "video" not in probe:
                raise UploadError("that file has no video")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-an", "-vf", f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "mp4", str(tmp)],
                check=True, capture_output=True,
            )  # fmt: skip
            have = probe_duration(tmp)
        except (subprocess.CalledProcessError, ValueError) as e:
            raise UploadError("that file is not a readable video") from e
        need = in_point + (shot.end - shot.start)
        if have < need - 0.05:  # one frame of rounding slack
            raise UploadError(f"the shot needs {need:.1f}s of video from the in-point, this clip has {have:.1f}s")
        os.replace(tmp, p.takes_dir / f"{key}.mp4")
    finally:
        src.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
    pick = {"key": key, "clip": f"takes/{key}.mp4", "in": in_point, "round": 0, "gen": 0, "quick": False, "uploaded": True,
            "shot_sha": sha12(asdict(shot)), "prompt_sha": prompt_sha(shot, brief)}  # fmt: skip
    merge_entry(p, p.picks_path, sid, pick)
    return pick


def changed_since_upload(p: Project, shot: Shot, brief: Brief, entry: dict | None) -> bool:
    """An upload is pinned, never discarded; this only tells the UI the shot's prompt moved on after it."""
    return bool(entry and entry.get("uploaded") and entry["prompt_sha"] != prompt_sha_or_none(shot, brief))
