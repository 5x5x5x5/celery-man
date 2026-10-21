"""Compose a keyframe prompt from a shot plus the brief; never truncates."""

from functools import cache

from tokenizers import Tokenizer

from celery_man.project import Brief, Shot, sha12

TOKEN_LIMIT = 256


class PromptTooLong(ValueError):
    def __init__(self, total: int, fields: list[tuple[str, int]]):
        self.total, self.fields = total, fields
        top = ", ".join(f"{n} ({c} tokens)" for n, c in fields[:3])
        super().__init__(f"prompt is {total} tokens, limit {TOKEN_LIMIT}; longest: {top}")


@cache
def _tok() -> Tokenizer:
    return Tokenizer.from_pretrained("google-t5/t5-base")


def count_tokens(text: str) -> int:
    """T5 token count including the single trailing </s> that T5 adds."""
    return len(_tok().encode(text).ids)


def _parts(shot: Shot, brief: Brief) -> list[tuple[str, str]]:
    cast = {c.name: c.description for c in brief.cast}
    places = {p.name: p.description for p in brief.places}
    parts = [("framing", shot.framing), ("action", shot.action)]
    for n in shot.cast:
        if n not in cast:
            raise ValueError(f"unknown cast member: {n}")
        parts.append((f"cast:{n}", f"{n}: {cast[n]}"))
    if shot.place is not None:
        if shot.place not in places:
            raise ValueError(f"unknown place: {shot.place}")
        parts.append((f"place:{shot.place}", f"{shot.place}: {places[shot.place]}"))
    parts.append(("style", ", ".join(brief.style[k] for k in ("medium", "palette", "lighting", "camera"))))
    if brief.avoid:
        parts.append(("avoid", "no " + ", no ".join(brief.avoid)))
    return parts


def compose(shot: Shot, brief: Brief) -> str:
    parts = _parts(shot, brief)
    text = ". ".join(t for _, t in parts)
    total = count_tokens(text)
    if total > TOKEN_LIMIT:
        raise PromptTooLong(total, sorted(((n, count_tokens(t)) for n, t in parts), key=lambda f: -f[1]))
    return text


def prompt_sha(shot: Shot, brief: Brief) -> str:
    return sha12(compose(shot, brief))
