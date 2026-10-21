# celery-man

Theme-neutral music-video pipeline: timing, brief, shots, keyframes, takes, render, assemble. Animation shots are JS Canvas2D scenes rendered in headless Chromium (`animate.py`, `renders.py`); the render step makes their clips (free, local) and `assemble` treats them like takes. Generation on Modal.

## Commands
- `uv sync`
- `uv run pytest` (tests in `tests/`; skips slow tests), `uv run pytest -m slow` for the slow ones
- `uv run ruff check` / `uv run ruff format` (line length 140)
- `uv run celery-man {new,status,modes,run,quick,serve} DIR` (see README)
- `uv run modal run celery_man/gen_modal.py::access` / `::smoke` (costs money, ask first)

## Rules
- No theme, song, story, style or cast words anywhere in the repo. Tests use neutral fixtures ("a person walks", cast "A").
- Mechanical steps never branch on which mode wrote a decision file.
- All project JSON is written through `project.write_json` (temp file, then `os.replace`).
- The web server binds to 127.0.0.1 only.
- Never `git add -A`; add named paths. `projects/` and `smoke/` are git-ignored.
- uv only, Python 3.12, type hints on public functions, `logging` not `print`.
- Scene templates (`celery_man/scenes/templates`) draw only with `env.look` and params: no hard-coded colours or fonts.
- Tests that launch Chromium are `@pytest.mark.slow`; everything else uses `FakeRenderer`. `playwright install chromium` is needed once.
- Never cut prompt text silently; raise `PromptTooLong`.
