import pytest

from celery_man import spend
from celery_man.ledger import Ledger, cost
from celery_man.pipeline import estimate, quick_cost
from celery_man.project import write_json
from celery_man.spend import OverReserve, PriceChanged
from celery_man.steps import EST_STILL_S, EST_WALL_S, SEEDS, STILL_GPU, TAKE_GPU
from tests.test_renders import project


def footage(tmp_path, song):
    return project(tmp_path, song, kinds=("footage",), scene=lambda i: None)


def test_prices_use_the_pipeline_constants(tmp_path, song):
    p = footage(tmp_path, song)
    assert spend.prices(p) == {
        "continue": estimate(p),
        "still": cost(1, EST_STILL_S, STILL_GPU),
        "keyframe_set": cost(4, EST_STILL_S, STILL_GPU),
        "take_round": cost(len(SEEDS) + 1, EST_WALL_S, TAKE_GPU),
    }


def test_summary_reserve_is_quick_cost(tmp_path, song):
    p = footage(tmp_path, song)
    assert spend.summary(p) == {"spent": Ledger(p.ledger_path).spent_usd(), "reserve": quick_cost(p), "cap": p.cap_usd}
    assert quick_cost(p) > 0


def test_check_refuses_risen_price(tmp_path, song):
    p = footage(tmp_path, song)
    with pytest.raises(PriceChanged) as e:
        spend.check(p, shown=0.10, now=0.12)
    assert e.value.price == 0.12


def test_check_allows_cent_of_slack(tmp_path, song):
    spend.check(footage(tmp_path, song), shown=0.10, now=0.105)


def test_check_keeps_reserve(tmp_path, song):
    p = footage(tmp_path, song)
    write_json(p.root / "project.json", p.config | {"cap_usd": Ledger(p.ledger_path).spent_usd() + 1.5 + quick_cost(p) - 0.01})
    with pytest.raises(OverReserve):
        spend.check(p, shown=1.5, now=1.5)
    write_json(p.root / "project.json", p.config | {"cap_usd": Ledger(p.ledger_path).spent_usd() + 1.5 + quick_cost(p) + 0.001})
    spend.check(p, shown=1.5, now=1.5)


def test_estimate_only_counts_named_shots(tmp_path, song):
    p = footage(tmp_path, song)
    assert 0 < estimate(p, only={"s01"}) < estimate(p)
    assert estimate(p, only=set()) == 0
