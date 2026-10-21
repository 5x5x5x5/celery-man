import fcntl

import pytest

from celery_man.ledger import DEFAULT_CAP_USD, FIXED_ALLOWANCE_USD, GPU_PRICE, Ledger, cost, job_key
from tests.helpers import job_row


def test_key_changes_with_prompt_or_keyframe():
    a = job_key("s1", 7, "p", b"k")
    assert a == job_key("s1", 7, "p", b"k")
    assert a != job_key("s1", 7, "p2", b"k") and a != job_key("s1", 7, "p", b"k2")


def test_done_requires_output_file(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.append(job_row(key="x", output=str(tmp_path / "missing.mp4")))
    assert led.done("x") is None
    (tmp_path / "ok.mp4").write_bytes(b"1")
    led.append(job_row(key="x", output=str(tmp_path / "ok.mp4")))
    assert led.done("x")["output"].endswith("ok.mp4")


def test_round_refused_whole_when_it_would_cross_cap(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    per_job = 600.0
    spend_so_far = DEFAULT_CAP_USD - FIXED_ALLOWANCE_USD - 2.5 * per_job * GPU_PRICE["H100"]
    led.append(job_row(key="a", wall_s=spend_so_far / GPU_PRICE["H100"], gpu="H100"))
    assert led.can_afford(cost(2, per_job, "H100"))
    assert not led.can_afford(cost(3, per_job, "H100"))


def test_cap_is_per_ledger(tmp_path):
    led = Ledger(tmp_path / "l.jsonl", cap_usd=5)
    assert led.spent_usd() == FIXED_ALLOWANCE_USD
    assert not led.can_afford(3.0)
    assert led.can_afford(2.0)
    assert Ledger(tmp_path / "other.jsonl").can_afford(3.0)


def test_rounds_ignores_quick_and_keyframes(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.append(job_row(key="a", round=2))
    led.append(job_row(key="b", round=1))
    led.append(job_row(key="c", round=2))
    led.append(job_row(key="d", round=3, model="quick"))
    led.append(job_row(key="e", round=4, model="flux-schnell"))
    led.append(job_row(key="f", round=5, model="overhead"))
    led.append(job_row(key="g", round=9, shot="s2"))
    led.append(job_row(key="h", round=0))
    led.append({"kind": "verdict", "key": "a", "round": 8, "ok": True, "note": "", "ts": 0.0})
    assert led.rounds("s1") == [1, 2]
    assert led.rounds("s3") == []


def test_verdict_latest_wins_and_costs_nothing(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.verdict("x", False, "dissolve at 2s")
    led.verdict("x", True, "")
    assert led.verdicts()["x"]["ok"] is True
    assert led.spent_usd() == FIXED_ALLOWANCE_USD


def test_truncated_last_line_is_survivable(tmp_path):
    path = tmp_path / "l.jsonl"
    led = Ledger(path)
    led.append(job_row(key="a", wall_s=10.0))
    with path.open("a") as f:
        f.write('{"kind": "job", "ke')
    assert len(led.rows()) == 1
    assert led.spent_usd() == FIXED_ALLOWANCE_USD + 10.0 * GPU_PRICE["L4"]
    led.append(job_row(key="b"))
    assert [r["key"] for r in led.rows()] == ["a", "b"]


def test_undecodable_tail_bytes_are_survivable(tmp_path):
    path = tmp_path / "l.jsonl"
    led = Ledger(path)
    led.append(job_row(key="a", wall_s=10.0))
    with path.open("ab") as f:
        f.write(b'{"kind": "job", "prompt": "\xe6\x97')
    assert [r["key"] for r in led.rows()] == ["a"]
    assert led.spent_usd() == FIXED_ALLOWANCE_USD + 10.0 * GPU_PRICE["L4"]


def test_failed_row_not_done_but_costs(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.append(job_row(key="x", output=None, wall_s=300, gpu="H100", failed=True))
    led.append(job_row(key="y", output="", wall_s=0, gpu="H100"))
    assert led.done("x") is None and led.done("y") is None
    assert led.spent_usd() == pytest.approx(FIXED_ALLOWANCE_USD + 300 * GPU_PRICE["H100"])


def test_lock_is_exclusive(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    with led.locked(), (tmp_path / "l.lock").open("a") as f, pytest.raises(BlockingIOError):
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
