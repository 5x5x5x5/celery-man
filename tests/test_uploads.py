import io
import subprocess
from dataclasses import replace

import pytest
from PIL import Image

from celery_man.assemble import probe_duration
from celery_man.ledger import Ledger
from celery_man.pipeline import status
from celery_man.project import Shot, brief_from_dict, read_json
from celery_man.steps import kf_need, write_provenance
from celery_man.takes import take_plan
from celery_man.uploads import UploadError, changed_since_upload, import_keyframe, import_take
from tests.test_pipeline import BRIEF, make


def png_bytes(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def mp4_bytes(seconds: float, tmp_path) -> bytes:
    out = tmp_path / "in.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={seconds}:size=320x240:rate=24",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
    )
    return out.read_bytes()


@pytest.fixture
def proj(tmp_path, song):
    p = make(tmp_path, song)
    p.save_brief(brief_from_dict(BRIEF), "x")
    shot = Shot("s01", 0.0, 4.0, "1", "a person walks", ["A"], "P", "wide shot")
    p.save_shots([shot], "x")
    return p, shot


def test_keyframe_upload_pinned_through_brief_edit(proj):
    p, shot = proj
    import_keyframe(p, "s01", png_bytes(1200, 800))
    b = p.load_brief()
    p.save_brief(replace(b, style={**b.style, "palette": "red"}), "x")
    e = read_json(p.keyframes_path)["s01"]
    assert kf_need(p, shot, p.load_brief(), e) == "done"
    assert changed_since_upload(p, shot, p.load_brief(), e)


def test_keyframe_upload_is_960x544(proj):
    p, _ = proj
    e = import_keyframe(p, "s01", png_bytes(500, 500))
    assert Image.open(p.root / e["path"]).size == (960, 544)


def test_keyframe_upload_follows_exif_orientation(proj):
    """Stored landscape, left half red and right half blue; EXIF says turn it upright: red on top once turned."""
    p, _ = proj
    im = Image.new("RGB", (960, 544), (255, 0, 0))
    im.paste((0, 0, 255), (480, 0, 960, 544))
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 degrees clockwise to view
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    out = Image.open(p.root / import_keyframe(p, "s01", buf.getvalue())["path"]).convert("RGB")
    left, right = out.getpixel((10, 10)), out.getpixel((950, 10))
    assert max(abs(a - b) for a, b in zip(left, right)) < 40  # one colour across the top row, not split left and right


@pytest.mark.parametrize("data", [b"", b"not an image", b"\x89PNG\r\n\x1a\n" + b"0" * 10])
def test_bad_keyframe_upload_writes_nothing(proj, data):
    p, _ = proj
    with pytest.raises(UploadError):
        import_keyframe(p, "s01", data)
    assert not p.keyframes_path.exists()
    if p.keys_dir.exists():
        assert not list(p.keys_dir.glob("*"))


def test_take_upload_without_keyframe_is_done(proj, tmp_path):
    p, shot = proj
    pick = import_take(p, "s01", mp4_bytes(5, tmp_path))
    assert abs(probe_duration(p.root / pick["clip"]) - 5) < 0.2
    plan = take_plan(p, Ledger(p.ledger_path), shot, p.load_brief(), None, read_json(p.picks_path)["s01"])
    assert plan.need == "done"
    assert {s.step: s.state for s in status(p)}["keyframes"] == "done"
    write_provenance(p)
    assert "supplied by the tastemaker" in (p.root / "PROVENANCE.md").read_text().lower()


def test_short_take_upload_refused(proj, tmp_path):
    p, _ = proj
    with pytest.raises(UploadError, match="needs"):
        import_take(p, "s01", mp4_bytes(1, tmp_path), in_point=0.5)
    assert not p.picks_path.exists()
    assert not list(p.takes_dir.glob("*"))


def test_non_video_take_refused(proj):
    p, _ = proj
    with pytest.raises(UploadError):
        import_take(p, "s01", b"not a video")
    assert not p.picks_path.exists()


def test_unknown_shot_upload_writes_nothing(proj, tmp_path):
    p, _ = proj
    with pytest.raises(UploadError):
        import_keyframe(p, "s99", png_bytes(100, 100))
    with pytest.raises(UploadError):
        import_take(p, "s99", mp4_bytes(5, tmp_path))
    assert not p.keyframes_path.exists() and not p.picks_path.exists()
    for d in (p.keys_dir, p.takes_dir):
        assert not d.exists() or not list(d.glob("*"))
