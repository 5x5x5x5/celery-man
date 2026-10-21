# celery-man Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a theme-neutral music-video pipeline. It takes any song. Four creative decisions (brief, shots, keyframes, takes) are each made by a person in a local web page (tastemaker) or by a configurable model (auto). It produces a beat-cut video from FLUX keyframes and LTX-2.5 clips on Modal.

**Architecture:** the code is a flat Python package, `celery_man/`. Every decision is a JSON file in a project folder, stamped with a hash of its inputs. A file is stale when its stamp no longer matches. The mechanical steps read only those files.

`pipeline.advance()` walks the steps in order and runs each mechanical or auto step that is missing or stale. It stops when a tastemaker decision is missing.

GPU work goes through a `Generator` protocol:
- `ModalGenerator` is the real one, carried over from paper-machine.
- `FakeGenerator` (ffmpeg testsrc) runs the whole pipeline in tests, with no Modal.

Model calls go through `llm.chat()`, which works with any OpenAI-compatible server.

**Tech Stack:** Python 3.12, uv, ruff, pytest. Libraries:
- modal
- beat-this (beats and downbeats), librosa (sections)
- tokenizers + huggingface_hub (T5 token count)
- openai (the client for any OpenAI-compatible server)
- fastapi, uvicorn, jinja2, python-multipart (the web page)
- pillow

The pipeline also uses ffmpeg and ffprobe.

**Spec:** `docs/superpowers/specs/2026-10-20-celery-man-design.md`

**Source of carried-over code:** `~/projects/paper-machine/footage/` at commit ba313d1. Read the named file and copy it. Then remove every paper-machine name, path and theme word.

## Global Constraints

- The repo contains no song, theme, story, style or cast words in code, defaults, templates or tests. Tests use neutral fixtures like `"a person walks"` and cast `"A"`.
- No mechanical step branches on which mode wrote a decision file.
- Python `>=3.12,<3.13`, pinned in `.python-version` (ML wheels). The Modal image is python 3.12.
- uv only; ruff, `line-length = 140`; pytest in `tests/`; type hints on public functions; `logging`, never `print`; small focused files; main branch.
- Commits: concise, imperative, about the why, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never `git add -A`: add named paths.
- All JSON written into a project goes through `project.write_json` (temp file, then `os.replace`).
- Modal app `celery-man`, Volume `celery-man-weights`, secret `huggingface` (already exists in workspace 5x5x5x5). diffusers is pinned to `b800f828f3951c4d206e154411bcbfa693cd322e`.
- The animator runs on an L4 with 88 GiB RAM, group offload, `max_containers=1`. The keyframer runs on an L40S with `max_containers=1`.
- Clips: 960x544, 24 fps, 121 frames (5.04 s). The trim handle (in-point) is 0.5 s, so the longest window is `MAX_WINDOW_S = 4.54`.
- Default cap: `$25` per project. `FIXED_ALLOWANCE_USD = 3`. `EST_WALL_S = 260` per L4 draft take, plus one more per run for the container load.
- FLUX T5 limit: 256 tokens. Count them with the `google-t5/t5-base` tokenizer, which uses the same SentencePiece vocabulary.
- The web server binds to `127.0.0.1` only. Design: light editorial: cream `#f6f1e7` background, ink `#1d1b18` text, one accent, a serif face for headings. No neon, no gradients.
- Never cut text silently. A prompt over the limit raises `PromptTooLong`, naming the longest fields.

## Project folder (file contracts)

Every decision file except `timing.json` is `{"inputs": "<sha12>", ...}`. `inputs` is `sha12(canonical JSON of the upstream values)`, where `sha12(x) = sha256(x).hexdigest()[:12]`.

| File | Written by | Content | `inputs` covers |
|---|---|---|---|
| `project.json` | `new` / user | `song` (path relative to project), `song_source` (free text for provenance), `range` `[start_s\|null, end_s\|null]`, `cap_usd`, `modes` `{brief,shots,keyframes,takes: "tastemaker"\|"auto"}`, `seed` (auto brief seed line, may be ""), `models` `{text,vision: {base_url, model, api_key_env}}` | — |
| `timing.json` | `timing` step | `duration`, `beats`, `downbeats`, `tempo` `[[t,bpm]...]`, `sections` `[{start,end,label}]`, `confidence`, `song_sha` | — (stale when `song_sha` changes) |
| `brief.json` | decision 1 | `logline`, `style` `{medium,palette,lighting,camera}`, `cast` `[{name,description}]`, `places` `[{name,description}]`, `avoid` `[str]` | `seed` |
| `shots.json` | decision 2 | `shots` `[{id, start, end, section, action, cast:[name], place: name\|null, framing}]` | windows + brief |
| `keyframes.json` | keyframe step + decision 3 | `{shot_id: {path, seed, prompt_sha, approved: bool\|null, note}}` (no file-level inputs; staleness is per shot via `prompt_sha`) | — |
| `picks.json` | decision 4 | `{shot_id: {key, clip, in, round, quick: bool}}` or `{shot_id: {rejected: true, round, reason}}` | — (per shot: stale when the key's prompt or keyframe no longer matches) |
| `ledger.jsonl` | ledger | job rows, verdict rows | — |
| `keys/`, `takes/`, `sheets/`, `out/video.mp4`, `out/report.md`, `PROVENANCE.md` | pipeline | | |

`windows` are produced by `timing.plan_windows`. Shot ids are `s01`, `s02`, … in window order.

## Review Focus

1. **Slow songs.** A song where one bar is longer than 4.54 s. Windows must split at beats, never exceed `MAX_WINDOW_S`, and still tile the range with no gaps.
2. **Bad model output.** Output that isn't JSON, has the wrong number of shots, has changed windows, or names an unknown cast member. Retry twice, then fail loudly. Never write a bad decision file.
3. **Editing the brief after takes exist.** Every keyframe and pick becomes stale, nothing regenerates until the user runs it, and `estimate()` reflects the redo.
4. **Kill mid-run, then rerun.** No finished take is regenerated, and no half-written JSON or PNG is left behind.
5. **The cap runs out between rounds.** Unfinished shots get one quick take each, and the video still assembles. If a quick pass over the whole range can't fit before the first spend, `advance` refuses to start.

---

### Task 1: Scaffold and project model

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `celery_man/__init__.py`, `celery_man/project.py`, `tests/test_project.py`

**Interfaces:**
- Produces: `sha12(obj) -> str` (canonical JSON: `sort_keys=True, separators=(",",":")`)
- Produces: `write_json(path, obj) -> None` (atomic)
- Produces: `read_json(path) -> dict | list | None` (None if missing)
- Produces: dataclasses `Brief`, `CastMember(name, description)`, `Place(name, description)`, `Shot(id, start, end, section, action, cast, place, framing)`, `ModelCfg(base_url, model, api_key_env)`
- Produces: `class Project(root: Path)`, with:
  - properties `config` (dict from project.json), `song` (Path), `modes`, `cap_usd`, `range`
  - path properties `timing_path`, `brief_path`, `shots_path`, `keyframes_path`, `picks_path`, `ledger_path`, `keys_dir`, `takes_dir`, `sheets_dir`, `out_dir`
  - `load_brief() -> Brief | None`, `load_shots() -> list[Shot] | None`
  - `save_brief(b, inputs)`, `save_shots(shots, inputs)`
- Produces: `Project.create(root, song: Path, song_source: str) -> Project`. It copies the song into the folder, and writes project.json with default modes (all `"tastemaker"`), `cap_usd` 25, `range` `[null, null]` and `seed` "". The default models are a local llama-server at `http://127.0.0.1:8080/v1`, with model `"local"` and `api_key_env` `""` for both roles.
- Produces: `brief_from_dict(d) -> Brief` and `shot_from_dict(d) -> Shot`. Both raise `ValueError` on missing or extra keys or wrong types. This is a trust boundary: the input comes from a model or the web.

- [ ] **Step 1:** `uv init --package`-free scaffold.
  - `pyproject.toml` (name `celery-man`) with:
    - deps: modal, numpy, librosa, soundfile, beat-this, tokenizers, huggingface_hub, openai, fastapi, uvicorn, jinja2, python-multipart, pillow
    - dev: pytest, ruff, httpx
    - `[project.scripts] celery-man = "celery_man.cli:main"`
    - `[tool.pytest.ini_options] pythonpath=["."]`, with marker `slow`
    - ruff line-length 140
  - `.gitignore`: `__pycache__/`, `.venv/`, `.superpowers/`, `projects/`.
  - `uv sync`. If `beat-this` fails to resolve on 3.12, fall back to `beat-this @ git+https://github.com/CPJKU/beat_this@<commit>` and note it.
- [ ] **Step 2: Failing tests** in `tests/test_project.py`:
  - `test_write_json_atomic_roundtrip`: write, then read, gives equal values, and no `.tmp` file remains.
  - `test_sha12_key_order_independent`: `sha12({"a":1,"b":2}) == sha12({"b":2,"a":1})`, and `len == 12`.
  - `test_create_copies_song_and_defaults`: the `song` file exists inside root, `modes == {"brief":"tastemaker","shots":"tastemaker","keyframes":"tastemaker","takes":"tastemaker"}`, `cap_usd == 25`.
  - `test_brief_from_dict_rejects_extra_and_missing`: `pytest.raises(ValueError)` for each case.
  - `test_shot_from_dict_rejects_bad_types`: `start="x"` raises `ValueError`.
  - `test_save_load_shots_roundtrip`.
- [ ] **Step 3:** Run `uv run pytest tests/test_project.py -v`. Expected: FAIL (import).
- [ ] **Step 4:** Implement `celery_man/project.py`.
- [ ] **Step 5:** Run `uv run pytest -v && uv run ruff check . && uv run ruff format --check .`. Expected: PASS.
- [ ] **Step 6:** Commit: `git add pyproject.toml uv.lock .python-version .gitignore celery_man tests docs` and commit with "Scaffold celery-man and the project-folder model".

### Task 2: Ledger (carried over, cap per project, rounds)

**Files:**
- Create: `celery_man/ledger.py`, `tests/helpers.py`, `tests/test_ledger.py`

**Interfaces:**
- Produces everything from paper-machine `footage/ledger.py`:
  - `job_key(shot_id, seed, prompt, keyframe) -> str`
  - `GPU_PRICE`, `FIXED_ALLOWANCE_USD`, `DEFAULT_CAP_USD = 25.0`
  - `Ledger(path, cap_usd=DEFAULT_CAP_USD)` with `locked`, `append`, `rows`, `done`, `spent_usd`, `verdict`, `verdicts`
- Changes:
  - `can_afford(cost_usd: float) -> bool`, true if `spent + cost <= cap`
  - `cost(n_jobs, est_wall_s, gpu) -> float`
  - `rounds(shot_id) -> list[int]`: sorted distinct `round >= 1` of job rows for this shot whose model is not `"flux-schnell"`, `"quick"` or `"overhead"`
- `tests/helpers.py`: `job_row(**over)`, copied from paper-machine `tests/footage_helpers.py` with `gpu="L4"`.

- [ ] **Step 1: Failing tests:**
  - port paper-machine `tests/test_footage_ledger.py`, adapted to `can_afford(cost)`
  - `test_cap_is_per_ledger`: `Ledger(p, cap_usd=5)` refuses a cost of $3 once $3 has been spent (because of the fixed allowance)
  - `test_rounds_ignores_quick_and_keyframes`
- [ ] **Step 2:** Run them; expected FAIL. **Step 3:** Implement. **Step 4:** PASS, plus ruff.
- [ ] **Step 5:** Commit "Carry the job ledger over with a per-project cap".

### Task 3: Timing for any song

**Files:**
- Create: `celery_man/timing.py`, `tests/test_timing.py`

**Interfaces:**
- Produces:
  - `MAX_WINDOW_S = 4.54`
  - `CONFIDENCE_MIN = 0.8`
  - `class TimingError(ValueError)`
- Produces: `build_timing(beats, downbeats, duration, sections, song_sha) -> dict`. It is pure.
  - `tempo` is a list of `[t, bpm]`, with one entry wherever the 4-beat-median bpm changes by more than 3%.
  - `confidence` is the fraction of consecutive inter-downbeat intervals within ±10% of each other.
- Produces: `detect(song: Path) -> dict`. It runs beat_this on CPU (`File2Beats(checkpoint_path="final0", device="cpu", dbn=False)`) for beats and downbeats. Sections come from librosa, using agglomerative segmentation on beat-synchronous chroma+MFCC with `k = max(2, round(duration/30))`, and the boundaries are snapped to the nearest downbeat. The labels are `"A"`, `"B"`, … by order of first appearance, with repeated material reusing its label when the cluster matches. If label matching is too fragile, plain `"1"`, `"2"`, … in order is acceptable; ledger it. It then returns `build_timing(...)`.
- Produces: `plan_windows(timing, start=None, end=None, max_s=MAX_WINDOW_S) -> list[tuple[float, float, str]]` (start, end, section label).
  - `start` and `end` snap to the nearest downbeat. `None` means the first and last downbeat.
  - Windows are made greedily: take 2 bars if they fit within `max_s`, else 1 bar, else split the bar at the last beat that keeps the piece within `max_s`.
  - A window never crosses a section boundary.
  - The windows tile `[start, end]` exactly.
  - Raises `TimingError` if `confidence < CONFIDENCE_MIN`, with a message saying the song has no steady pulse. It also raises `TimingError` if the range is empty.

- [ ] **Step 1: Failing tests** (pure; synthetic beat arrays):
  - `test_windows_tile_range_at_120bpm`: 4/4, 120 bpm, 32 bars. Every window is 4.0 s, and consecutive windows share edges.
  - `test_windows_at_90bpm_fall_back_to_one_bar`: a 2-bar window would be 5.33 s, so each window is 2.667 s.
  - `test_windows_split_slow_bars_at_beats`: at 40 bpm (6 s bars), every window is ≤ 4.54, and every edge is a beat time.
  - `test_windows_never_cross_sections`
  - `test_tempo_change_is_reported`: 100 → 130 bpm halfway gives 2 tempo entries, and the windows still sit on downbeats.
  - `test_range_snaps_to_downbeats`
  - `test_low_confidence_raises`: jittered downbeats raise `TimingError`.
- [ ] **Step 2: Slow test:** `@pytest.mark.slow test_detect_on_synthetic_drums`.
  - Synthesize 20 s of 4/4 at 110 bpm with numpy: a kick on beat 1, a snare on 2 and 4, a hat on 8ths.
  - `detect` finds the beats within 70 ms on ≥ 90% of them, and the tempo is within 2 bpm.
- [ ] **Steps 3–5:** Run (FAIL), implement, run all (including `-m slow` once), then ruff.
- [ ] **Step 6:** Commit "Detect beats, downbeats and sections; plan beat-snapped windows".

### Task 4: Prompt composition

**Files:**
- Create: `celery_man/compose.py`, `tests/test_compose.py`

**Interfaces:**
- Produces: `TOKEN_LIMIT = 256`, and `class PromptTooLong(ValueError)` with `.fields: list[tuple[str, int]]` (longest first).
- Produces: `count_tokens(text) -> int`, using the `google-t5/t5-base` tokenizer from the hub, cached via `functools.cache`.
- Produces: `compose(shot: Shot, brief: Brief) -> str`. The order is: framing; action; for each cast name in `shot.cast`, `"<name>: <description>"`; the place, as `"<name>: <description>"`; the style (`medium`, `palette`, `lighting`, `camera`); then `"no " + ", no ".join(avoid)` if `avoid` isn't empty. Parts are joined with `". "`. It raises `PromptTooLong` if `count_tokens > TOKEN_LIMIT`.
  - An unknown cast or place name raises `ValueError`.
- Produces: `prompt_sha(shot, brief) -> str`, which is `sha12(compose(...))`.

- [ ] **Step 1: Failing tests:**
  - `test_cast_description_verbatim`
  - `test_style_after_action_and_avoid_last`
  - `test_unknown_cast_raises`
  - `test_too_long_names_longest_field`: a 400-word description is `fields[0][0]`, `"cast:A"`
  - `test_style_change_changes_every_prompt_sha_but_no_shot_text`
- [ ] **Steps 2–4:** Run (FAIL), implement, then PASS and ruff. The tokenizer download needs the network; mark one test `slow` only if it can't be cached.
- [ ] **Step 5:** Commit "Compose prompts from shot plus brief, rejecting overlong ones".

### Task 5: Assembly and contact sheets

**Files:**
- Create: `celery_man/assemble.py`, `tests/test_assemble.py`

**Interfaces:**
- Produces: `probe_duration(path) -> float` and `class ClipTooShort(ValueError)` (carried over).
- Produces: `assemble(cuts: list[dict], song: Path, out: Path, fps=24, w=960, h=544) -> Path`.
  - Each cut is `{"clip", "in", "start", "end"}`: song-time window edges, in order and contiguous.
  - The song is trimmed to `[cuts[0].start, cuts[-1].end]`.
  - It keeps paper-machine's checks: `ClipTooShort`, `-an` on segments, and the song as the only audio.
- Produces: `cut_points(cuts) -> list[float]`.
- Produces: `contact_sheet(clip, dest) -> Path`, using the ffmpeg `fps=2,scale=480:-1,tile=6x2` from paper-machine `cmd_sheets`.

- [ ] **Step 1: Failing tests:**
  - port paper-machine `tests/test_footage_assemble.py`, adapted to explicit windows (e.g. 4 cuts of 4 s from t=8)
  - `test_output_duration_matches_windows` (±1 frame)
  - `test_only_song_audio`
  - `test_uneven_windows`: cuts of 2.667 s and 4.0 s, where the cut points equal the window edges
  - `test_contact_sheet_written`
- [ ] **Steps 2–4:** Run (FAIL), implement, then PASS and ruff.
- [ ] **Step 5:** Commit "Assemble beat-snapped cuts under the song".

### Task 6: Generators (Modal and fake)

**Files:**
- Create: `celery_man/generate.py` (protocol, job types, `FakeGenerator`, `ModalGenerator`), `celery_man/gen_modal.py` (Modal app, carried over), `tests/test_generate.py`

**Interfaces:**
- Produces: `StillJob(shot_id, prompt, seed)`, `ClipJob(shot_id, key, prompt, seed, keyframe: bytes)`, and `Result = dict | BaseException`, with keys `png` | `mp4`, `wall_s`, `gpu`.
- Produces: `class Generator(Protocol)` with `session() -> ContextManager`, `stills(jobs) -> list[Result]` and `clips(jobs) -> list[Result]`. Results are in job order. Exceptions are returned, never raised; this matches `starmap(return_exceptions=True)`.
- Produces: `FakeGenerator(fail: set[str] = set())`.
  - A still is a 960x544 solid-colour PNG coloured from `seed`.
  - A clip is ffmpeg `testsrc2=size=960x544:rate=24`, 121 frames.
  - Shot ids in `fail` return `RuntimeError`.
  - `gpu` is `"L4"` or `"L40S"`, and `wall_s` is `1.0`.
- Produces: `ModalGenerator()`, where `session()` wraps `modal.enable_output()` and `app.run()`, and `stills` and `clips` call `Keyframer` and `Animator` through `starmap(..., return_exceptions=True)`.
- `gen_modal.py` comes from paper-machine `footage/gen_modal.py`:
  - app `celery-man`, Volume `celery-man-weights`
  - `smoke(seed=7)` uses the neutral prompt `"a paper cup on a wooden table, soft daylight, locked camera"` and writes to `./smoke/`
  - `check_access`, `download_weights` and the `access` entrypoint stay
  - `Keyframer.still` and `Animator.clip` stay; `clip` returns `{"mp4", "wall_s", "max_rss_gib"}`
  - Drop the `hq` path, so draft only (YAGNI; paper-machine has it if needed).

- [ ] **Step 1: Failing tests:**
  - `test_fake_clip_is_5_04s_960x544`
  - `test_fake_failure_returned_not_raised`
  - `test_results_in_job_order`
- [ ] **Steps 2–4:** Run (FAIL), implement, then PASS and ruff. `gen_modal.py` is only import-checked (`python -c "import celery_man.gen_modal"`); there is no Modal call here.
- [ ] **Step 5:** Commit "Generator protocol: Modal LTX/FLUX and an ffmpeg fake for tests".

### Task 7: Model client and auto deciders

**Files:**
- Create: `celery_man/llm.py`, `celery_man/auto.py`, `tests/test_auto.py`

**Interfaces:**
- Produces: `chat(cfg: ModelCfg, system: str, user: str, images: list[Path] = []) -> str` in `llm.py`. It uses the `openai.OpenAI(base_url, api_key=os.environ.get(cfg.api_key_env) or "none")` chat completions, with images as base64 `image_url` data URLs, `temperature=0.7`, and a 300 s timeout.
- Produces: `ask_json(chat_fn, system, user, images, validate: Callable[[Any], T], tries=3) -> T`. It strips ``` fences, `json.loads`es the reply and validates it. On failure it re-asks, appending the error. After `tries` it raises `AutoError`.
- Produces in `auto.py`, where each function takes `chat_fn` (injectable; tests pass a stub):
  - `write_brief(chat_fn, seed: str) -> Brief`
  - `write_shots(chat_fn, brief, windows) -> list[Shot]`. The model gets the windows as `(id, start, end, section)` and returns only `action, cast, place, framing` per id. The code builds the `Shot`s, so the model cannot move windows. Validation: one entry per id, cast and place names exist in the brief, and `compose` doesn't raise `PromptTooLong` (on a too-long error the message is fed back).
  - `check_keyframe(chat_fn_vision, shot, brief, png: Path) -> tuple[bool, str]`
  - `pick_take(chat_fn_vision, shot, brief, sheets: list[Path], must_pick: bool) -> tuple[int | None, str]` (index into sheets)
  - `rewrite_shot(chat_fn, shot, brief, reason) -> Shot` (same window, a simpler single action)
- Prompts:
  - Brief and shots ask for concrete, filmable, single locked or slow camera actions, with no on-screen text or signage (lessons from the trial).
  - Prompt wording is the implementer's.
  - No theme words appear in prompts beyond these craft rules.

- [ ] **Step 1: Failing tests** (stub `chat_fn` returning canned strings):
  - `test_ask_json_retries_then_succeeds`: the first reply is junk and the second is valid
  - `test_ask_json_gives_up_after_three`: `AutoError`
  - `test_write_shots_rejects_unknown_cast_then_accepts`
  - `test_write_shots_keeps_windows`: the stub tries to return `start` too, and the windows are still the planned ones
  - `test_pick_take_must_pick_rejects_null`
  - `test_chat_sends_images_as_data_urls`: monkeypatch the openai client and inspect the payload
- [ ] **Steps 2–4:** Run (FAIL), implement, then PASS and ruff.
- [ ] **Step 5:** Commit "Auto deciders behind any OpenAI-compatible model".

### Task 8: Pipeline: status, staleness, advance

**Files:**
- Create: `celery_man/pipeline.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces: `STEPS = ("timing", "brief", "shots", "keyframes", "takes", "assemble")`.
- Produces: `@dataclass StepState(step, state, detail)`, where `state` is `"done" | "stale" | "missing" | "waiting"`. `"waiting"` means a tastemaker decision is missing; `detail` names the shots.
- Produces: `status(p: Project) -> list[StepState]`. Staleness rules:
  - **timing:** `song_sha` changed.
  - **brief:** `inputs != sha12(seed)` (in tastemaker mode, saving the brief from the web stamps the current seed).
  - **shots:** `inputs != sha12([windows, brief])`.
  - **keyframes:** per shot, `prompt_sha` differs from the current one, or the keyframe is missing.
  - **takes:** per shot, the pick's ledger row prompt differs from the current `compose`, or the keyframe sha differs. A rejected entry with `round < 2` means the shot needs a rewrite.
  - **assemble:** `out/video.mp4` is older than `picks.json`, or missing.
- Produces: `estimate(p) -> float`, the USD for every keyframe and take that `advance` would generate now. Keyframes cost `n × 20 s × L40S`. Takes cost `(n_shots × 3 + 1) × EST_WALL_S × L4`, and on a rerun only the missing keys are counted.
- Produces: `advance(p, gen: Generator, chat_text=None, chat_vision=None) -> list[StepState]`. It runs steps in order until done or a `"waiting"` step:
  - **timing:** `detect`, then `write_json`.
  - **brief / shots:** in auto mode, `write_brief` and `write_shots` with `plan_windows(timing, *range)`. In tastemaker mode with the file missing or stale, return waiting.
  - **keyframes:** generate stills for stale shots via `gen.stills` (seed 1, or the previous seed + 1 if regenerating), save to `keys/<id>-<sha12 of png>.png` (content-addressed, so it is never overwritten), and write keyframes.json with `approved=None`. In auto mode, `check_keyframe`: on reject, regenerate once with seed + 1 and accept that one, recording the note. In tastemaker mode, wait while any `approved` is not `True`. A tastemaker rejection sets `approved=False`, which the next `advance` regenerates.
  - **takes:**
    1. Shots without a valid pick are run. The round for each is `max(ledger.rounds(shot)) + 1`, or `1`, and it must be ≤ 2. Round 2 requires that the prompt or keyframe changed, using `not_rewritten` logic carried over from paper-machine `direct.py`.
    2. Seeds are `round*100 + {1,2,3}`, keys come from `job_key`, and `ledger.done` keys are reused.
    3. The `_run_takes` pattern from paper-machine `direct.py` is kept, including the overhead row in `finally`. Clips are saved to `takes/<key>.mp4` (file first, then the ledger row), and one contact sheet is made per take.
    4. Auto mode: `pick_take(must_pick = round == 2)`. When a take is picked, `picks[shot] = {key, clip, in: 0.5, round, quick: False}`. On a reject, `picks[shot] = {rejected, round, reason}`, then `rewrite_shot`, save shots.json (re-stamped), and loop. The keyframe is now stale, so the loop regenerates it and runs round 2.
    5. Tastemaker mode: wait for picks. A web rejection writes the rejected entry, and the shot waits for its edit.
  - **assemble:** run `assemble` on the cuts from shots + picks, then write `out/report.md` and `PROVENANCE.md`. The report covers: per shot, the mode that decided each step, rounds, the pick, whether it was quick, and any failures; then spend against the cap. Provenance gives the LTX-2.x Community License, FLUX.1-schnell Apache-2.0, `song_source` and the diffusers sha.
- All Modal work in one `advance` call happens inside one `gen.session()`.

- [ ] **Step 1: Failing tests** (`FakeGenerator`, stub chat functions, a timing.json written directly with 8 windows of 4 s, so `detect` isn't needed):
  - `test_all_auto_runs_to_video`: all modes auto. `advance` returns every step done, `out/video.mp4` exists, and its duration equals the range.
  - `test_tastemaker_waits_for_brief`: the default modes return `waiting` at brief, with no generation calls.
  - `test_mixed_modes`: tastemaker brief and shots (written by the test), auto keyframes and takes, run to a video.
  - `test_editing_brief_marks_everything_stale_without_running`: run all-auto to done, change `style.palette`, and `status` shows keyframes and takes stale, with `estimate > 0`. No generation happens until `advance`.
  - `test_rerun_reuses_finished_takes`: the `FakeGenerator` fails one shot. Rerun `advance`, and only that shot's clips are generated (count the calls).
  - `test_auto_reject_rewrites_then_round_two_must_pick`: the vision stub rejects round 1 for `s01`. Then shots.json has a new action for `s01`, the ledger shows rounds [1, 2], and the pick is from round 2.
  - `test_round_three_refused`
- [ ] **Steps 2–4:** Run (FAIL), implement, then PASS and ruff.
- [ ] **Step 5:** Commit "Pipeline: hash-stamped decisions, staleness, and advance in either mode".

### Task 9: Budget preflight and the quick fallback

**Files:**
- Modify: `celery_man/pipeline.py`
- Test: `tests/test_budget.py`

**Interfaces:**
- Produces: `quick_cost(p) -> float`, the cost of 1 still and 1 take for every shot that has no pick, plus 1 load.
- Produces: `quick(p, gen, chat_text=None) -> Path`, the test mode. It first runs any missing timing, brief and shots steps the way `advance` does: auto if set, waiting (BudgetError-free `WaitingError`) if tastemaker. It then makes 1 take per shot with seed 1, uses each take unreviewed (`quick: True`), assembles to `out/quick.mp4`, and ignores modes for keyframes and takes.
- Changes to `advance`:
  - **Preflight:** before the first spend in a call, if `ledger.spent + quick_cost > cap`, it raises `BudgetError("a quick pass over the range needs $X, $Y left")`.
  - **Fallback:** before a takes round, if the round's cost won't fit but `quick_cost` does, the round isn't run. Instead, each unfinished shot gets one quick take, recorded with `quick: True` and the ledger model `"quick"`. The pipeline then assembles, and the report lists those shots under "Quick fallback (cap reached)".

- [ ] **Step 1: Failing tests:**
  - `test_preflight_refuses_before_any_spend`: with a cap of $3.10, `BudgetError` is raised and the ledger has no job rows
  - `test_fallback_to_quick_when_round_unaffordable`: set the cap so round 1 doesn't fit but quick does. The video is still made, every pick has `quick=True`, and the report mentions the fallback.
  - `test_quick_mode_uses_one_take_per_shot`
- [ ] **Steps 2–4:** Run (FAIL), implement, then PASS and ruff.
- [ ] **Step 5:** Commit "Refuse runs a quick pass can't cover; fall back to quick takes at the cap".

### Task 10: CLI

**Files:**
- Create: `celery_man/cli.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `main(argv=None) -> int` (argparse, subcommands):
  - `new DIR --song FILE [--source TEXT]`
  - `status DIR`: one line per step with its state and detail, plus `estimate`, spend and cap
  - `run DIR [--yes]`: prints the estimate and asks `y/N` on stdin unless `--yes`, then runs `advance` with `ModalGenerator` and chat functions built from `project.models`
  - `quick DIR [--yes]`
  - `serve DIR [--port 8765]`
  - `modes DIR STEP=MODE ...`, e.g. `modes p brief=auto takes=auto`, which validates the step names and modes
- Output goes through `logging` at INFO with format `%(message)s`. Exit codes: 0 for ok, 1 for a failure, 2 for refused or invalid (`BudgetError`, `TimingError`, `PromptTooLong` and `AutoError` print their message).

- [ ] **Step 1: Failing tests** (monkeypatch the generator factory to `FakeGenerator`):
  - `test_new_then_status`
  - `test_modes_rejects_unknown_step`: exit 2
  - `test_run_without_yes_aborts_on_n`: stdin "n", no generation
  - `test_run_yes_all_auto_to_video`: stub chat factory
- [ ] **Steps 2–4:** Run (FAIL), implement, then PASS and ruff.
- [ ] **Step 5:** Commit "CLI: new, status, modes, run, quick, serve".

### Task 11: Tastemaker web page

**Files:**
- Create: `celery_man/web/app.py`, `celery_man/web/templates/{base,overview,brief,shots,keyframes,takes}.html`, `celery_man/web/static/style.css`, `tests/test_web.py`

**Interfaces:**
- Produces: `create_app(project: Project, gen_factory, chat_factory) -> FastAPI`. `serve` runs `uvicorn.run(app, host="127.0.0.1", port)`.
- Routes, server-rendered with plain HTML forms and no JS framework:
  - `GET /`: the overview. Step states, modes (changeable with a form: `POST /modes`), the estimate, spend against the cap, and a "Run (≈$X)" button (`POST /run`). The run happens in a background thread; while it's running, the page shows "running…" and refreshes itself with `<meta http-equiv="refresh" content="5">`. Only one run at a time; a second POST returns 409.
  - `GET/POST /brief`: a form for every brief field. Cast and places are repeatable rows, with 3 blank rows to add more. Saving stamps `inputs = sha12(seed)`.
  - `GET/POST /shots`: a table with one row per planned window (start–end, section), with action, cast (checkboxes), place (select) and framing. Each row shows `count_tokens(compose(...))` out of 256, and turns red over the limit. On save, `PromptTooLong` re-renders the page with the error on that row, and nothing is saved.
  - `GET /keyframes`: a grid of stills, with approve and reject buttons per shot (`POST /keyframes/{id}`).
  - `GET /takes`: per shot, the take contact sheets. You can pick one (`POST /takes/{id}/pick`) or reject all (`POST /takes/{id}/reject`). Reject all needs a reason, and is disabled in round 2. A rejected shot links to its row on /shots.
  - `GET /files/{path}`: serves files from the project folder only. A path that resolves outside the folder returns 404.
- Every POST writes through the same `save_*` and `write_json` functions the auto steps use.

- [ ] **Step 1: Failing tests** (FastAPI `TestClient`, a project with timing and shots prepared):
  - `test_save_brief_writes_stamped_file`
  - `test_shots_too_long_not_saved`: the response contains the error, and the shots file is unchanged
  - `test_pick_writes_picks_json`
  - `test_reject_disabled_in_round_two`
  - `test_files_route_blocks_traversal`: `/files/../pyproject.toml` returns 404
  - `test_second_run_conflicts`: 409
- [ ] **Steps 2–4:** Run (FAIL), implement, then PASS and ruff. Styling follows the editorial look in Global Constraints. It must be readable at 375 px width.
- [ ] **Step 5:** Commit "Tastemaker web page: brief, shots, keyframes and takes".

### Task 12: Docs and a real Modal smoke run

**Files:**
- Create: `README.md`, `CLAUDE.md` (commands and rules, ≤ 30 lines)
- Note: no project folder is committed.

- [ ] **Step 1:** Write the docs.
  - **README:** what the tool is; the two modes per decision; quick start (`new`, `modes`, `serve`, `run`); model config, for llama-server or a cloud model via `api_key_env`; costs (L4 ≈ $0.07 per draft clip); limits (text-only cast, so faces drift; songs need a steady pulse).
  - **CLAUDE.md:** commands; the rules "no theme words in the repo", "mechanical steps never branch on mode", and "all project JSON through write_json".
- [ ] **Step 2:** Run `uv run modal run celery_man/gen_modal.py::access`. Expected: both repos `OK`.
- [ ] **Step 3:** Run `uv run modal run celery_man/gen_modal.py::smoke`. This downloads the weights into the new Volume once, then makes 1 still and 1 clip, for about $0.30. Expected: `smoke/key.png` and `smoke/clip-L4.mp4`, with the clip 5.04 s long.
- [ ] **Step 4:** Commit "Document celery-man; verify Modal generation end to end".

### Task 13: Auto quick run on a new song (Danny, 2026-10-20: "Do a quick auto run to test things out with a new song")

Not committed to the repo; runs in a project folder outside it (`~/projects/celery-man-runs/<name>/`).

- [ ] **Step 1:** Synthesize a new original instrumental with numpy, a throwaway script in the run folder with no rights issues. It should be about 40 s, at a tempo other than 120 (e.g. 96 bpm, so 2-bar windows are 5 s and the 1-bar fallback is exercised), with a kick, a snare, bass and chords, and an intro, a verse and a lift so section detection has something to find.
- [ ] **Step 2:** The text model comes from llama.cpp `llama-server` on this machine's CPU, which has no NVIDIA GPU (the 3080 is another box). It runs a small open instruct GGUF (Qwen3-4B-Instruct-2507 Q4_K_M) on `127.0.0.1:8080`. Quick mode needs only the text role. The vision role is unused.
- [ ] **Step 3:**
  1. `celery-man new <dir> --song song.wav --source "original, synthesized for this test"`
  2. `celery-man modes <dir> brief=auto shots=auto keyframes=auto takes=auto`
  3. `celery-man quick <dir> --yes` (quick runs timing, brief and shots first if they're missing)
  4. Expected: `out/quick.mp4`, its length equal to the range, cuts on the detected downbeats, spend about $1.50.
- [ ] **Step 4:** Look at the contact sheets and frames, and write the findings into the morning report.
