import io

from celery_man import cli
from celery_man.generate import FakeGenerator
from celery_man.pipeline import status
from celery_man.project import DEFAULT_MODES, Project, read_json
from tests.test_pipeline import make, text_chat, vision_chat


def patch(monkeypatch, gens):
    def make_gen():
        gens.append(FakeGenerator())
        return gens[-1]

    monkeypatch.setattr(cli, "make_generator", make_gen)
    monkeypatch.setattr(cli, "make_chats", lambda p: (text_chat, vision_chat()))


def test_new_then_status(tmp_path, song, caplog):
    d = tmp_path / "p"
    caplog.set_level("INFO")
    assert cli.main(["new", str(d), "--song", str(song), "--source", "tone"]) == 0
    assert (d / "project.json").exists()
    assert cli.main(["new", str(d), "--song", str(song)]) == 2  # never overwrite
    caplog.clear()
    assert cli.main(["status", str(d)]) == 0
    text = caplog.text
    assert "timing" in text and "assemble" in text and "spent $3.00 of $25.00 cap" in text


def test_modes_rejects_unknown_step(tmp_path, song):
    d = tmp_path / "p"
    Project.create(d, song, "t")
    assert cli.main(["modes", str(d), "bogus=auto"]) == 2
    assert cli.main(["modes", str(d), "brief=sideways"]) == 2
    assert cli.main(["modes", str(d), "brief=auto", "takes=auto"]) == 0
    assert read_json(d / "project.json")["modes"]["brief"] == "auto"


def test_run_without_yes_aborts_on_n(tmp_path, song, monkeypatch):
    gens = []
    patch(monkeypatch, gens)
    p = make(tmp_path, song, DEFAULT_MODES)
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    assert cli.main(["run", str(p.root)]) == 1
    assert not p.brief_path.exists()


def test_run_yes_all_auto_to_video(tmp_path, song, monkeypatch):
    gens = []
    patch(monkeypatch, gens)
    p = make(tmp_path, song)
    assert cli.main(["run", str(p.root), "--yes"]) == 0
    assert all(s.state == "done" for s in status(p))
    assert (p.out_dir / "final.mp4").exists() or list(p.out_dir.glob("*.mp4"))


def test_run_waiting_is_not_failure(tmp_path, song, monkeypatch):
    patch(monkeypatch, [])
    p = make(tmp_path, song)  # tastemaker defaults: stops at brief
    assert cli.main(["run", str(p.root), "--yes"]) == 0


def test_quick_waiting_exits_2(tmp_path, song, monkeypatch):
    patch(monkeypatch, [])
    p = make(tmp_path, song, DEFAULT_MODES)
    assert cli.main(["quick", str(p.root), "--yes"]) == 2
