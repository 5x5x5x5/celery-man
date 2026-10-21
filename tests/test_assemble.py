import subprocess

import pytest

from celery_man.assemble import ClipTooShort, assemble, contact_sheet, cut_points, probe_duration


def make_clip(path, fps):
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate={fps}:duration=5",
         "-f", "lavfi", "-i", "sine=duration=5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )  # fmt: skip


def stream_types(path) -> list[str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    return out.split()


def frame_count(path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    return int(out)


def make_cuts(tmp_path, fps_list, edges):
    cuts = []
    for i, fps in enumerate(fps_list):
        clip = tmp_path / f"c{i}.mp4"
        make_clip(clip, fps)
        cuts.append({"clip": str(clip), "in": 0.5, "start": edges[i], "end": edges[i + 1]})
    return cuts


@pytest.fixture
def song(tmp_path):
    p = tmp_path / "song.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=duration=40", str(p)], check=True)
    return p


@pytest.fixture
def cuts_24(tmp_path):
    return make_cuts(tmp_path, [24] * 4, [8.0, 12.0, 16.0, 20.0, 24.0])


def test_cut_points_are_window_edges(cuts_24):
    assert cut_points(cuts_24) == [8.0, 12.0, 16.0, 20.0, 24.0]


def test_output_duration_matches_windows(cuts_24, song, tmp_path):
    out = assemble(cuts_24, song, tmp_path / "v.mp4", w=320, h=180)
    assert abs(probe_duration(out) - 16.0) <= 1 / 24


def test_only_song_audio(cuts_24, song, tmp_path):
    out = assemble(cuts_24, song, tmp_path / "v.mp4", w=320, h=180)
    assert stream_types(out) == ["video", "audio"]


def test_mixed_fps_clips(tmp_path, song):
    cuts = make_cuts(tmp_path, [24, 30, 24, 30], [8.0, 12.0, 16.0, 20.0, 24.0])
    out = assemble(cuts, song, tmp_path / "v.mp4", w=320, h=180)
    assert abs(probe_duration(out) - 16.0) <= 1 / 24


def test_uneven_windows(tmp_path, song):
    cuts = make_cuts(tmp_path, [24, 24], [8.0, 10.667, 14.667])
    assert cut_points(cuts) == [8.0, 10.667, 14.667]
    out = assemble(cuts, song, tmp_path / "v.mp4", w=320, h=180)
    assert frame_count(out) == round(14.667 * 24) - round(8.0 * 24)


def test_no_cumulative_drift(tmp_path, song):
    edges = [8.0 + i * 3.1111 for i in range(9)]
    cuts = make_cuts(tmp_path, [24] * 8, edges)
    out = assemble(cuts, song, tmp_path / "v.mp4", w=320, h=180)
    assert frame_count(out) == round(edges[-1] * 24) - round(edges[0] * 24)


def test_empty_cuts_rejected(song, tmp_path):
    with pytest.raises(ValueError, match="no cuts"):
        assemble([], song, tmp_path / "v.mp4")


def test_short_clip_fails_loudly(cuts_24, song, tmp_path):
    cuts_24[1]["in"] = 1.5  # 1.5 + 4.0 > 5.0
    with pytest.raises(ClipTooShort, match="cut 1"):
        assemble(cuts_24, song, tmp_path / "v.mp4")


def test_gap_between_cuts_rejected(cuts_24, song, tmp_path):
    cuts_24[2]["start"] = 16.5
    with pytest.raises(ValueError, match="contiguous"):
        assemble(cuts_24, song, tmp_path / "v.mp4")


def test_contact_sheet_written(cuts_24, tmp_path):
    sheet = contact_sheet(cuts_24[0]["clip"], tmp_path / "sheets" / "a.jpg")
    assert sheet.exists() and sheet.stat().st_size > 0


def test_clip_of_exactly_the_window_frames_fits(song, tmp_path):
    # an off-grid window: round(end*24) - round(start*24) frames is a hair shorter than end - start
    clip = tmp_path / "c.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24", "-frames:v", "37",
         "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )  # fmt: skip
    assemble([{"clip": str(clip), "in": 0, "start": 0.51, "end": 2.06}], song, tmp_path / "v.mp4")
