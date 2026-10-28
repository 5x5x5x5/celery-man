import io

from celery_man import cli
from celery_man.ledger import Ledger
from celery_man.pipeline import estimate, run_lock, status
from celery_man.project import DEFAULT_MODES, Project, read_json, write_json
from tests.test_pipeline import CountingGen, make, text_chat, vision_chat


def patch(monkeypatch, gens):
    def make_gen():
        gens.append(CountingGen())
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


def no_spend(p, gens) -> bool:
    jobs = [r for r in Ledger(p.ledger_path).rows() if r.get("kind") == "job"]
    return not jobs and all(g.stills_n == g.clips_n == [] for g in gens)


def test_run_without_yes_aborts_on_n(tmp_path, song, monkeypatch, caplog):
    gens = []
    patch(monkeypatch, gens)
    p = make(tmp_path, song)
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    caplog.set_level("INFO")
    assert cli.main(["run", str(p.root)]) == 1
    assert p.shots_path.exists() and no_spend(p, gens)  # asked at the spend gate, after the brief and shots exist
    assert f"estimate ${estimate(p):.2f}" in caplog.text and estimate(p) > 0


def test_run_eof_at_prompt_declines_and_exits_2(tmp_path, song, monkeypatch):
    gens = []
    patch(monkeypatch, gens)
    p = make(tmp_path, song)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert cli.main(["run", str(p.root)]) == 2
    assert no_spend(p, gens)


def test_quick_declined_exits_1(tmp_path, song, monkeypatch):
    gens = []
    patch(monkeypatch, gens)
    p = make(tmp_path, song)
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    assert cli.main(["quick", str(p.root)]) == 1
    assert no_spend(p, gens)


def test_busy_project_exits_2(tmp_path, song, monkeypatch):
    patch(monkeypatch, [])
    p = make(tmp_path, song)
    with run_lock(p):
        assert cli.main(["run", str(p.root), "--yes"]) == 2


def test_run_yes_all_auto_to_video(tmp_path, song, monkeypatch):
    gens = []
    patch(monkeypatch, gens)
    p = make(tmp_path, song)
    assert cli.main(["run", str(p.root), "--yes"]) == 0
    assert all(s.state == "done" for s in status(p))
    assert (p.out_dir / "video.mp4").exists() and (p.out_dir / "report.md").exists()


def test_run_waiting_is_not_failure(tmp_path, song, monkeypatch):
    patch(monkeypatch, [])
    p = make(tmp_path, song)  # tastemaker defaults: stops at brief
    assert cli.main(["run", str(p.root), "--yes"]) == 0


def test_quick_waiting_exits_2(tmp_path, song, monkeypatch):
    patch(monkeypatch, [])
    p = make(tmp_path, song, DEFAULT_MODES)
    assert cli.main(["quick", str(p.root), "--yes"]) == 2


def test_missing_dir_and_bad_shots_exit_2(tmp_path, song, caplog):
    assert cli.main(["status", str(tmp_path / "nope")]) == 2
    assert str(tmp_path / "nope") in caplog.text
    p = make(tmp_path, song)
    write_json(p.shots_path, {"inputs": "x"})
    assert cli.main(["status", str(p.root)]) == 2


def test_new_kinds(tmp_path, song):
    assert cli.main(["new", str(tmp_path / "a"), "--song", str(song), "--kinds", "footage,animation"]) == 0
    assert Project(tmp_path / "a").kinds == ["footage", "animation"]
    assert cli.main(["new", str(tmp_path / "b"), "--song", str(song), "--kinds", "cartoon"]) == 2
    assert not (tmp_path / "b").exists()


def test_cli_modes_per_shot(tmp_path, song):
    d = tmp_path / "p"
    Project.create(d, song, "tone")
    assert cli.main(["modes", str(d), "keyframes=auto", "s05.takes=auto"]) == 0
    assert Project(d).mode("keyframes") == "auto" and Project(d).mode("takes", "s05") == "auto"
    assert cli.main(["modes", str(d), "s05.brief=auto"]) == 2


def test_serve_loads_the_tokenizer_before_serving(tmp_path, monkeypatch):
    """The first compose() would otherwise fetch the tokenizer inside a request, freezing the event loop."""
    import uvicorn

    from celery_man import compose

    calls = []
    monkeypatch.setattr(compose, "count_tokens", lambda text: calls.append("tokenizer") or 1)
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: calls.append(("serve", host)))
    assert cli.main(["serve", str(tmp_path)]) == 0
    assert calls == ["tokenizer", ("serve", "127.0.0.1")]
