"""Model-made decisions. chat_fn(system, user, images) -> str; JSON in, validated records out."""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import openai

from celery_man.compose import PromptTooLong, compose
from celery_man.project import STYLE_KEYS, Brief, Shot, brief_from_dict

log = logging.getLogger(__name__)
ChatFn = Callable[[str, str, list[Path]], str]

CRAFT = (
    "Craft rules: every shot is one concrete, filmable action under a locked or slowly moving camera. "
    "No on-screen text, captions, signs or signage. Reply with JSON only, no prose."
)


class AutoError(RuntimeError):
    pass


def _parse(reply: str) -> Any:
    """First JSON value in the reply, ignoring fences and surrounding prose."""
    s = reply.replace("```json", "```")
    starts = [i for i in (s.find("{"), s.find("[")) if i >= 0]
    if not starts:
        raise ValueError("reply contains no JSON object or list")
    return json.JSONDecoder().raw_decode(s[min(starts) :])[0]


def ask_json[T](chat_fn: ChatFn, system: str, user: str, images: list[Path], validate: Callable[[Any], T], tries: int = 3) -> T:
    prompt, err = user, ""
    for n in range(tries):
        try:
            reply = chat_fn(system, prompt, images)
        except openai.OpenAIError as e:  # a dead server is not fixed by re-asking
            raise AutoError(f"model call failed: {e}") from e
        try:
            return validate(_parse(reply))
        except KeyError as e:
            err = f"missing key {e}"
        except (ValueError, TypeError) as e:  # JSONDecodeError is a ValueError
            err = str(e)
        log.warning("attempt %d/%d rejected: %s", n + 1, tries, err)
        prompt = f"{user}\n\nYour previous reply:\n{reply[:1500]}\n\nIt was rejected: {err}\nFix it and reply with JSON only."
    raise AutoError(f"model gave no valid reply after {tries} tries: {err}")


def _brief_text(b: Brief) -> str:
    return json.dumps(
        {"logline": b.logline, "style": b.style, "cast": [vars(c) for c in b.cast], "places": [vars(p) for p in b.places], "avoid": b.avoid}
    )


def write_brief(chat_fn: ChatFn, seed: str) -> Brief:
    system = (
        "You write a creative brief for a short music video. " + CRAFT + " Keys: logline (str), style (object with "
        f"{', '.join(STYLE_KEYS)}, all strings), cast ([{{name, description}}], short names), places ([{{name, description}}]), "
        "avoid ([str]). Descriptions are visual and self-contained."
    )
    user = f"Seed: {seed}" if seed.strip() else "No seed given: invent the theme freely."
    return ask_json(chat_fn, system, user, [], brief_from_dict)


def write_shots(chat_fn: ChatFn, brief: Brief, windows: list[tuple[float, float, str]]) -> list[Shot]:
    ids = [f"s{i:02d}" for i in range(1, len(windows) + 1)]
    plan = [{"id": i, "start": w[0], "end": w[1], "section": w[2]} for i, w in zip(ids, windows)]
    system = (
        "You plan the shots of a music video, one per window. " + CRAFT + " Reply with a JSON list with one object per id: "
        "{id, action, cast ([names]), place (name or null), framing}. Do not change or add windows."
    )
    user = f"Brief: {_brief_text(brief)}\nWindows: {json.dumps(plan)}"
    cast, places = {c.name for c in brief.cast}, {p.name for p in brief.places}

    def one(i, w, d) -> Shot:
        c, p = d.get("cast"), d.get("place")
        if isinstance(c, str):
            c = [c]
        if isinstance(p, str) and p.strip().lower() in ("", "none", "null"):
            p = None
        if not isinstance(c, list) or not all(isinstance(x, str) for x in c):
            raise ValueError(f"{i}: cast must be a list of names")
        for x in c:
            if x not in cast:
                raise ValueError(f"{i}: unknown cast '{x}' (known: {', '.join(sorted(cast))})")
        if p is not None and (not isinstance(p, str) or p not in places):
            raise ValueError(f"{i}: place must be a name or null (known: {', '.join(sorted(places))})")
        if not all(isinstance(d.get(k), str) and d[k] for k in ("action", "framing")):
            raise ValueError(f"{i}: action and framing must be non-empty strings")
        shot = Shot(i, w[0], w[1], w[2], d["action"], c, p, d["framing"])
        try:
            compose(shot, brief)
        except PromptTooLong as e:
            raise ValueError(f"{i}: {e}") from e
        return shot

    def validate(data) -> list[Shot]:
        if isinstance(data, dict) and isinstance(data.get("shots"), list):
            data = data["shots"]
        if not isinstance(data, list):
            raise TypeError("expected a JSON list")
        by_id = {d.get("id"): d for d in data if isinstance(d, dict)}
        if len(data) != len(ids) or set(by_id) != set(ids):
            raise ValueError(f"expected exactly one entry for each of {', '.join(ids)}")
        shots, errs = [], []
        for i, w in zip(ids, windows):
            try:
                shots.append(one(i, w, by_id[i]))
            except ValueError as e:
                errs.append(str(e))
        if errs:
            raise ValueError("; ".join(errs))
        return shots

    return ask_json(chat_fn, system, user, [], validate)


def _shot_text(shot: Shot) -> str:
    return json.dumps({k: getattr(shot, k) for k in ("action", "cast", "place", "framing")})


def check_keyframe(chat_fn_vision: ChatFn, shot: Shot, brief: Brief, png: Path) -> tuple[bool, str]:
    system = (
        "You check a keyframe image against its shot. Fail it for: wrong or missing cast, wrong place, visible text or signage, "
        'broken anatomy, or a composition that contradicts the action. Reply JSON: {"ok": bool, "why": str}.'
    )
    user = f"Brief: {_brief_text(brief)}\nShot: {_shot_text(shot)}"

    def validate(d) -> tuple[bool, str]:
        if not isinstance(d["ok"], bool):
            raise TypeError("ok must be true or false")
        return d["ok"], str(d.get("why", ""))

    return ask_json(chat_fn_vision, system, user, [png], validate)


def pick_take(chat_fn_vision: ChatFn, shot: Shot, brief: Brief, sheets: list[Path], must_pick: bool) -> tuple[int | None, str]:
    if not sheets:
        raise ValueError("pick_take needs at least one sheet")
    system = (
        "You judge contact sheets of candidate video takes for one shot, shown in order from index 0. Prefer the take that best "
        "matches the action with steady motion and no morphing, flicker or text. "
        + ("You must pick one. " if must_pick else "If none is acceptable, pick null. ")
        + 'Reply JSON: {"pick": index or null, "why": str}.'
    )
    user = f"Brief: {_brief_text(brief)}\nShot: {_shot_text(shot)}\nSheets: {len(sheets)}"

    def validate(d) -> tuple[int | None, str]:
        p = d["pick"]
        if p is None:
            if must_pick:
                raise ValueError("pick must be an index, null is not allowed")
        elif not isinstance(p, int) or isinstance(p, bool) or not 0 <= p < len(sheets):
            raise ValueError(f"pick {p!r} out of range; valid 0..{len(sheets) - 1}")
        return p, str(d.get("why", ""))

    return ask_json(chat_fn_vision, system, user, sheets, validate)


def rewrite_shot(chat_fn: ChatFn, shot: Shot, brief: Brief, reason: str) -> Shot:
    system = (
        "You simplify one shot that failed. " + CRAFT + " Keep it to a single simpler action. "
        "Reply JSON: {action, cast ([names]), place (name or null), framing}."
    )
    user = f"Brief: {_brief_text(brief)}\nShot: {_shot_text(shot)}\nWhy it failed: {reason}"

    def validate(d) -> Shot:
        new = Shot(shot.id, shot.start, shot.end, shot.section, d["action"], d["cast"], d["place"], d["framing"])
        if not isinstance(new.action, str) or not isinstance(new.framing, str) or not isinstance(new.cast, list):
            raise TypeError("action/framing must be strings and cast a list")
        try:
            compose(new, brief)  # raises ValueError for unknown names, PromptTooLong (a ValueError) when long
        except ValueError as e:
            raise ValueError(f"shot {shot.id}: {e}") from e
        return new

    return ask_json(chat_fn, system, user, [], validate)
