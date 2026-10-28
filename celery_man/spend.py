"""What each paid UI action costs, and the check that runs before it spends."""

from celery_man.ledger import Ledger, cost
from celery_man.pipeline import estimate, quick_cost
from celery_man.project import Project
from celery_man.steps import EST_STILL_S, EST_WALL_S, SEEDS, STILL_GPU, TAKE_GPU


class PriceChanged(Exception):
    def __init__(self, price: float):
        super().__init__(f"the price is now ${price:.2f}")
        self.price = price


class OverReserve(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def prices(p: Project) -> dict[str, float]:
    return {
        "continue": estimate(p),
        "still": cost(1, EST_STILL_S, STILL_GPU),
        "keyframe_set": cost(4, EST_STILL_S, STILL_GPU),
        "take_round": cost(len(SEEDS) + 1, EST_WALL_S, TAKE_GPU),
    }


def summary(p: Project) -> dict:
    return {"spent": Ledger(p.ledger_path, p.cap_usd).spent_usd(), "reserve": quick_cost(p), "cap": p.cap_usd}


def check(p: Project, shown: float, now: float) -> None:
    """Refuse a spend whose price rose past what the click showed, or that would eat the quick-fallback reserve."""
    if now > shown + 0.01:
        raise PriceChanged(now)
    s = summary(p)
    if s["spent"] + now + s["reserve"] > s["cap"]:
        raise OverReserve(f"${now:.2f} would leave less than the ${s['reserve']:.2f} quick-fallback reserve under the ${s['cap']:.2f} cap")
