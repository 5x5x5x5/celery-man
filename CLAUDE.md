# celery-man

Theme-neutral music-video pipeline: timing, brief, shots, keyframes, takes, render, assemble. Animation shots are JS Canvas2D scenes rendered in headless Chromium (`animate.py`, `renders.py`); the render step makes their clips (free, local) and `assemble` treats them like takes. Generation on Modal.

## Commands
- `uv sync`
- `uv run pytest` (tests in `tests/`; skips slow tests), `uv run pytest -m slow` for the slow ones
- `uv run ruff check` / `uv run ruff format` (line length 140)
- `uv run celery-man {new,status,modes,run,quick,serve} DIR` (see README)
- Front end: `npm --prefix web ci`, `npm --prefix web test` (vitest), `npm --prefix web run build` (type-checks, then builds into `celery_man/web/dist`, git-ignored; `serve` answers 503 until it exists). Node 24.
- `uv run pytest tests/test_ui_e2e.py -m slow`: the whole tastemaker path in Chromium over a live server; skips without a build.
- `uv run modal run celery_man/gen_modal.py::access` / `::smoke` (costs money, ask first)

## Layout
- `celery_man/web/`: the FastAPI JSON API (`app.py` projects, files, front end; `routes.py` decisions and paid actions; `state.py` the one State snapshot the page polls, field names identical to `web/src/types.ts`; `jobs.py` per-project paid queue and free jobs; `preview.py` sandboxed scene previews).
- `web/`: React + TypeScript + Vite. `src/App.tsx` routes `#/p/<id>/<step>?shot=<sid>`; `src/steps/` one screen per step (Song, Brief, Shots, Keyframes, Takes, Cut); shared pieces (`PricedButton`, `ModeSwitch`, `RoughCut`, `InPoint`, ...) beside it; `src/api.ts` every call.
- Project `candidates/` hold unchosen suggestions; `fill/` the stand-in clips of a draft.

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
