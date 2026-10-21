"""Command line: new, status, modes, run, quick, serve."""

import argparse
import functools
import logging
from pathlib import Path

from celery_man import llm
from celery_man.auto import AutoError, ChatFn
from celery_man.compose import PromptTooLong
from celery_man.generate import Generator, ModalGenerator
from celery_man.ledger import Ledger
from celery_man.pipeline import WaitingError, advance, estimate, quick, quick_cost, status
from celery_man.project import Project, write_json
from celery_man.steps import BudgetError
from celery_man.timing import TimingError

log = logging.getLogger("celery_man")
DECISIONS = ("brief", "shots", "keyframes", "takes")
MODES = ("tastemaker", "auto")
REFUSED = (BudgetError, TimingError, PromptTooLong, AutoError, WaitingError)


def make_generator() -> Generator:
    return ModalGenerator()


def make_chats(p: Project) -> tuple[ChatFn, ChatFn]:
    m = p.models
    return functools.partial(llm.chat, m["text"]), functools.partial(llm.chat, m["vision"])


def _status_lines(p: Project) -> None:
    for s in status(p):
        log.info("%-10s %-8s %s", s.step, s.state, s.detail)
    log.info("estimate $%.2f  spent $%.2f of $%.2f cap", estimate(p), Ledger(p.ledger_path).spent_usd(), p.cap_usd)


def _confirm(cost: float, yes: bool) -> bool:
    log.info("estimate $%.2f", cost)
    return yes or input("Proceed? [y/N] ").strip().lower() in ("y", "yes")


def _new(a) -> int:
    if (a.dir / "project.json").exists():
        log.error("%s already holds a project", a.dir)
        return 2
    Project.create(a.dir, a.song, a.source)
    log.info("created %s", a.dir)
    return 0


def _modes(a) -> int:
    new = {}
    for kv in a.pairs:
        step, _, mode = kv.partition("=")
        if step not in DECISIONS or mode not in MODES:
            log.error("bad %r: steps are %s, modes are %s", kv, "/".join(DECISIONS), "/".join(MODES))
            return 2
        new[step] = mode
    p = Project(a.dir)
    write_json(p.root / "project.json", p.config | {"modes": p.modes | new})
    return 0


def _run(a) -> int:
    p = Project(a.dir)
    if not _confirm(estimate(p), a.yes):
        log.info("aborted")
        return 1
    sts = advance(p, make_generator(), *make_chats(p))
    for s in sts:
        log.info("%-10s %-8s %s", s.step, s.state, s.detail)
    return 0 if all(s.state in ("done", "waiting") for s in sts) else 1


def _quick(a) -> int:
    p = Project(a.dir)
    if not _confirm(quick_cost(p), a.yes):
        log.info("aborted")
        return 1
    log.info("%s", quick(p, make_generator(), make_chats(p)[0]))
    return 0


def _serve(a) -> int:
    import uvicorn

    from celery_man.web.app import create_app  # Task 11

    uvicorn.run(create_app(Project(a.dir), make_generator, make_chats), host="127.0.0.1", port=a.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(prog="celery-man")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def cmd(name, fn, *args):
        sp = sub.add_parser(name)
        sp.add_argument("dir", type=Path)
        for flags, kw in args:
            sp.add_argument(*flags, **kw)
        sp.set_defaults(fn=fn)

    cmd("new", _new, (("--song",), {"type": Path, "required": True}), (("--source",), {"default": ""}))
    cmd("status", lambda a: _status_lines(Project(a.dir)) or 0)
    cmd("modes", _modes, (("pairs",), {"nargs": "+", "metavar": "STEP=MODE"}))
    cmd("run", _run, (("--yes",), {"action": "store_true"}))
    cmd("quick", _quick, (("--yes",), {"action": "store_true"}))
    cmd("serve", _serve, (("--port",), {"type": int, "default": 8765}))
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        return a.fn(a)
    except REFUSED as e:
        log.error("%s", e)
        return 2
    except Exception as e:  # noqa: BLE001 - CLI boundary: report, exit 1
        log.error("failed: %s", e)
        return 1
