"""Model suggestions kept as candidates under candidates/, outside the decision files, until the tastemaker uses one."""

from dataclasses import asdict
from pathlib import Path

from celery_man import auto
from celery_man.compose import PromptTooLong, compose
from celery_man.project import Project, Shot, brief_from_dict, read_json, sha12, shot_from_dict, update_json
from celery_man.steps import STILL_MODEL, Run, brief_inputs, gen_stills, merge_entry, shots_inputs

KEYFRAME_SUGGESTIONS = 4


def _path(p: Project, kind: str, sid: str | None = None) -> Path:
    path = p.root / "candidates" / (f"{kind}/{sid}.json" if sid else f"{kind}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _with_id(item: dict) -> dict:
    return {"id": sha12(item)} | item


def _add(p: Project, path: Path, items: list[dict], **fields) -> None:
    """Append items (and set fields) in one candidates file; earlier candidates stay."""
    update_json(p, path, lambda d: (d or {"items": []}) | fields | {"items": [*(d or {"items": []})["items"], *items]})


def _item(p: Project, path: Path, cid: str) -> dict:
    for item in (read_json(path) or {}).get("items", []):
        if item["id"] == cid:
            return item
    raise KeyError(f"no candidate {cid} in {path.relative_to(p.root)}")


def _shot(p: Project, sid: str) -> Shot:
    for s in p.load_shots() or []:
        if s.id == sid:
            return s
    raise KeyError(f"no shot {sid}")


def briefs(run: Run, steer: str) -> None:
    p = run.p
    found = auto.suggest_briefs(run.chat("brief", "text"), p.config["seed"], steer)
    _add(p, _path(p, "brief"), [_with_id({"brief": asdict(b)}) for b in found], steer=steer)


def shots(run: Run, sid: str, steer: str) -> None:
    """Free ideas for one window, then one paid still per footage idea; an empty steer falls back to the stored note."""
    p, path = run.p, _path(run.p, "shots", sid)
    brief, all_shots = p.load_brief(), p.load_shots() or []
    at = next(n for n, s in enumerate(all_shots) if s.id == sid)
    me = all_shots[at]
    neighbours = [s for s in all_shots[max(at - 1, 0) : at + 2] if s.id != sid and not s.is_open]
    note = (read_json(path) or {}).get("note", "")
    ideas = auto.suggest_shots(run.chat("shots", "text"), brief, sid, (me.start, me.end, me.section), p.kinds, neighbours, steer or note)
    items = [{"shot": asdict(s), "code": code, "still": None, "error": None} for s, code in ideas]
    jobs, which = [], []
    for n, (s, _) in enumerate(ideas):
        if s.kind != "footage":
            continue
        try:
            jobs.append((sid, compose(s, brief), n + 1))
            which.append(n)
        except PromptTooLong as e:
            items[n]["error"] = str(e)
    for n, still in zip(which, gen_stills(run, jobs)):
        items[n] |= {"still": str(still.relative_to(p.root))} if still else {"error": "the still failed"}
    _add(p, path, [_with_id(i) for i in items], steer=steer, note=note)


def keyframes(run: Run, sid: str, near_seed: int | None = None) -> None:
    """Four paid stills on new seeds after the highest the shot has used, or on near_seed * 10 + 1.. for Variations."""
    p, path = run.p, _path(run.p, "keyframes", sid)
    shot, brief = _shot(p, sid), p.load_brief()
    prompt = compose(shot, brief)
    used = [r["seed"] for r in run.led.rows() if r.get("kind") == "job" and r.get("shot") == sid and r.get("model") == STILL_MODEL]
    base = near_seed * 10 if near_seed is not None else max(used, default=0)
    jobs = [(sid, prompt, base + k) for k in range(1, KEYFRAME_SUGGESTIONS + 1)]
    vision = run.chat("keyframes", "vision") if p.config.get("vision_flags") else None
    items = []
    for (_, _, seed), still in zip(jobs, gen_stills(run, jobs)):
        if still is None:
            continue
        flag = None
        if vision:
            ok, why = auto.check_keyframe(vision, shot, brief, still)
            flag = None if ok else why
        items.append(_with_id({"path": str(still.relative_to(p.root)), "seed": seed, "prompt_sha": sha12(prompt), "flag": flag}))
    _add(p, path, items)


def use_brief(p: Project, cid: str) -> None:
    p.save_brief(brief_from_dict(_item(p, _path(p, "brief"), cid)["brief"]), brief_inputs(p))


def use_shot(p: Project, sid: str, cid: str) -> None:
    item = _item(p, _path(p, "shots", sid), cid)
    new, now = shot_from_dict(item["shot"]), _shot(p, sid)
    if (new.start, new.end) != (now.start, now.end):
        raise ValueError(f"{sid}: the window moved since this idea was made; ask for new ideas")
    if item["code"] is not None:
        p.scenes_dir.mkdir(exist_ok=True)
        (p.root / new.scene["file"]).write_text(item["code"])
    stamp = shots_inputs(p, p.load_brief())
    update_json(p, p.shots_path, lambda d: d | {"inputs": stamp, "shots": [asdict(new) if x["id"] == sid else x for x in d["shots"]]})


def use_keyframe(p: Project, sid: str, cid: str) -> None:
    item = _item(p, _path(p, "keyframes", sid), cid)
    entry = {"path": item["path"], "seed": item["seed"], "prompt_sha": item["prompt_sha"], "approved": True, "note": ""}
    merge_entry(p, p.keyframes_path, sid, entry)


def reopen_note(p: Project, sid: str, note: str) -> None:
    """None of the keyframes fit: the note becomes the default steer for the next shot ideas."""
    update_json(p, _path(p, "shots", sid), lambda d: {"steer": "", "items": []} | (d or {}) | {"note": note})
