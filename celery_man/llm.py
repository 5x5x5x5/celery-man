"""Thin OpenAI-compatible chat client (llama-server, hosted, anything)."""

import base64
import mimetypes
import os
from pathlib import Path

import openai

from celery_man.project import ModelCfg


def _data_url(p: Path) -> str:
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(Path(p).read_bytes()).decode()}"


def chat(cfg: ModelCfg, system: str, user: str, images: list[Path] | None = None) -> str:
    client = openai.OpenAI(base_url=cfg.base_url, api_key=os.environ.get(cfg.api_key_env) or "none", timeout=300)
    content = [{"type": "text", "text": user}] + [{"type": "image_url", "image_url": {"url": _data_url(p)}} for p in images or []]
    r = client.chat.completions.create(
        model=cfg.model,
        temperature=0.7,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
    )
    return r.choices[0].message.content or ""
