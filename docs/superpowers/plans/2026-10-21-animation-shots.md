# Animation Shots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a shot can be **animation**: a JS Canvas2D scene that moves with the music, rendered locally in headless Chromium. Animation and generated footage cut together in one beat-cut video. A tastemaker or the auto model chooses the kind and the scene for each shot.

**Architecture:**
- **Shots.** A shot gains `kind` and `scene`.
- **Brief.** The brief gains `look` (colours and a typeface) for scenes to read.
- **Render step.** A new mechanical step, `render`, sits between `shots` and `keyframes`. It renders each animation shot through a `Renderer` protocol into `renders/`:
  - `ChromiumRenderer` runs Playwright in a subprocess, so a runaway scene can be killed.
  - `FakeRenderer` uses ffmpeg testsrc and serves the tests.
- **Render cache.** Each render is cached by a hash of everything that shapes its frames.
- **Other steps.** keyframes, takes, estimates and quick cover footage shots only. assemble takes each shot's clip from the pick or the render.

**Tech Stack:**
- Existing: Python 3.12, uv, ruff, pytest, librosa, fastapi/jinja2, ffmpeg.
- New: `playwright` (Python) with Chromium; plain JS (no build step); 4 bundled OFL fonts.

**Spec:** `docs/superpowers/specs/2026-10-21-animation-shots-design.md`, which extends `docs/superpowers/specs/2026-10-20-celery-man-design.md`.

**Prior art:**
- `~/projects/paper-machine/render.py` has the Playwright → `toDataURL` → ffmpeg `image2pipe` path. It is known to work, so copy its shape.
- `~/projects/paper-machine/engine/` holds the page.

## Global Constraints

- **Repo rules.** These come from celery-man's CLAUDE.md:
  - uv only, Python 3.12, ruff with line length 140, pytest in `tests/`
  - type hints on public functions, `logging` not `print`
  - small focused files
  - all project JSON goes through `project.write_json`
  - never `git add -A`
- **No theme words** anywhere in the repo, including templates, template defaults and tests. Tests use neutral fixtures.
- **Mode-blind mechanical steps.** No mechanical step (render, keyframes, takes, assemble) branches on which mode wrote a decision.
- **Same frame size and rate as footage.** Animation renders at 960x544 and 24 fps. Import `W, H` from `celery_man.generate` and `FPS` from `celery_man.gen_modal`. If importing `gen_modal` pulls in modal at import time, move `FPS = 24` into `generate.py` and import it in both places.
- **Frame count.** A shot has `round(end * FPS) - round(start * FPS)` frames, the same formula `assemble` uses.
- **Free.** Animation spends $0 and writes no ledger rows.
- **Per-frame limit.** `FRAME_LIMIT_S = 2.0`.
- **Retry limit.** At most 3 tries for an auto custom scene, then a template fallback.
- **Kinds.** `KINDS = ("footage", "animation")`. Project `kinds` defaults to `["footage"]`.
- **Typefaces.** `TYPEFACES = ("serif", "sans", "mono", "display")`, bundled as:
  - Source Serif 4
  - Inter
  - JetBrains Mono
  - Archivo Black
  
  All four are OFL, from github.com/google/fonts. Each OFL.txt ships beside its font.
- **Default look.** `DEFAULT_LOOK = {"background": "#f4efe6", "colors": ["#1d1b18"], "typeface": "serif"}`.
- **Old projects keep working.** A shots.json without `kind`/`scene`, a brief.json without `look`, and a project.json without `kinds` all load, stay fresh, and behave exactly as before.
- **Browser tests are slow-marked.** Tests that launch Chromium are `@pytest.mark.slow`, and everything else uses `FakeRenderer`.
- **Commit trailer.** Every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **An existing footage-only project after upgrade.** status, estimate, advance and the web pages are unchanged, nothing goes stale, and the step list just gains `render: done`. Owned by Task 1 (`test_old_files_load_and_stay_fresh`) and Task 5 (`test_footage_only_project_unchanged`).
2. **A scene that never returns** (`while(true){}`) or keeps the page busy. The subprocess is killed at the wall limit and the shot fails cleanly, with no hang. Owned by Task 3 (`test_infinite_loop_times_out`).
3. **A tastemaker scene that fails.** The run stops at render as `waiting`, with the error in the detail. A second `advance` does not re-render the same failing inputs, and other shots still render. Owned by Task 5 (`test_failed_render_waits_and_does_not_retry`).
4. **An all-animation project.** There is no picks.json or keyframes.json, and assemble, status and report all cope. Owned by Task 5 (`test_all_animation_project_assembles`).
5. **A small auto model** that writes wrong params, an unknown template, or code that throws. It retries with feedback, then falls back, and never stalls. Owned by Task 6 (`test_bad_code_three_times_falls_back_to_template`, `test_out_of_range_param_is_fed_back`).

---

### Task 1: Shot kinds, scene field, brief look, project kinds

**Files:**
- Modify: `celery_man/project.py`, `celery_man/steps.py` (`shots_inputs`), `celery_man/pipeline.py` (`status` only: the kinds check), `celery_man/cli.py` (`new --kinds`)
- Create: `celery_man/templates.py` (catalogue + params validation; the template JS arrives in Task 4)
- Test: `tests/test_project.py`, `tests/test_templates.py`, `tests/test_cli.py`

**Interfaces:**
- Produces, in `project.py`:
  - `KINDS`, `TYPEFACES`, `DEFAULT_LOOK`
  - `Shot.kind: str = "footage"` and `Shot.scene: dict | None = None`, as new trailing dataclass fields with defaults
  - `Brief.look: dict = field(default_factory=lambda: dict(DEFAULT_LOOK))`
  - `look_from_dict(d) -> dict`
  - `Project.kinds -> list[str]`, which is `config.get("kinds", ["footage"])`
  - `Project.create(root, song, song_source, kinds=("footage",))`, which writes `"kinds"`
  - `Project.load_footage() -> list[Shot] | None` and `Project.load_animation() -> list[Shot] | None`, filtering `load_shots()` (None when it is None)
  - `Project.renders_path` (`renders.json`), `renders_dir` (`renders/`), `scenes_dir` (`scenes/`), `features_path` (`features.json`)
- Produces, in `templates.py`:
  - `TEMPLATES_DIR = Path(__file__).parent / "scenes" / "templates"`
  - `catalogue() -> dict[str, dict]`, mapping name → `{"family": str, "description": str, "params": {pname: spec}}` and read from `TEMPLATES_DIR/*.json`
  - `source(name: str) -> str`, the template's `.js` text
  - `validate_params(name: str, params: dict) -> dict`, which returns params with defaults filled in, or raises `ValueError` naming the parameter
  - `FALLBACK_TEMPLATE = "rings"`
  - A param spec is `{"type": "number"|"integer"|"string"|"choice"|"bool", "default": …, "min"?, "max"?, "choices"?, "max_len"?, "doc": str}`.

- [ ] **Step 1: Write failing tests** in `tests/test_project.py`:
  - `test_old_files_load_and_stay_fresh`:
    - A shots.json written with only the old 8 keys loads with `kind == "footage"` and `scene is None`.
    - A brief.json without `look` loads with `look == DEFAULT_LOOK`.
    - A project.json without `kinds` gives `p.kinds == ["footage"]`.
    - `steps.shots_inputs(p, brief)` equals the value the old formula gives: `sha12([windows(p), sorted cast names, sorted place names])`.
  - `test_animation_shot_round_trips`:
    - `{"kind": "animation", "scene": {"template": "t", "params": {}}}` survives save then load.
    - So does `{"file": "scenes/s01.js", "params": {"x": 1}, "note": "n"}`.
  - `test_bad_scenes_rejected` raises `ValueError` for each of:
    - a footage shot with a scene
    - an animation shot with no scene
    - a scene with both `template` and `file`
    - a scene with neither
    - `file` set to `"../x.js"` or `"scenes/a/b.js"`
    - `kind` set to `"cartoon"`
  - `test_look_validation` rejects:
    - colours that aren't `#rrggbb`
    - zero or six `colors`
    - an unknown typeface
    - extra keys
    
    It accepts `DEFAULT_LOOK`.
  - `test_kinds_enter_shots_stamp_only_when_not_default`: setting `kinds` to `["footage", "animation"]` changes `shots_inputs`, and `["footage"]` keeps the old value.

- [ ] **Step 2: Write failing tests** in `tests/test_templates.py`. Use a temporary `TEMPLATES_DIR` (monkeypatch) holding one `demo.json` that has a number param `size` (min 0, max 1, default 0.5), a string `text` (max_len 10, default `""`) and a choice `shape` (choices `["a", "b"]`).
  - `test_defaults_filled`
  - `test_out_of_range_rejected_naming_param`: `"size"` appears in the message.
  - `test_unknown_param_rejected`
  - `test_wrong_type_rejected`: a bool is not a number.
  - `test_unknown_template_rejected`
  - `test_string_too_long_rejected`

- [ ] **Step 3: Run the tests** with `uv run pytest tests/test_project.py tests/test_templates.py -v`. Expect them to fail.

- [ ] **Step 4: Implement.**
  - **Scene validation.** `shot_from_dict` accepts the 8 required keys plus optional `kind` and `scene`; it may not have other keys. It validates:
    - `kind` is in `KINDS`
    - footage has `scene is None`
    - an animation scene has exactly one of `template`/`file`, plus a `params` dict and an optional `note` str
    - `file` matches `^scenes/[A-Za-z0-9_-]+\.js$`
    - a template scene's params go through `templates.validate_params`, which fills in defaults
  - **Brief.** `brief_from_dict` takes `look` as an optional key (default `DEFAULT_LOOK`) and validates it with `look_from_dict`.
  - **`shots_inputs`.** Append `sorted(p.kinds)` to the hashed list only when `p.kinds != ["footage"]`.
  - **`pipeline.status`.** After `_window_mismatch`, add `_kind_problem(p, shots) -> str`. It returns, for example, `"s03 is animation; this project allows footage"`, or `""` when nothing is wrong. It feeds into the shots decision the same way the mismatch does.
  - **CLI.** `celery-man new` gets `--kinds` (comma list, validated against `KINDS`, default `footage`).
  - **Old shots.** An old shot with action, cast and so on but no `kind` must still go through `compose` unchanged.

- [ ] **Step 5: Run** `uv run pytest`. Expect everything to pass, with the existing tests unchanged.

- [ ] **Step 6: Commit:** `git add` the named files, then `git commit -m "Add shot kinds, scenes and the brief look so shots can be animation"`.

---

### Task 2: Audio features and per-frame data

**Files:**
- Create: `celery_man/features.py`
- Modify: `celery_man/steps.py` (`run_timing` also calls `features.ensure`)
- Test: `tests/test_features.py`

**Interfaces:**
- Consumes: `Project.features_path`, and `timing.json` (`beats`, `downbeats`, `song_sha`).
- Produces:
  - `compute(song: Path, fps: int) -> dict`, giving `{"song_sha", "fps", "level", "low", "mid", "high"}`. The last four are lists with one value per video frame over the whole song, each in 0..1 and rounded to 3 decimals.
  - `ensure(p: Project) -> dict`, which loads `features.json`, or computes and writes it when it is missing or its `song_sha` doesn't match `steps.song_sha(p)`.
  - `frame_data(timing: dict, feats: dict, start: float, end: float, fps: int) -> list[dict]`. Each frame has the keys `t`, `song_t`, `progress`, `beat`, `beat_phase`, `bar`, `bar_phase`, `on_beat`, `on_downbeat`, `level`, `low`, `mid` and `high`.

- [ ] **Step 1: Write failing tests:**
  - `test_frame_count_matches_assemble_formula`:
    - With start 1.01 and end 3.5 at 24 fps, `len(frame_data(...))` is `round(3.5*24) - round(1.01*24)`.
    - Frame 0 has `t == 0` and `song_t == round(1.01*24)/24`.
    - The last frame has `progress == 1`.
  - `test_beat_phase_and_flags` uses beats every 0.5 s, downbeats every 2 s, start 0, 24 fps:
    - `on_beat` is true on exactly frames 0, 12, 24 and so on.
    - `on_downbeat` is true on frames 0, 48 and so on.
    - At frame 6, `beat_phase == 0.5` and `bar_phase == 0.125`.
    - At frame 12, `beat == 1`.
  - `test_before_first_beat_is_phase_zero`: when the first beat is at 0.3 s, frames before it have `beat == 0`, `beat_phase == 0` and `on_beat` false.
  - `test_levels_index_by_song_frame`: with fake feats where `level[i] = i/1000`, frame k of a shot starting at 2.0 s gets `level == (48+k)/1000`. Past the end of the arrays, the last value is used.
  - `test_compute_on_tone`:
    - Uses the `song` fixture, a 220 Hz sine for 40 s.
    - The arrays have `round(40*24)` ± 1 entries.
    - All values are within 0..1.
    - `low` has a higher mean than `high`.
  - `test_ensure_caches_and_restamps`:
    - The second call doesn't recompute (monkeypatch `compute` to count calls).
    - Changing the song recomputes.

- [ ] **Step 2: Run** `uv run pytest tests/test_features.py -v`. Expect FAIL.

- [ ] **Step 3: Implement** using librosa:
  - Load mono at `sr = 24000`, so `hop = sr // fps` is exact.
  - Compute `stft` with `n_fft = 2048` and that hop.
  - Bands:
    - `level` is the RMS of the frame.
    - `low` is the summed magnitude below 250 Hz.
    - `mid` is 250–2000 Hz.
    - `high` is above 2000 Hz.
  - Normalise each band by its 99th percentile (guard against 0), then clip to 0..1.
  - **Phases.** In `frame_data`, beat and bar phases come from the bracketing beats or downbeats. After the last beat, extend with the last interval.
  - **`on_beat`.** True when some beat `b` satisfies `song_t - 1/fps < b <= song_t`, and the same for `on_downbeat`.
  - **Timing step.** `run_timing` writes timing, then calls `features.ensure(run.p)`.

- [ ] **Step 4: Run** `uv run pytest`. Expect PASS.

- [ ] **Step 5: Commit:** `git commit -m "Compute per-frame loudness and band energy so scenes can move with the song"`.

---

### Task 3: Scene runtime and renderers

**Files:**
- Create:
  - `celery_man/scenes/index.html` (canvas, `@font-face` for the 4 faces, CSP meta)
  - `celery_man/scenes/runtime.js`
  - `celery_man/scenes/fonts/{serif,sans,mono,display}.ttf`, each with its `OFL-<face>.txt`
  - `celery_man/animate.py`
- Modify: `pyproject.toml`, adding the `playwright` dependency and making sure hatchling ships `scenes/**` as package data
- Test: `tests/test_animate.py`

**Interfaces:**
- Produces, in `animate.py`:
  - `FRAME_LIMIT_S = 2.0` and `STARTUP_S = 30.0`
  - `class SceneError(RuntimeError)`
  - `@dataclass(frozen=True) RenderJob(shot_id: str, source: str, params: dict, look: dict, frames: list[dict], fps: int, width: int, height: int, seed: int)`
  - `class Renderer(Protocol): def render(self, job: RenderJob, out: Path) -> Path`. It writes `out` (an mp4 with exactly `len(job.frames)` frames, video only) and `out.with_suffix(".jpg")` (the poster, the middle frame), returns `out`, and raises `SceneError` with a readable message.
  - `ChromiumRenderer` and `FakeRenderer`. `FakeRenderer` produces ffmpeg testsrc of the right size, rate and frame count, plus a poster. It raises `SceneError` when the source contains `throw`, and records `self.jobs`.
  - `runtime_sha() -> str`, a sha12 over `index.html`, `runtime.js` and the font files' bytes.
  - `main(argv)`, the subprocess entry: `python -m celery_man.animate JOB.json OUT.mp4`.

**The scene contract.** Task 4's templates and Task 6's auto prompt both quote it, so `runtime.js` implements exactly this:
- **Globals.** A scene is a classic script that defines the globals `setup(env)` and `draw(ctx, f, env)`.
- **`env`.** It holds `params`, `look` (`background`, `colors`, and `typeface` already resolved to a CSS font-family name), `width`, `height`, `fps`, `duration`, and `random()`.
  - `random()` is a mulberry32 generator seeded with `job.seed`.
  - `Math.random` is replaced by the same generator.
  - `Date.now()` and `performance.now()` return the current frame's `song_t * 1000`.
- **Call order.** `setup` runs once, after `document.fonts.ready`. Then `draw` runs once per frame, in order. The canvas is not cleared between frames, so the scene owns its background.
- **Frame limit.** `runtime.js` keeps a real clock captured before pinning. A `draw` that takes longer than `FRAME_LIMIT_S` throws `frame N took X ms`.
- **Missing globals.** If either global is missing after load, the error is `scene must define setup(env) and draw(ctx, f, env)`.

**The sandbox:**
- **Fake origin.** The page is served from `http://scene.invalid/` through `page.route("**/*")`. Only `index.html`, `runtime.js`, `scene.js` and `fonts/*` are fulfilled, and every other request is aborted.
- **CSP.** `index.html` carries `default-src 'self'; script-src 'self'; connect-src 'none'; img-src 'self' data:; font-src 'self'`.
- **Error capture.** `pageerror` and any thrown error become `SceneError` in the child. The child exits non-zero with the message on stderr.
- **Timeout.** The parent runs the child with `subprocess.run(..., timeout=STARTUP_S + len(frames) * FRAME_LIMIT_S)`. On `TimeoutExpired` it raises `SceneError("scene did not finish in …s (stuck loop?)")`. Any other non-zero exit raises `SceneError` with the last 20 lines of stderr.
- **Atomic output.** The child writes `out` through a `.part.mp4` and `os.replace`, so a killed render never leaves a usable-looking clip.

- [ ] **Step 1: Fetch the fonts** with `curl` from `https://github.com/google/fonts` (`ofl/sourceserif4`, `ofl/inter`, `ofl/jetbrainsmono`, `ofl/archivoblack`), each with its `OFL.txt`. Check that each file is a real TTF with `file` before committing.

- [ ] **Step 2: Write failing tests** in `tests/test_animate.py`. All of them except the fake ones are `@pytest.mark.slow`. `GOOD` is a scene that fills the background with `env.look.background` and draws a rect whose width follows `f.level`.
  - `test_fake_renderer_frame_count_and_poster` (fast): 30 frames give `probe_duration ≈ 1.25` and a poster exists. A source containing `throw` raises `SceneError`.
  - `test_renders_exact_frames` (slow): `ffprobe -count_frames` equals `len(frames)` for a 13-frame job, the output is 960x544, and a poster jpg exists.
  - `test_same_inputs_identical_frames` (slow):
    - A scene that uses `Math.random()`, `env.random()` and `Date.now()`, rendered twice.
    - Extracting frames with `ffmpeg -f rawvideo` gives identical bytes.
    - Hash the decoded frames, not the mp4 container.
  - `test_throwing_scene_fails` (slow): `draw` throws `"nope"` at frame 5, and `SceneError` contains `nope`.
  - `test_missing_globals_fails` (slow)
  - `test_network_blocked` (slow): `setup` calls `fetch("https://example.com")`. The render must either raise `SceneError`, or succeed with the fetch rejected inside the page. Assert with a scene that draws red when the fetch resolves and blue when it rejects, then check that a pixel is blue. Do the same for `new Image().src = "http://example.com/x.png"`, where `onload` must never fire.
  - `test_infinite_loop_times_out` (slow): monkeypatch `FRAME_LIMIT_S` and `STARTUP_S` small (for example 0.5 and 15). A `draw` with `while(true){}` raises `SceneError` mentioning `did not finish` within about 20 s, and no `out` file exists.
  - `test_slow_frame_fails` (slow): a `draw` that busy-waits 2.5 s in real time (use the runtime's real clock, exposed as `env.__realNow` for tests only, or spin on a counter) raises `frame` … `took`.

- [ ] **Step 3: Run** `uv run pytest tests/test_animate.py -m "slow or not slow" -v`. Expect FAIL. First run `uv run playwright install chromium`.

- [ ] **Step 4: Implement** `index.html`, `runtime.js` and `animate.py`, following paper-machine's `engine_page` and `render_range`. The init call passes `{params, look, width, height, fps, duration, seed}`. Each frame is one `page.evaluate` that calls `draw` and returns `toDataURL('image/jpeg', 0.92)`, which is piped to ffmpeg as `image2pipe` → libx264 `yuv420p`, `-framerate fps`.

- [ ] **Step 5: Run** `uv run pytest -m "slow or not slow" tests/test_animate.py` and then `uv run pytest`. Both must PASS.

- [ ] **Step 6: Commit:** `git commit -m "Render Canvas2D scenes in a sandboxed headless browser"`.

---

### Task 4: Template library

**Files:**
- Create: `celery_man/scenes/templates/{words,title,grid,rings,bars,drift,burst}.{js,json}`
- Test: `tests/test_templates.py` (extend it)

**Interfaces:**
- Consumes: the scene contract (Task 3) and `templates.catalogue` and `validate_params` (Task 1).
- Produces 7 templates. The `family` comes first in each line.
  - **words** (type): `text` (string, max_len 120) split on spaces, one word per beat (or per bar via `every`: choice beat|bar), with `size` and `position` (choice center|lower|upper).
  - **title** (type): `text` (string, max_len 60) on one line, scaled with a pulse on downbeats by `pulse` (0–1) and fading in over `fade` beats.
  - **grid** (geometric): an n×m tile grid (`cols` 2–24), with tiles flashing on beats by `pulse` and rotation driven by `mid`.
  - **rings** (geometric, also `FALLBACK_TEMPLATE`): concentric rings born on beats and expanding with `level`, using `count` and `thickness`.
  - **bars** (geometric): stripes whose heights follow `low`, `mid` and `high`, using `orientation` (horizontal|vertical) and `count`.
  - **drift** (particles): a noise flow field with `count` particles (100–4000), speed scaled by `level`, and trails controlled by `trail` (0–1).
  - **burst** (particles): particles bursting from the centre on downbeats, sized by `low`, with `count` and `gravity`.

Every template:
- draws only with `env.look` colours and typeface and its own params
- has a default for every param, and the defaults must render something visible
- writes a one-sentence `description` and a `doc` for each param, which the auto prompt and the web page show

- [ ] **Step 1: Write failing tests:**
  - `test_catalogue_is_complete` (fast):
    - The 7 names are present.
    - Every param has a default that passes `validate_params(name, {})`.
    - The families are `{type, geometric, particles}`.
    - Every `.json` has a matching `.js`.
  - `test_templates_are_theme_neutral` (fast):
    - No hex colour literal (`#[0-9a-fA-F]{3,6}\b`) and no `rgb(` appears in any template `.js`.
    - No string default is non-empty except where its doc says it is a layout choice.
  - `test_every_template_renders_at_two_tempos` (slow, parametrised over the 7 names × beat spacing 0.5 s and 0.8 s):
    - Render a 2 s shot through `ChromiumRenderer` with defaults, plus `text="one two three"` for the type templates.
    - It must not raise.
    - The poster must not be one flat colour: Pillow `getextrema` differs on at least one channel.

- [ ] **Step 2: Run** them and expect FAIL. **Step 3:** Write the 7 templates. **Step 4:** Run `uv run pytest -m "slow or not slow" tests/test_templates.py` and expect PASS.

- [ ] **Step 5: Commit:** `git commit -m "Add seven theme-neutral scene templates"`.

---

### Task 5: The render step and footage/animation integration

**Files:**
- Create: `celery_man/renders.py`
- Modify: `celery_man/steps.py`, `celery_man/takes.py`, `celery_man/pipeline.py`, `celery_man/cli.py` (renderer factory)
- Test: `tests/test_renders.py`, plus updates to `tests/test_pipeline.py` and `tests/test_web.py`. Existing assertions that list 6 steps now list 7.

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces, in `renders.py`:
  - `scene_source(p, shot) -> str`: the template source, or the project file text. A missing file raises `SceneError(f"missing {path}")`.
  - `render_key(p, shot, look, feats) -> str`: a sha12 over `[source, scene params, look, shot.id, start, end, FPS, W, H, feats["song_sha"], animate.runtime_sha()]`.
  - `render_need(p, shot, look, entry) -> str`:
    - `"done"`: the key matches, there is no error, and the clip exists
    - `"failed"`: the key matches and the entry has an error
    - `"gen"`: otherwise
  - `render_shot(run, shot, brief) -> str`: renders one shot, writes its `renders.json` entry `{"key", "clip": "renders/<sid>-<key>.mp4", "poster": "renders/<sid>-<key>.jpg", "error": ""}`, and returns `""` or the error text. It reuses a matching done entry without rendering, and it is used by Task 6 too.
  - `run_render(run)`: calls `features.ensure`, then `render_shot` for every animation shot whose need is `"gen"`, writing `renders.json` after each.
- Produces, in `steps.Run`: a new field `renderer: Renderer | None = None` and a method `run.renders() -> Renderer` that lazily builds `ChromiumRenderer()`. `advance(..., renderer=None)` and `quick(..., renderer=None)` pass it through.
- Produces, in `pipeline`: `STEPS = ("timing", "brief", "shots", "render", "keyframes", "takes", "assemble")`.

**Behaviour:**
- **`status` for render.** It is a per-shot state over the animation shots. With none open it is `done`. When every open need is `"failed"`, it is `waiting`, whatever the mode, with detail `"s03: <first line of error>"`, so advance stops without re-rendering a deterministic failure. Otherwise it is `stale`/`missing`. Render is not in `SPENDING`.
- **Footage only.** keyframes, takes, `estimate`, `quick_cost`, `_quick_price` and the keyframes/takes web pages use `p.load_footage()` in place of `p.load_shots()`. Check every `load_shots` call site listed by `grep -n load_shots -r celery_man`. The footage-only ones are in pipeline status, estimate, quick_cost, quick, takes `_plans`, steps `run_keyframes`, and web keyframes/takes. assemble, report and the shots page keep all shots.
- **assemble.**
  - `status` requires render and takes to be done. The video is stale when it is older than `picks.json` or `renders.json`, whichever exists.
  - `run_assemble` takes footage clips from picks (`in` as now) and animation clips from `renders.json` (`in` = 0).
  - `picks.json` may be absent when there are no footage shots.
- **quick.** After brief and shots it runs `run_render`. If render is not `done`, it raises `AutoError(f"quick: render is {state}: {detail}")`. Then it does stills and takes for footage only, and the timeline mixes both kinds.
- **Report and provenance.**
  - `write_report` gives each shot's kind. Animation lines show the template name or file, the render key, and the scene's `note` when there is one.
  - Add a "## Scene fallbacks" section listing the shots whose note starts with `fallback:`.
  - `write_provenance` adds a line for each bundled font with its OFL licence.
- **CLI.** `make_renderer()` returns `ChromiumRenderer()` and is passed into `advance` and `quick`.

- [ ] **Step 1: Write failing tests** in `tests/test_renders.py`. Use the `make()` and `text_chat` helpers from `test_pipeline.py` (move them to `tests/helpers.py` if importing across test modules is awkward), plus a tastemaker project whose shots.json is written directly with a mix of kinds and the `kinds` setting both.
  - `test_footage_only_project_unchanged`:
    - An all-auto footage project behaves as before.
    - `status` lists 7 steps with `render: done`.
    - The `estimate` value equals the pre-change formula.
    - No `renders.json` is written.
    - The FakeRenderer gets no jobs.
  - `test_mixed_project_cuts_on_downbeats`:
    - Even shots are animation (template `rings`) and odd shots are footage.
    - `advance` reaches assemble.
    - The video's duration equals the range.
    - `cut_points` of the cuts used equal the shot starts, which are downbeats in the fixture timing.
    - Footage spend covers only odd shots: count the ledger rows by shot.
  - `test_render_cached_by_key`:
    - A second `advance` renders nothing.
    - Changing `look` re-renders every animation shot and does not touch keyframes.
    - Changing a footage shot's action does not re-render.
  - `test_failed_render_waits_and_does_not_retry`:
    - Shot s02 uses `scenes/s02.js` containing `throw`.
    - `advance` leaves render `waiting` with `s02` in the detail, and other animation shots rendered.
    - A second `advance` sends the renderer no job for s02.
    - Fixing the file re-renders s02.
  - `test_all_animation_project_assembles`:
    - `kinds=["animation"]` and all shots on templates.
    - The video is produced, with no `picks.json` and no `keyframes.json`.
    - The report lists every shot as animation.
    - The ledger holds no job rows.
  - `test_quick_renders_animation`: `quick` on a mixed project produces `quick.mp4`, and its timeline holds `renders/…` clips for animation shots.
  - `test_kind_not_allowed_makes_shots_stale`: with `kinds = ["footage"]` and an animation shot in shots.json, the shots state is not done and its detail names the shot.

- [ ] **Step 2: Run** them and expect FAIL. **Step 3:** Implement. **Step 4:** Run `uv run pytest` and expect all PASS, with the step-count assertions updated in existing tests.

- [ ] **Step 5: Commit:** `git commit -m "Add the render step and cut animation and footage shots together"`.

---

### Task 6: Auto decisions for animation

**Files:**
- Modify: `celery_man/auto.py`, `celery_man/steps.py` (`run_shots`)
- Create: `celery_man/auto_scene.py`, holding the scene-contract prompt text and the fix/fallback loop so `auto.py` stays focused
- Test: `tests/test_auto.py`, `tests/test_auto_scene.py`

**Interfaces:**
- Consumes: `templates.catalogue`/`validate_params`/`FALLBACK_TEMPLATE` and `renders.render_shot`.
- Produces:
  - `auto.write_brief`: the system prompt asks for `look` too (hex colours, 1–5 `colors`, typeface from `TYPEFACES`). `_lenient_brief` fills in `DEFAULT_LOOK` when `look` is missing or not a dict. A present but invalid look is fed back as an error.
  - `auto.write_shots(chat_fn, brief, windows, kinds: list[str]) -> tuple[list[Shot], dict[str, str]]`. It returns the shots and `{sid: code}` for custom scenes.
    - When `kinds` is `["footage"]`, the prompt and validation are exactly as today, and the dict is empty.
    - Otherwise each entry also has `kind`. An animation entry has `action` plus either `template` and `params`, or `code`.
    - Custom-code shots get `scene = {"file": f"scenes/{sid}.js", "params": {}}`.
    - Bad params or an unknown template are fed back through `ask_json`, naming the shot.
    - The footage CRAFT rule ("no on-screen text") is stated as applying to footage shots only.
  - `auto_scene.CONTRACT`: the one-page scene contract, from Task 3, as prompt text.
  - `auto_scene.settle(chat_fn, run, shot, brief, code) -> Shot`:
    - It writes `scenes/<sid>.js` and calls `renders.render_shot`.
    - On error, it asks the model to fix the code: same contract, the previous code, and the error. That is up to 3 renders in total.
    - After 3 failures, it calls `pick_template(chat_fn, shot, brief) -> dict`, which is `ask_json` validated with `validate_params`. If that raises `AutoError`, it uses `{"template": FALLBACK_TEMPLATE, "params": {}}`.
    - It returns the shot with that scene and `note = "fallback: <last error, first line>"`.
    - A shot that rendered gets `note = ""`.
  - `steps.run_shots`: calls `write_shots(..., p.kinds)`, runs `settle` for each custom shot, then saves. The settle renders land in `renders.json`, so the render step reuses them.

- [ ] **Step 1: Write failing tests**, using `FakeRenderer` (its `throw` rule simulates broken code):
  - `test_footage_only_prompt_unchanged`: with `kinds = ["footage"]`, the system prompt sent equals today's, and the shots match.
  - `test_mixed_kinds_parsed`: a scripted chat returns one template shot, one custom-code shot and one footage shot. The result has the right kinds, the scenes are filled with defaults, and the code dict holds the custom one.
  - `test_kind_not_allowed_fed_back`: an animation entry under `kinds = ["footage"]` gets a retry whose prompt names the shot.
  - `test_out_of_range_param_is_fed_back`: the first reply has `count: 99999`. The second prompt contains `count`, and the second reply is accepted.
  - `test_fixed_code_on_second_try`: the first code contains `throw` and the fix doesn't. The result is the file scene with an empty note, and the renderer saw 2 jobs.
  - `test_bad_code_three_times_falls_back_to_template`: three throwing codes, then the chat picks `grid`. The scene is `{"template": "grid", …}`, the note starts with `fallback:`, and the renderer saw exactly 3 jobs for the shot.
  - `test_fallback_when_template_pick_also_fails`: the chat returns garbage for the pick, and the scene falls back to `FALLBACK_TEMPLATE`.
  - `test_brief_without_look_gets_default`: a brief reply missing `look` is accepted with `DEFAULT_LOOK`.

- [ ] **Step 2: Run** them and expect FAIL. **Step 3:** Implement. **Step 4:** Run `uv run pytest` and expect PASS.

- [ ] **Step 5: Commit:** `git commit -m "Let the auto model choose kinds, fill templates and write scenes with a fallback"`.

---

### Task 7: Web page

**Files:**
- Modify: `celery_man/web/pages.py`, `celery_man/web/templates/{brief,shots}.html`, `celery_man/web/static/style.css`, and `celery_man/web/app.py` (pass the renderer factory, if `/run` needs one)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: Task 1 validation, `templates.catalogue()`, and `renders.json`.
- Produces:
  - **Brief page.** It gains a `look` section:
    - `background` as a colour input
    - five colour inputs, `color_1` … `color_5`, each with an "unused" checkbox
    - a typeface select
    
    Saving validates through `brief_from_dict`, and errors show as now.
  - **Shots page.** Each row gets a `kind_{sid}` select, shown only when the project allows both kinds.
    - **Footage rows** keep the current fields.
    - **Animation rows** show:
      - the action
      - a `scene_{sid}` select of the template names plus `custom`
      - for a template, one input per param of the *saved* template, named `param_{sid}_{name}`, typed from the spec (number/checkbox/select/text), with its doc as hint text
      - for custom, a `code_{sid}` textarea that saves to `scenes/{sid}.js`
      - the render poster (`/files/renders/…jpg`) and the render error from `renders.json`, when present
    - **Switching template or kind** takes effect on save, and the new template's params start at their defaults. No JavaScript is needed.
    - **Errors.** Validation errors return 422 with the row marked, as now. `compose` checks only footage rows.
  - **Overview.** It lists the 7 steps, which the existing status loop should already handle. Check it.
    - It also gets a "Kinds" form with footage and animation checkboxes (at least one) next to the modes form. `POST /kinds` writes `kinds` into project.json through `write_json`. It is guarded like `/modes` (409 while a run is going). The spec says kinds can be edited later on the page.

- [ ] **Step 1: Write failing tests:**
  - `test_brief_look_saves_and_validates`: a good look saves, and a bad hex returns 422 with the error shown.
  - `test_shots_kind_select_only_when_both_allowed`
  - `test_switch_to_animation_then_set_params`:
    - Post with `kind_s01 = animation` and `scene_s01 = grid`. On reload, the grid's param inputs are present at their defaults.
    - Post `param_s01_cols = 9`, and shots.json holds `cols: 9`.
    - Post `cols = 999`, and the response is 422 naming `cols`.
  - `test_custom_code_saved_to_scenes`: shots.json holds the `file` scene and `scenes/s01.js` holds the code.
  - `test_render_error_and_poster_shown`: with `renders.json` holding an error for s01 and a poster for s02, both appear on the page.
  - `test_kinds_form_saves_and_rejects_empty`: posting animation only saves it, and posting nothing returns 422.
  - `test_old_project_pages_unchanged`: a footage-only project's shots page has no kind select and no scene fields.

- [ ] **Step 2: Run** them and expect FAIL. **Step 3:** Implement. **Step 4:** Run `uv run pytest` and expect PASS.

- [ ] **Step 5: Look at it.** Run `uv run celery-man serve` on a mixed fixture project. Take screenshots of `/brief` and `/shots` at 1280 px and 375 px with Playwright, which is now a dependency, and check that the editorial look holds and nothing scrolls sideways at 375 px.

- [ ] **Step 6: Commit:** `git commit -m "Edit the look, shot kinds and scenes on the web page"`.

---

### Task 8: Docs and a live run

**Files:**
- Modify: `README.md`, `CLAUDE.md`
- Test: `tests/test_end_to_end.py` (slow)

- [ ] **Step 1: README.** Add:
  - `uv run playwright install chromium`
  - `--kinds`
  - the scene contract (link to or include `auto_scene.CONTRACT`'s content)
  - the template list with params
  - where custom scenes go
  - that animation is free and rendered locally
  
  **CLAUDE.md.** Add the render step to the step list, plus the rule "templates draw only with `env.look` and params".

- [ ] **Step 2: Write the slow test.** `test_end_to_end_animation_project` is a tastemaker project on the `song` fixture with real `detect` timing and `kinds = ["animation"]`. Write a brief and shots.json directly: one shot per template, cycling, with one custom scene file. Run `advance` with the real `ChromiumRenderer` and `FakeGenerator`. Then check:
  - `video.mp4` exists
  - its duration matches the range
  - `report.md` lists every template
- [ ] **Step 3: Run** `uv run pytest -m "slow or not slow"`. Expect all PASS. Also run `uv run ruff check` and `uv run ruff format --check`, and expect them clean.

- [ ] **Step 4: Live auto run**, which spends no money:
  - Start llama-server with Qwen3-4B on port 8091, as in `~/projects/celery-man-runs/drift-run/project.json` (the binary and model are in `~/projects/celery-man-runs/llm`).
  - Make a new project under `~/projects/celery-man-runs/` on `drift/song.wav`, with `--kinds animation` and every decision on auto.
  - Run `celery-man quick --yes`.
  - Watch `out/quick.mp4` (frames at a few cuts), read the report, and note any fallbacks.
  - Stop llama-server afterwards with `kill $(pgrep -x llama-server)`, never `pkill -f`.
- [ ] **Step 5: Commit:** `git commit -m "Document animation shots and add an end-to-end render test"`.
