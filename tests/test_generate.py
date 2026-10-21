import json
import subprocess
import sys

from celery_man.generate import ClipJob, FakeGenerator, StillJob


def _probe(path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=width,height,nb_read_frames,r_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return json.loads(out)["streams"][0]


def test_fake_clip_is_5_04s_960x544(tmp_path):
    gen = FakeGenerator()
    with gen.session():
        [res] = gen.clips([ClipJob("s1", "k1", "a person walks", 1, b"")])
    assert res["gpu"] == "L4" and res["wall_s"] == 1.0
    (tmp_path / "c.mp4").write_bytes(res["mp4"])
    s = _probe(tmp_path / "c.mp4")
    assert (s["width"], s["height"], s["nb_read_frames"], s["r_frame_rate"]) == (960, 544, "121", "24/1")


def test_fake_failure_returned_not_raised():
    gen = FakeGenerator(fail={"bad"})
    out = gen.stills([StillJob("ok", "a person walks", 1), StillJob("bad", "a person walks", 2)])
    assert out[0]["gpu"] == "L40S" and out[0]["png"].startswith(b"\x89PNG")
    assert isinstance(out[1], RuntimeError)
    assert isinstance(gen.clips([ClipJob("bad", "k", "p", 1, b"")])[0], RuntimeError)


def test_results_in_job_order():
    gen = FakeGenerator(fail={"b"})
    out = gen.stills([StillJob(i, "a person walks", n) for n, i in enumerate("abc")])
    assert [isinstance(r, BaseException) for r in out] == [False, True, False]
    assert out[0]["png"] != out[2]["png"]  # colour comes from the seed


def test_import_is_lazy():
    code = "import sys, celery_man.generate; sys.exit('modal' in sys.modules or 'celery_man.gen_modal' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
