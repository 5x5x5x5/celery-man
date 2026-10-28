# Tastemaker Front End Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Jinja tastemaker pages with a React front end. A person walks a song through Song → Brief → Shots → Keyframes → Takes → Cut, choosing Auto / Suggestions / Manual for each shot, with a rough cut always playable.

**Architecture:** The Python package stays the engine. Tasks 1–7 change the pipeline so that:
- each shot progresses on its own;
- modes can be set per shot;
- tastemaker take rounds are uncapped;
- uploads are pinned;
- assemble fills gaps;
- the model can offer suggestions, stored as candidates.

Tasks 8–10 turn `celery_man/web/` into a JSON API with one job runner per project (a paid queue plus a free pool). Tasks 11–15 build a Vite + React + TypeScript app in `web/`. Its build is served by `celery-man serve`.

**Tech Stack:** Python 3.12, FastAPI, uv, pytest, ruff (line length 140), ffmpeg, Pillow, Playwright (Python, already a dependency); Node 24, Vite, React 18, TypeScript, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-28-tastemaker-ui-design.md`

## Global Constraints

- No theme, song, story, style or cast words anywhere in the repo: code, UI copy, placeholders, fixtures. Tests use neutral fixtures ("a person walks", cast "A", place "P").
- Mechanical steps never branch on which mode wrote a decision file.
- All project JSON is written through `project.write_json`, or the new `project.update_json` (which uses `write_json`).
- The server binds to 127.0.0.1 only. The Host + same-origin middleware from today's `web/app.py` stays on every route.
- Never cut prompt text silently: `PromptTooLong` reaches the user as an error.
- Nothing spends without a price shown at the click. Every paid request carries the price the UI showed. The server refuses with 409 if its own price is more than $0.01 higher, or if the spend would break the reserve.
- The reserve is the quick fallback (`pipeline.quick_cost(p)`), and every spend keeps it.
- Auto shots keep `MAX_ROUND = 2` and the quick fallback. Tastemaker shots have no round cap.
- Uploads are never discarded automatically.
- Counts: 3 brief suggestions, 3 shot ideas per window, 4 keyframe stills per suggestion set, 3 takes per round (`SEEDS`).
- Uploaded images and clips are converted to 960×544, 24 fps.
- Upload limits: audio 100 MB, image 20 MB, clip 200 MB. Type is checked by content (Pillow / ffprobe), never by extension.
- Python: uv only, type hints on public functions, `logging` not `print`. Tests launching Chromium or a browser are `@pytest.mark.slow`.
- Commit messages: concise, imperative, about why. End each with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never `git add -A`; add named paths.

## Where this plan narrows the spec (planner's calls)

- **Editing prompts:** the composed prompt cannot be typed into directly. It is derived from the shot and brief (that is how staleness works), so "edit the prompt" means editing the shot's fields inline (Tasks 14, 9).
- **Generating one:** Manual "generate one" on Keyframes is `continue` for that shot (one still). On Takes it is a normal round of 3.
- **Uploads and the ledger:** uploads add no ledger rows. An `uploaded` flag in `keyframes.json` / `picks.json` does the job the spec gave the ledger (Task 5).
- **Suggestion code:** model-written scene code in a suggestion is not test-rendered when suggested. The live preview shows its errors, and a chosen one that fails to render waits in the render step like any failed scene (Task 7).

## Review Focus

1. **Upload of the wrong thing:** a text file named `.png`, a 0-byte file, a clip shorter than its window plus in-point, or a file over the limit. Expected: 4xx with a plain reason, and nothing written to `keys/`, `takes/`, `keyframes.json` or `picks.json`. Tests are in Task 5.
2. **Price moved between display and click:** another job spent, or the estimate rose. Expected: 409 with the new price, and no ledger rows added. Tests are in Task 4 and Task 9.
3. **A decision saved while a paid batch is writing the same file:** for example, approving s03's keyframe while stills for s05 land. Expected: both survive. Tests are in Task 1 (`update_json`) and Task 2.
4. **Range changed after shots are decided:**
   - Expected: refused with 409 unless `reset_shots` is sent.
   - With `reset_shots`, every window becomes open again; uploads stay on disk.

   Test is in Task 8.
5. **Hostile preview code:**
   - The case: custom scene code containing `</script>`, `fetch(...)`, or `parent.document`.
   - Expected: the preview document keeps the code inside its script, its CSP blocks network, and the iframe is sandboxed without same-origin.

   Tests are in Task 10 (Python, the HTML) and Task 12 (the iframe attribute).

---

### Task 1: Open shots, per-shot modes, locked JSON updates

**Files:**
- Modify: `celery_man/project.py`
- Modify: `celery_man/cli.py` (`_modes`)
- Test: `tests/test_project.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `OPEN = "open"`: a shot `kind` stored in `shots.json`, not in `KINDS`. An open shot has `action=""`, `cast=[]`, `place=None`, `framing=""`, `scene=None`.
  - `open_shot(sid: str, start: float, end: float, section: str) -> Shot`.
  - `Shot.is_open` (property): `kind == OPEN`.
  - `Project.mode(step: str, shot_id: str | None = None) -> str`: `shot_modes[shot_id][step]` if set, else `modes[step]`.
  - `Project.shot_modes -> dict` (config key `shot_modes`, default `{}`).
  - `update_json(p: Project, path: Path, fn: Callable[[Any], Any]) -> Any`: holds an exclusive `fcntl` lock on `root/decisions.lock`, re-reads `path` (`None` if missing), writes `fn(old)` via `write_json`, and returns it.
  - `load_footage` / `load_animation` already filter by kind, so open shots are excluded from both.

- [ ] **Step 1: Write the failing tests**

```python
def test_open_shot_round_trips():
    s = open_shot("s01", 0.0, 2.5, "intro")
    assert s.is_open and shot_from_dict(asdict(s)) == s


def test_open_shot_rejects_content():
    with pytest.raises(ValueError):
        shot_from_dict(asdict(open_shot("s01", 0, 1, "x")) | {"action": "a person walks"})


def test_mode_prefers_shot_override(tmp_path, song):
    p = Project.create(tmp_path / "p", song, "src")
    write_json(p.root / "project.json", p.config | {"shot_modes": {"s02": {"takes": "auto"}}})
    assert p.mode("takes") == "tastemaker" and p.mode("takes", "s02") == "auto" and p.mode("takes", "s03") == "tastemaker"


def test_update_json_serialises_writers(tmp_path, song):
    p = Project.create(tmp_path / "p", song, "src")
    path = p.root / "k.json"
    threads = [threading.Thread(target=update_json, args=(p, path, lambda d, i=i: (d or {}) | {str(i): i})) for i in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(read_json(path)) == 20


def test_cli_modes_per_shot(tmp_path, song):  # tests/test_cli.py
    # `celery-man modes DIR keyframes=auto s05.takes=auto`
    assert main(["modes", str(d), "keyframes=auto", "s05.takes=auto"]) == 0
    assert Project(d).mode("keyframes") == "auto" and Project(d).mode("takes", "s05") == "auto"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_project.py tests/test_cli.py -q`. Expected: the new tests FAIL on missing names.
- [ ] **Step 3: Implement** the Interfaces above.
  - `shot_from_dict` accepts `kind == OPEN` only with the empty field values listed, and `scene` must be `None`.
  - `_modes` in `cli.py` accepts `STEP=MODE` and `SID.STEP=MODE`. Per-shot steps are `shots`, `keyframes`, `takes`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all PASS.
- [ ] **Step 5: Commit**: "Let shots be open and modes be set per shot".

---

### Task 2: Per-shot progression through status and advance

**Files:**
- Modify: `celery_man/steps.py`, `celery_man/pipeline.py`, `celery_man/takes.py`, `celery_man/auto.py` (`write_shots`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `steps.ensure_open_shots(p: Project) -> None`: if `shots.json` is missing, or every shot in it is open and the windows changed, write one open shot per window, stamped `shots_inputs`. `run_timing` calls it after writing timing (once a brief exists; with no brief, it writes with `inputs=""` and `status` treats that as fresh-for-open).
  - `steps.Run.only: set[str] | None = None`: when set, the shots, keyframes, takes and render runners act only on those shot ids.
  - `auto.write_shots(chat_fn, brief, windows, kinds, ids: list[str] | None = None)`: `ids` defaults to `s01..sNN`. Given ids are used as-is, so a subset of windows keeps its ids.
  - `steps.run_shots(run)`:
    - Shots file fresh: auto-writes only the open shots whose `p.mode("shots", sid) == "auto"` (and in `run.only`, if set), merges them via `update_json`, and settles custom code as today.
    - Shots file stale: behaves as today, using the step-level mode.
  - `pipeline.status(p)` with open shots:
    - `shots`: done when no open shots and fresh; `waiting` when every remaining open shot is tastemaker; `missing` when any open shot is auto.
    - `render`, `keyframes`, `takes`: computed over decided shots only, with `wait_on` per shot via `p.mode(step, sid)`.
    - `_kind_problem` skips open shots.
  - `pipeline.advance(...)`:
    - A `waiting` state on `shots`, `render`, `keyframes` or `takes` no longer ends the call. It moves to the next step that is not done and not waiting.
    - `timing` or `brief` waiting still returns.
    - `assemble` runs once every other step is done or waiting.
  - `run_keyframes` and `run_takes`:
    - Auto judging and rewrites are per shot (`p.mode("keyframes"|"takes", s.id) == "auto"`).
    - A vision chat is requested only if some shot needs it.
    - Every write to `keyframes.json`, `picks.json` and `shots.json` goes through `update_json`, which merges the one shot's entry.

- [ ] **Step 1: Write the failing tests** (fixtures from `tests/test_pipeline.py`: `make`, `text_chat`, `vision_chat`, `FakeGenerator`)

```python
def test_timing_writes_open_shots(tmp_path, song):
    p = make(tmp_path, song, DEFAULT_MODES)
    advance(p, FakeGenerator())
    assert all(s.is_open for s in p.load_shots()) and len(p.load_shots()) == len(windows(p))


def test_decided_shot_gets_keyframe_while_others_open(tmp_path, song):
    # brief saved; s01 decided as footage, the rest open; keyframes=auto for s01 only
    advance(p, FakeGenerator(), vision_chat=vision_chat())
    kfs = read_json(p.keyframes_path)
    assert set(kfs) == {"s01"} and status_of(p, "shots").state == "waiting"


def test_auto_window_filled_tastemaker_left_open(tmp_path, song):
    # shot_modes {"s02": {"shots": "auto"}}, step mode tastemaker
    advance(p, FakeGenerator(), chat_text=text_chat)
    shots = {s.id: s for s in p.load_shots()}
    assert not shots["s02"].is_open and shots["s01"].is_open


def test_only_limits_runners(tmp_path, song):
    # two decided footage shots, keyframes=tastemaker
    advance(p, FakeGenerator(), only={"s01"})  # new kwarg, passed to Run
    assert set(read_json(p.keyframes_path)) == {"s01"}


def test_concurrent_keyframe_write_survives(tmp_path, song):
    # a FakeGenerator subclass whose stills() calls update_json(p, p.keyframes_path, approve "s02") mid-batch
    advance(p, gen)
    assert read_json(p.keyframes_path)["s02"]["approved"] is True
```

- [ ] **Step 2: Run** `uv run pytest tests/test_pipeline.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the Interfaces above. `advance` gains `only: set[str] | None = None`, passed into `steps.Run`. Keep each runner's existing ledger and reservation behaviour unchanged.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all PASS, including the old pipeline, budget and end-to-end tests. Old tests that write a full shot list keep working because a full list has no open shots.
- [ ] **Step 5: Commit**: "Let each shot move ahead while other windows are still open".

---

### Task 3: No round cap for tastemaker takes

**Files:**
- Modify: `celery_man/takes.py`, `celery_man/steps.py` (`write_report`)
- Test: `tests/test_pipeline.py`, `tests/test_budget.py`

**Interfaces:**
- Produces: `takes.round_cap(p: Project, sid: str) -> int | None`: `MAX_ROUND` when `p.mode("takes", sid) == "auto"`, else `None`. `take_plan`, `_no_usable_take` and `run_takes` use it in place of `MAX_ROUND`.
  - With no cap, `limit` and `fallback` never occur for that shot.
  - The `edit` rule (a new round needs a changed prompt) still applies.
  - The reserve in `run_takes` still counts the shot.

- [ ] **Step 1: Write the failing tests**

```python
def test_tastemaker_round_three_runs(tmp_path, song):
    # tastemaker shot rejected in rounds 1 and 2, shot edited after each rejection
    assert take_plan(...).need == "run" and take_plan(...).round == 3


def test_auto_round_three_is_fallback(tmp_path, song):
    # same history with takes=auto for that shot
    assert take_plan(...).need in ("limit", "fallback")


def test_unusable_round_two_tastemaker_rejected_not_quick(tmp_path, song):
    # round-2 takes all unreadable, tastemaker shot
    assert read_json(p.picks_path)["s01"]["rejected"] is True and not read_json(p.picks_path)["s01"].get("quick")
```

- [ ] **Step 2: Run** `uv run pytest tests/test_pipeline.py tests/test_budget.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `round_cap` and use it in the three places. In `write_report`, "(round N, forced)" applies only to auto shots.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit**: "Let a tastemaker keep paying for take rounds".

---

### Task 4: Prices, the reserve, and the price check

**Files:**
- Create: `celery_man/spend.py`
- Modify: `celery_man/pipeline.py` (`estimate`)
- Test: `tests/test_spend.py`

**Interfaces:**
- Consumes: `pipeline.estimate`, `pipeline.quick_cost`, `ledger.cost`, the `steps` constants.
- Produces:
  - `pipeline.estimate(p, only: set[str] | None = None) -> float`.
  - `spend.prices(p: Project) -> dict[str, float]`. Keys:
    - `continue`: `estimate(p)`;
    - `still`: `cost(1, EST_STILL_S, STILL_GPU)`;
    - `keyframe_set`: `cost(4, EST_STILL_S, STILL_GPU)`;
    - `take_round`: `cost(len(SEEDS) + 1, EST_WALL_S, TAKE_GPU)`.
  - `spend.summary(p: Project) -> dict`: `{"spent": float, "reserve": float, "cap": float}`, where `reserve = pipeline.quick_cost(p)`.
  - `class PriceChanged(Exception)` with `.price: float`.
  - `class OverReserve(Exception)` with `.reason: str`.
  - `spend.check(p: Project, shown: float, now: float) -> None`:
    - raises `PriceChanged(now)` if `now > shown + 0.01`;
    - raises `OverReserve` if `spent + now + reserve > cap`.

- [ ] **Step 1: Write the failing tests**

```python
def test_check_refuses_risen_price(tmp_path, song):
    with pytest.raises(PriceChanged) as e:
        spend.check(p, shown=0.10, now=0.12)
    assert e.value.price == 0.12


def test_check_allows_cent_of_slack(tmp_path, song):
    spend.check(p, shown=0.10, now=0.105)


def test_check_keeps_reserve(tmp_path, song):
    # cap set so that spent + now fits but spent + now + quick_cost does not
    with pytest.raises(OverReserve):
        spend.check(p, shown=1.0, now=1.0)


def test_estimate_only_counts_named_shots(tmp_path, song):
    assert estimate(p, only={"s01"}) < estimate(p)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_spend.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `spend.py` and the `only` argument.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit**: "Price every UI spend and keep the fallback reserve".

---

### Task 5: Pinned uploads

**Files:**
- Create: `celery_man/uploads.py`
- Modify: `celery_man/steps.py` (`kf_need`, `run_keyframes`, `write_provenance`), `celery_man/takes.py` (`pick_ok`, `take_plan`), `celery_man/pipeline.py` (`status` keyframes)
- Test: `tests/test_uploads.py`

**Interfaces:**
- Consumes: Task 1 `update_json`, `generate.W/H/FPS`, `compose.prompt_sha`.
- Produces:
  - `class UploadError(ValueError)`: the message is shown to the user.
  - `uploads.import_keyframe(p: Project, sid: str, data: bytes) -> dict`. It:
    - checks with Pillow (`Image.open` + `verify`, then reopen) that `data` is PNG or JPEG of at most 20 MB;
    - centre-crops and resizes it to 960×544 and writes `keys/<sid>-<sha12>.png`;
    - records `keyframes.json[sid] = {"path", "seed": 0, "prompt_sha": <current prompt_sha>, "approved": True, "note": "uploaded", "uploaded": True}` and returns that entry.
  - `uploads.import_take(p: Project, sid: str, data: bytes, in_point: float = 0.0) -> dict`. It:
    - checks with ffprobe that `data` is a video of at most 200 MB;
    - converts it with ffmpeg (scale + crop to 960×544, `fps=24`, h264, no audio) into `takes/upload-<sha12>.mp4`;
    - requires `duration >= in_point + window length` (else `UploadError`);
    - writes `picks.json[sid] = {"key": "upload-<sha12>", "clip": "takes/upload-<sha12>.mp4", "in": in_point, "round": 0, "gen": 0, "quick": False, "uploaded": True, "shot_sha": <sha12 of the shot's asdict>, "prompt_sha": <prompt_sha>}`.
  - `uploads.changed_since_upload(p: Project, shot: Shot, brief: Brief, entry: dict | None) -> bool`: true when an `uploaded` keyframe's `prompt_sha`, or an uploaded pick's `prompt_sha`, no longer matches.
  - `kf_need` returns `"done"` for an `uploaded` entry whose file exists, whatever its `prompt_sha`.
  - `pick_ok` returns true for an `uploaded` pick whose clip exists.
  - `take_plan` checks the pick before the keyframe, so an uploaded take is `done` without a keyframe.
  - `run_keyframes` and keyframe `status` skip shots with an uploaded pick.
  - `write_provenance` lists uploaded keyframes and takes as "supplied by the tastemaker". Uploads add no ledger rows: the `uploaded` flag replaces the ledger check the spec mentions.

- [ ] **Step 1: Write the failing tests**

```python
def test_keyframe_upload_pinned_through_brief_edit(tmp_path, song):
    import_keyframe(p, "s01", png_bytes(1200, 800))
    edit_brief_style(p)
    assert kf_need(p, shot, p.load_brief(), read_json(p.keyframes_path)["s01"]) == "done"
    assert changed_since_upload(p, shot, p.load_brief(), read_json(p.keyframes_path)["s01"])


def test_keyframe_upload_is_960x544(tmp_path, song):
    e = import_keyframe(p, "s01", png_bytes(500, 500))
    assert Image.open(p.root / e["path"]).size == (960, 544)


@pytest.mark.parametrize("data", [b"", b"not an image", b"\x89PNG\r\n\x1a\n" + b"0" * 10])
def test_bad_keyframe_upload_writes_nothing(tmp_path, song, data):
    with pytest.raises(UploadError):
        import_keyframe(p, "s01", data)
    assert not p.keyframes_path.exists() and not any(p.keys_dir.glob("*")) if p.keys_dir.exists() else True


def test_take_upload_without_keyframe_is_done(tmp_path, song):
    import_take(p, "s01", mp4_bytes(seconds=5))
    assert take_plan(p, Ledger(p.ledger_path), shot, brief, None, read_json(p.picks_path)["s01"]).need == "done"


def test_short_take_upload_refused(tmp_path, song):
    with pytest.raises(UploadError, match="needs"):
        import_take(p, "s01", mp4_bytes(seconds=1), in_point=0.5)
    assert not p.picks_path.exists()
```

- [ ] **Step 2: Run** `uv run pytest tests/test_uploads.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `uploads.py` and the five hooks listed.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit**: "Let a tastemaker supply keyframes and takes that never go stale".

---

### Task 6: Assemble at any time, filling gaps

**Files:**
- Create: `celery_man/cut.py`
- Modify: `celery_man/steps.py` (`run_assemble`, `write_report`), `celery_man/pipeline.py` (assemble status), `celery_man/assemble.py` (filler clip makers)
- Test: `tests/test_cut.py`

**Interfaces:**
- Consumes: `takes.take_plan`, `renders.render_need`, `steps.kf_need`, Task 5 upload rules.
- Produces:
  - `cut.cut_plan(p: Project) -> list[dict]`: one item per window, in order: `{"shot", "kind", "src", "text", "in", "start", "end"}`. `kind` is the first that applies:
    1. `"take"`: footage whose take plan is `done`; `src` is the pick's clip, `in` its in-point.
    2. `"render"`: animation whose `render_need` is `done`; `src` is the clip, `in` 0.
    3. `"still"`: footage with an approved keyframe; `src` is the PNG.
    4. `"card"`: a decided shot with neither; `text` is the shot's action.
    5. `"blank"`: an open window.

    `src` is relative to the project root, `None` for card and blank.
  - `assemble.still_clip(png: Path, seconds: float, out: Path) -> Path`: an ffmpeg `zoompan` push-in from 1.0 to 1.06 at 960×544 / 24 fps.
  - `assemble.card_clip(text: str, look: dict, seconds: float, out: Path) -> Path`:
    - fills with `look.background` and draws `text` in `look.colors[0]` with the look's typeface TTF from `celery_man/scenes/fonts/`;
    - an empty `text` gives a blank card;
    - uses ffmpeg `color` + `drawtext`, with the text passed through `textfile=`, never inline, so quotes and colons are safe.
  - Filler clips are cached in `fill/<sha12 of item+look>.mp4`.
  - `run_assemble` builds cuts from `cut_plan`, making fillers as needed. The output goes to a unique temp name (`video.<pid>-<ns>.tmp.mp4`), then `os.replace`.
  - `write_report` adds "## Filled windows" listing still / card / blank items.
  - Assemble status is `done` when `video.mp4` is newer than `picks.json`, `renders.json`, `keyframes.json` and `shots.json`.

- [ ] **Step 1: Write the failing tests**

```python
def test_cut_plan_kinds(tmp_path, song):
    # s01 picked take, s02 animation rendered, s03 approved still only, s04 decided no keyframe, s05 open
    assert [c["kind"] for c in cut_plan(p)][:5] == ["take", "render", "still", "card", "blank"]


def test_assemble_with_gaps_makes_full_length_video(tmp_path, song):
    run_assemble(Run(p, FakeGenerator()))
    assert abs(probe_duration(p.out_dir / "video.mp4") - (windows(p)[-1][1] - windows(p)[0][0])) < 0.1


def test_card_text_with_quotes_and_colons(tmp_path):
    card_clip("a: 'b' \"c\" 50%", DEFAULT_LOOK, 1.0, tmp_path / "c.mp4")
    assert probe_duration(tmp_path / "c.mp4") >= 1.0


def test_stale_pick_falls_back_to_still(tmp_path, song):
    # pick made, then shot action edited and keyframe re-approved
    assert cut_plan(p)[0]["kind"] == "still"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_cut.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the Interfaces above.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit**: "Assemble a draft at any time, filling unfinished windows".

---

### Task 7: Suggestions as candidates

**Files:**
- Create: `celery_man/suggest.py`
- Modify: `celery_man/auto.py` (lift the per-shot reply parsing out of `write_shots`; add the suggest functions), `celery_man/steps.py` (split `_stills`)
- Test: `tests/test_suggest.py`, `tests/test_auto.py`

**Interfaces:**
- Consumes: Tasks 1, 4, 5.
- Produces:
  - `auto.shot_from_reply(sid: str, window: tuple[float, float, str], d: dict, brief: Brief, kinds: list[str], codes: dict[str, str]) -> Shot`: today's `one()`/`animation()`, moved out unchanged. For code, the scene file is `scenes/<sid>.js`.
  - `auto.suggest_briefs(chat_fn, seed: str, steer: str, n: int = 3) -> list[Brief]`: one call returning a JSON list of `n` briefs, validated with `brief_from_dict(_lenient_brief(...))`.
  - `auto.suggest_shots(chat_fn, brief, sid: str, window, kinds, neighbours: list[Shot], steer: str, n: int = 3) -> list[tuple[Shot, str | None]]`:
    - one call, `n` ideas for one window, each parsed by `shot_from_reply`;
    - when `kinds` holds both, the system prompt asks for at least one of each kind;
    - returns `(shot, code or None)` per idea.
  - `steps.gen_stills(run: Run, jobs: list[tuple[str, str, int]]) -> list[Path | None]`:
    - takes `(shot_id, prompt, seed)` jobs;
    - does today's budget check, reservation, files and ledger rows, without touching `keyframes.json`;
    - `_stills` becomes a wrapper that records the entries.
  - Candidate store `candidates/` (all via `update_json`):
    - `brief.json`: `{"steer": str, "items": [{"id", "brief"}]}`;
    - `shots/<sid>.json`: `{"steer", "note", "items": [{"id", "shot", "code", "still", "error"}]}`;
    - `keyframes/<sid>.json`: `{"items": [{"id", "path", "seed", "prompt_sha", "flag"}]}`.

    An `id` is `sha12` of the item.
  - `suggest.briefs(run: Run, steer: str) -> None` (free).
  - `suggest.shots(run: Run, sid: str, steer: str) -> None`:
    - free text call, then a paid `gen_stills` for the footage ideas;
    - a footage idea whose `compose` raises `PromptTooLong` keeps `error` and gets no still.
  - `suggest.keyframes(run: Run, sid: str, near_seed: int | None = None) -> None`:
    - 4 stills, seeds `base+1..base+4`;
    - `base` is the highest seed used for the shot so far, or `near_seed * 10` for Variations;
    - when `p.config.get("vision_flags")` is true, each item gets `flag = why` from `auto.check_keyframe` if it returns not-ok.
  - `suggest.use_brief(p, cid: str) -> None`, `suggest.use_shot(p, sid: str, cid: str) -> None` (writes the scene file for code, stamps `shots_inputs`), `suggest.use_keyframe(p, sid: str, cid: str) -> None` (writes the approved entry).
  - `suggest.reopen_note(p, sid: str, note: str) -> None`: "none of these" on keyframes. It stores `note` in `candidates/shots/<sid>.json`, which becomes the default steer.

- [ ] **Step 1: Write the failing tests** (fake chats return fixed JSON with neutral content)

```python
def test_write_shots_unchanged_after_refactor(): ...  # the existing test_auto write_shots tests still pass untouched


def test_suggest_briefs_three():
    assert len(auto.suggest_briefs(fake_chat(three_briefs), "", "")) == 3


def test_suggest_shots_mixed_kinds(tmp_path, song):
    ideas = auto.suggest_shots(fake_chat(mixed), brief, "s03", w, ["footage", "animation"], [], "")
    assert {s.kind for s, _ in ideas} == {"footage", "animation"} and all(s.id == "s03" for s, _ in ideas)


def test_shot_suggestions_get_paid_stills(tmp_path, song):
    suggest.shots(Run(p, FakeGenerator(), text_chat=fake_chat(mixed)), "s03", "")
    items = read_json(p.root / "candidates/shots/s03.json")["items"]
    assert all((i["still"] is not None) == (i["shot"]["kind"] == "footage") for i in items)
    assert sum(r["model"] == STILL_MODEL for r in Ledger(p.ledger_path).rows()) == sum(i["shot"]["kind"] == "footage" for i in items)


def test_keyframe_suggestions_four_new_seeds(tmp_path, song):
    suggest.keyframes(run, "s01")
    suggest.keyframes(run, "s01")
    seeds = [i["seed"] for i in read_json(p.root / "candidates/keyframes/s01.json")["items"]]
    assert len(seeds) == 8 and len(set(seeds)) == 8


def test_use_keyframe_approves(tmp_path, song):
    suggest.use_keyframe(p, "s01", cid)
    assert read_json(p.keyframes_path)["s01"]["approved"] is True


def test_suggestions_never_touch_decisions(tmp_path, song):
    before = {f: f.read_bytes() for f in [p.shots_path, p.brief_path] if f.exists()}
    suggest.briefs(run, "")
    suggest.shots(run, "s02", "")
    assert before == {f: f.read_bytes() for f in before}
```

- [ ] **Step 2: Run** `uv run pytest tests/test_suggest.py tests/test_auto.py -q`. Expected: the new tests FAIL; the old auto tests still PASS.
- [ ] **Step 3: Implement** the Interfaces above.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit**: "Let the model offer alternatives without deciding".

---

### Task 8: API core: projects, state, jobs, files

**Files:**
- Rewrite: `celery_man/web/app.py`
- Create: `celery_man/web/jobs.py`, `celery_man/web/state.py`
- Delete: `celery_man/web/pages.py`, `celery_man/web/templates/`, `celery_man/web/static/`, `tests/test_web.py`
- Modify: `celery_man/cli.py` (`_serve`), `.gitignore` (`celery_man/web/dist/`)
- Test: `tests/test_api_core.py`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces:
  - `jobs.Jobs(p: Project)`:
    - `.paid(name: str, fn: Callable[[], object]) -> str` (job id; FIFO, one worker thread, each job holding `run_lock`);
    - `.free(name, fn) -> str` (`ThreadPoolExecutor(max_workers=4)`);
    - `.snapshot() -> dict` = `{"running": Job | None, "queued": [Job], "free": [Job], "errors": [{"id", "name", "error"}] (last 10)}`, where `Job = {"id", "name", "started"}`.

    A job's exception is logged and recorded and never kills the worker.
  - `state.project_state(p: Project, jobs: Jobs) -> dict`: the shape below. It is the TypeScript `State` in Task 11, so keep field names identical.

```
{ "project": {id, song, song_source, range, kinds, cap_usd, modes, shot_modes, seed, vision_flags},
  "timing": {duration, windows: [{id, start, end, section}]} | null,
  "brief": <brief dict> | null,
  "shots": [{ "shot": <shot dict>, "open": bool,
              "modes": {shots, keyframes, takes},
              "render": {need, clip, poster, error} | null,
              "keyframe": {need, entry, changed_since_upload} | null,
              "takes": {need, round, cap, takes: [{key, clip, sheet, seed}], pick, changed_since_upload} | null }],
  "steps": [{step, state, detail}],
  "spend": {spent, reserve, cap}, "prices": {continue, still, keyframe_set, take_round},
  "candidates": {brief: {...}|null, shots: {sid: {...}}, keyframes: {sid: {...}}},
  "jobs": <Jobs.snapshot()>, "cut": <cut_plan(p)> }
```

  - `create_app(root: Path, gen_factory, chat_factory) -> FastAPI`:
    - if `root/project.json` exists, the single project is served with id = folder name;
    - otherwise each child folder holding `project.json` is a project;
    - one `Jobs` per project, created lazily.
  - Routes:
    - `GET /api/projects` → `[{id, song, kinds}]`.
    - `POST /api/projects` (multipart `name`, `song`, `source`, `kinds`):
      - `name` must match `^[A-Za-z0-9_-]{1,64}$`, else 422; an existing folder → 409;
      - the song is checked with ffprobe as audio and is at most 100 MB;
      - creates the project, then queues a free `timing` job (`run_timing`);
      - returns `{id}`.
    - `GET /api/projects/{pid}/state`.
    - `PATCH /api/projects/{pid}` with any of `{range, kinds, seed, vision_flags, reset_shots}`:
      - a range or kinds change with any decided shot and no `reset_shots` → 409;
      - with `reset_shots`, `shots.json` is rewritten all open.
    - `PUT /api/projects/{pid}/shot-modes` `{shots: [sid], step, mode}`. With `shots: []`, it sets the step default in `modes`.
    - `GET /api/projects/{pid}/files/{path}` (today's traversal guard).
    - `GET /` and the SPA assets from `celery_man/web/dist`. A missing dist returns 503 with the text "front end not built: npm --prefix web ci && npm --prefix web run build".
  - The `same_origin` middleware from today's app is kept as is.
  - `cli._serve` takes `ROOT` (a project folder or a folder of projects).

- [ ] **Step 1: Write the failing tests** (`TestClient(app, base_url="http://127.0.0.1:8765")`)

```python
def test_lists_folder_of_projects(tmp_path, song): ...  # two projects → two ids
def test_single_project_folder_served(tmp_path, song): ...  # id == folder name
def test_create_project_runs_timing(tmp_path, song):
    r = c.post("/api/projects", data={"name": "one", "source": "s", "kinds": "footage,animation"}, files={"song": song.open("rb")})
    wait_jobs(c, "one")
    st = c.get("/api/projects/one/state").json()
    assert st["timing"]["windows"] and all(s["open"] for s in st["shots"])


def test_create_rejects_non_audio(tmp_path): ...  # files={"song": ("a.mp3", b"text")} → 422, no folder left behind
def test_create_rejects_bad_name(tmp_path, song): ...  # name "../x" → 422
def test_range_change_with_decided_shots_refused(tmp_path, song):
    assert c.patch(u, json={"range": [0, 8]}).status_code == 409
    assert c.patch(u, json={"range": [0, 8], "reset_shots": True}).status_code == 200


def test_cross_site_post_refused(tmp_path, song): ...  # Origin http://evil.example → 403
def test_job_error_recorded_and_worker_survives(tmp_path, song): ...  # failing paid job, then a good one runs
def test_state_shape_keys(tmp_path, song):
    assert set(st) == {"project", "timing", "brief", "shots", "steps", "spend", "prices", "candidates", "jobs", "cut"}


def test_files_blocks_traversal(tmp_path, song): ...  # /files/../x → 404
```

- [ ] **Step 2: Run** `uv run pytest tests/test_api_core.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the Interfaces above. Delete the old pages, templates, static files and `tests/test_web.py`.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check`. Expected: PASS and clean.
- [ ] **Step 5: Commit**: "Turn the web page into a JSON API with a job runner per project".

---

### Task 9: API decisions: brief, shots, keyframes, takes, continue, cut

**Files:**
- Create: `celery_man/web/routes.py` (an `APIRouter` included by `app.py`)
- Test: `tests/test_api_decisions.py`

**Interfaces:**
- Consumes: Tasks 4–8.
- Produces: all routes under `/api/projects/{pid}`. Paid routes take a JSON body with `price`, check it with `spend.check(p, price, <server price>)`, then queue `jobs.paid`. They return 202 `{job}`, 409 `{"price": new}` on `PriceChanged`, and 409 `{"reason"}` on `OverReserve`. Validation errors → 422 `{"error"}`; `PromptTooLong` → 422 with its message.

| Route | Body | Does | Paid |
|---|---|---|---|
| `PUT /brief` | `{brief}` | `brief_from_dict`, `save_brief(..., brief_inputs(p))` | no |
| `POST /suggest/brief` | `{steer}` | free job `suggest.briefs` | no |
| `POST /brief/use` | `{id}` | `suggest.use_brief` | no |
| `PUT /shots/{sid}` | `{shot}` | validate (`shot_from_dict`, kind in `p.kinds`, id/window unchanged, `compose`), merge via `update_json`, stamp `shots_inputs` | no |
| `POST /suggest/shots` | `{shots: [sid], steer, price}` | one paid job running `suggest.shots` per sid; price = `prices.still × footage ideas` estimated as `3 × len(shots)` | yes |
| `POST /shots/{sid}/use` | `{id}` | `suggest.use_shot` | no |
| `POST /suggest/keyframes/{sid}` | `{near_seed?, price}` | paid `suggest.keyframes`; price `keyframe_set` | yes |
| `POST /keyframes/{sid}/use` | `{id}` | `suggest.use_keyframe` | no |
| `POST /keyframes/{sid}/none` | `{note}` | `suggest.reopen_note`; clears the keyframe candidates | no |
| `POST /uploads/keyframe/{sid}` | multipart `file` | `uploads.import_keyframe`; `UploadError` → 422 | no |
| `POST /takes/{sid}/pick` | `{key, in}` | `in` clamped-checked to `[0, clip duration − window]` else 422; writes pick (`round`, `gen` from the plan) | no |
| `POST /takes/{sid}/reject` | `{reason, shot?}` | writes the rejected pick; if `shot` is given, saves it as `PUT /shots` does, else queues a free job running `auto.rewrite_shot` and saving the result | no |
| `POST /uploads/take/{sid}` | multipart `file`, `in` | `uploads.import_take` | no |
| `POST /continue` | `{price, only?: [sid]}` | paid `advance(p, gen, chats, confirm=lambda c: c <= price + 0.01, only=set(only) or None)` | yes |
| `POST /assemble` | `{}` | free job `run_assemble` | no |

- [ ] **Step 1: Write the failing tests** (use FakeGenerator and fake chats; `wait_jobs` polls state until no jobs)

```python
def test_put_shot_rejects_window_change(...): assert c.put(f"{u}/shots/s01", json={"shot": s | {"end": 9}}).status_code == 422
def test_put_shot_too_long_prompt_422(...): ...  # long action → 422 with "PromptTooLong"-derived message
def test_continue_price_rise_refused_nothing_spent(...):
    rows = len(Ledger(p.ledger_path).rows())
    r = c.post(f"{u}/continue", json={"price": 0.0})
    assert r.status_code == 409 and r.json()["price"] > 0 and len(Ledger(p.ledger_path).rows()) == rows
def test_continue_only_one_shot(...): ...  # only=["s01"] → keyframes.json has s01 only
def test_suggest_keyframes_then_use(...): ...  # 4 candidates, use one → approved
def test_pick_in_point_out_of_range_422(...): ...  # in = 99 → 422
def test_reject_with_edited_shot_skips_model(...): ...  # chat fake asserts it is never called
def test_upload_bad_keyframe_422_nothing_written(...): ...
def test_assemble_route_makes_video(...): ...  # open windows present; out/video.mp4 exists
def test_decision_while_paid_job_runs(...):
    # a slow FakeGenerator holds the paid worker; PUT /shots/s02 returns 200 and is in shots.json before the job ends
```

- [ ] **Step 2: Run** `uv run pytest tests/test_api_decisions.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `routes.py` as tabled.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check`. Expected: PASS.
- [ ] **Step 5: Commit**: "Expose every tastemaker decision through the API".

---

### Task 10: Scene preview document and frame data

**Files:**
- Create: `celery_man/web/preview.py`
- Modify: `celery_man/scenes/runtime.js` (two small changes below), `celery_man/web/routes.py`
- Test: `tests/test_preview.py`

**Interfaces:**
- Produces:
  - `preview.preview_html(source: str, params: dict, look: dict, seed: int) -> str`. A self-contained document with:
    - CSP meta `default-src 'none'; script-src 'unsafe-inline'; font-src data:; img-src data:; style-src 'unsafe-inline'`;
    - the four fonts as `data:` URLs in `window.__FONTS = {serif: ..., ...}`;
    - the scene code inline, with every `</` written as `<\/`;
    - `runtime.js` inline;
    - a message bridge. `message {type: "init", payload}` calls `__init`. `{type: "frame", i, f}` calls `window.draw` directly, so there is no JPEG encode and the frame limit is not applied. It posts `{type: "error", message}` to the parent on any error.
  - `runtime.js` changes:
    - (1) the font URL is `(window.__FONTS || {})[file] || "fonts/" + file + ".ttf"`;
    - (2) `__init` loads `scene.js` only when `setup` and `draw` are not already defined.

    The `runtime_sha` change re-renders existing clips once. That is expected and free.
  - `GET /api/projects/{pid}/preview?sid=&candidate=` returns `text/html`: the shot's scene (or the candidate's, template or code) with the project look. The front end puts it in `srcdoc`.
  - `GET /api/projects/{pid}/frames?start=&end=` returns `features.frame_data(timing, feats, start, end, FPS)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_preview_escapes_script_close():
    html = preview_html("window.x='</script><script>alert(1)</script>'", {}, DEFAULT_LOOK, 1)
    assert "</script><script>alert(1)" not in html and html.count("<script") == html.count("</script>")

def test_preview_csp_blocks_network():
    assert "default-src 'none'" in preview_html("", {}, DEFAULT_LOOK, 1) and "connect-src" not in preview_html("", {}, DEFAULT_LOOK, 1)

def test_frames_route_counts(...): assert len(r.json()) == round(2.5 * 24)

@pytest.mark.slow
def test_preview_draws_and_fetch_blocked(tmp_path):
    # Playwright: load preview_html(scene that fetch()es then draws) via page.set_content, post init + one frame;
    # the canvas is not blank and an error message mentioning the blocked request is posted
```

- [ ] **Step 2: Run** `uv run pytest tests/test_preview.py -q -m "slow or not slow"`. Expected: FAIL.
- [ ] **Step 3: Implement** `preview.py`, the `runtime.js` changes and the two routes.
- [ ] **Step 4: Run** `uv run pytest -q -m "slow or not slow"`. Expected: PASS, including the existing animate render tests.
- [ ] **Step 5: Commit**: "Serve a sealed scene preview the browser can run live".

---

### Task 11: Front-end scaffold, state, Song step

**Files:**
- Create:
  - `web/` with `package.json`, `vite.config.ts` (`build.outDir: ../celery_man/web/dist`; dev proxy `/api` → `http://127.0.0.1:8765`), `tsconfig.json`, `index.html`;
  - `web/src/main.tsx`, `web/src/api.ts`, `web/src/types.ts`, `web/src/useProject.ts`;
  - `web/src/App.tsx`, `web/src/StepStrip.tsx`, `web/src/SpendMeter.tsx`, `web/src/JobsBadge.tsx`;
  - `web/src/steps/Song.tsx`, `web/src/Waveform.tsx`, `web/src/theme.css`.
- Test: `web/src/*.test.ts` (Vitest)

**Interfaces:**
- Consumes: the Task 8 / 9 / 10 routes and the `State` shape.
- Produces:
  - `types.ts`: `State` and its parts, field-for-field with Task 8's shape.
  - `api.ts`:
    - one typed function per route, e.g. `getState(pid)`, `putShot(pid, sid, shot)`, `continueRun(pid, price, only?)`;
    - each paid call returns `{ok: true} | {ok: false, price?: number, reason?: string}`, never throws on 409.
  - `useProject(pid): {state: State | null, refresh(): void}`: polls `GET /state` every 1500 ms while any job runs, else every 5000 ms, and immediately after any mutation.
  - `App`: hash routes `#/` (project list + "New") and `#/p/<pid>/<step>`, where step ∈ `song|brief|shots|keyframes|takes|cut`. `StepStrip` shows each step's state from `state.steps`.
  - `Waveform`:
    - draws peaks from decoding the song in the browser (`AudioContext.decodeAudioData`, downsampled to 1 peak per pixel);
    - shows `timing.windows` boundaries;
    - supports a draggable range selection (Song step).
  - Song step:
    - create form (name, song file, source, kinds checkboxes);
    - for an existing project: range on the waveform (PATCH; on 409 a confirm dialog naming that decided shots will reopen, then PATCH with `reset_shots`), plus a kinds toggle.
  - Design: light editorial. Cream background, ink text, a serif for headings, one teal accent (`#2a6f6f`, as in the mockups). Colours live as CSS variables on `:root`.

- [ ] **Step 1: Write the failing tests** (`npm --prefix web test`)

```ts
test("paid call maps 409 price", async () => {
  mockFetch(409, { price: 0.42 });
  expect(await continueRun("p", 0.1)).toEqual({ ok: false, price: 0.42 });
});
test("poll interval fast while jobs run", () => {
  expect(pollMs({ running: job, queued: [], free: [], errors: [] })).toBe(1500);
  expect(pollMs({ running: null, queued: [], free: [], errors: [] })).toBe(5000);
});
```

- [ ] **Step 2: Run** `npm --prefix web test`. Expected: FAIL.
- [ ] **Step 3: Implement** the scaffold. Use React 18.3.1, Vite 5, TypeScript 5, Vitest 2, with exact versions pinned in `package.json`.
- [ ] **Step 4: Run** `npm --prefix web test && npm --prefix web run build && uv run pytest tests/test_api_core.py -q`. Expected: PASS, and `celery_man/web/dist/index.html` exists.
- [ ] **Step 5: Commit**: "Start the front end: projects, state polling, the song step".

---

### Task 12: Shared pieces: mode switch, suggestions, priced buttons, scene preview, song clock

**Files:**
- Create: `web/src/ModeSwitch.tsx`, `web/src/Suggestions.tsx`, `web/src/PricedButton.tsx`, `web/src/ScenePreview.tsx`, `web/src/clock.ts`, `web/src/ShotStrip.tsx`
- Test: `web/src/clock.test.ts`, `web/src/ScenePreview.test.tsx`, `web/src/PricedButton.test.tsx`

**Interfaces:**
- Produces:
  - `ModeSwitch({value: "auto" | "suggestions" | "manual", onChange})`:
    - `auto` maps to API mode `auto`; `suggestions` and `manual` are both `tastemaker`;
    - the choice between those two is a UI-only view kept per shot in `localStorage` (wrapped in try/catch).
  - `Suggestions({items, render(item), onUse(id), onEdit?(id), steer, onSteer(text)})`: the card grid plus the steer box.
  - `PricedButton({label, price, onConfirm(): Promise<PaidResult>, disabledReason?})`:
    - always shows `label · $x.xx`;
    - on `{ok:false, price}` it updates to the new price and asks once more;
    - on `reason` it shows the reason and disables itself.
  - `clock.ts`: one shared `<audio>` for the song.
    - `useSongTime(): number`: rAF-driven while playing.
    - `playWindow(start, end, loop)`.
    - `frameIndex(t, start, fps): number`.
  - `ScenePreview({pid, sid?, candidate?, start, end})`:
    - an `<iframe sandbox="allow-scripts">` (never `allow-same-origin`) with `srcdoc` from `GET /preview`;
    - posts `init`, then one `frame` per animation frame from `useSongTime()` within `[start, end)`;
    - shows the error posted back.
  - `ShotStrip({shots, selected, onSelect, badge(shot)})`: one block per window, coloured by kind (footage / animation / open).
  - `ContinueButton({pid, state})`: a `PricedButton` "Continue" at `prices.continue`, calling `continueRun`. It is placed in the `App` header next to `StepStrip` and `SpendMeter` on every step, and is disabled with "nothing to do" when the price is 0 and no step is `missing` or `stale`.

- [ ] **Step 1: Write the failing tests**

```ts
test("preview iframe is sandboxed without same-origin", () => {
  const { container } = render(<ScenePreview pid="p" sid="s02" start={0} end={2.5} />);
  expect(container.querySelector("iframe")!.getAttribute("sandbox")).toBe("allow-scripts");
});
test("frameIndex clamps to the window", () => { expect(frameIndex(1.0, 0.5, 24)).toBe(12); expect(frameIndex(0.1, 0.5, 24)).toBe(0); });
test("priced button re-asks at the new price", async () => { /* first onConfirm → {ok:false, price:0.2}; label shows $0.20 */ });
```

- [ ] **Step 2: Run** `npm --prefix web test`. Expected: FAIL.
- [ ] **Step 3: Implement** the Interfaces above.
- [ ] **Step 4: Run** `npm --prefix web test`. Expected: PASS.
- [ ] **Step 5: Commit**: "Add the shared choose-a-way, suggestion, price and preview pieces".

---

### Task 13: Brief and Shots screens

**Files:**
- Create: `web/src/steps/Brief.tsx`, `web/src/steps/Shots.tsx`, `web/src/ShotForm.tsx`, `web/src/SceneEditor.tsx`
- Test: `web/src/ShotForm.test.tsx`

**Interfaces:**
- Consumes: Tasks 11 and 12.
- Produces:
  - **Brief:**
    - ModeSwitch at step level. Auto: a seed field + "Let the model write it" (`PATCH seed`, step mode auto, `continue` priced at $0).
    - Suggestions: `POST /suggest/brief`; cards show logline, style, cast/place names, look swatches and typeface; Use / Edit (Edit loads the card into the form).
    - Manual: a form with every brief field and look (colour inputs for 1–5 colours, typeface select).
  - **Shots:**
    - Waveform + `ShotStrip` (badge: you / auto / open) + selected-shot panel.
    - Selecting a shot loops its window.
    - Per-shot ModeSwitch (`PUT /shot-modes`).
    - Suggestions: footage ideas show their still; animation ideas show `ScenePreview` (candidate). A `PricedButton` "Suggest for this shot" uses `prices.still × 3`.
    - Manual: `ShotForm`.
    - Bulk bar: "Suggest for all open shots" (PricedButton, `still × 3 × n`) and "Auto the rest" (`PUT /shot-modes` for open shots, then `continue` priced).
  - `ShotForm({shot, brief, kinds, onSave})`:
    - kind radio limited to `kinds`;
    - footage fields: action, cast (multi-select from brief names), place (select or none), framing;
    - animation fields: template select with params generated from the catalogue specs, or a code editor (`SceneEditor`: a `<textarea>` + live `ScenePreview`).
    - A server 422 is shown inline at the field it names.

- [ ] **Step 1: Write the failing tests**

```ts
test("kind choice limited to project kinds", () => { /* kinds ["animation"] → no footage radio */ });
test("server 422 shown inline", async () => { /* putShot → {error: "action ..."} → text visible */ });
```

- [ ] **Step 2: Run** `npm --prefix web test`. Expected: FAIL.
- [ ] **Step 3: Implement.** The template catalogue comes from a new tiny route `GET /api/templates` → `templates.catalogue()`, added in `routes.py` with a one-line test in `tests/test_api_decisions.py`.
- [ ] **Step 4: Run** `npm --prefix web test && uv run pytest tests/test_api_decisions.py -q`. Expected: PASS.
- [ ] **Step 5: Commit**: "Add the brief and shots screens".

---

### Task 14: Keyframes and Takes screens

**Files:**
- Create: `web/src/steps/Keyframes.tsx`, `web/src/steps/Takes.tsx`, `web/src/InPoint.tsx`, `web/src/Upload.tsx`
- Test: `web/src/InPoint.test.tsx`

**Interfaces:**
- Produces:
  - **Keyframes:**
    - `ShotStrip` of footage shots (badge ✓ you / ✓ auto / … / uploaded).
    - The composed prompt, read-only, with an "Edit shot" link that opens `ShotForm` inline. The prompt is derived from the shot, so editing the shot is how it changes.
    - Auto: shows the current still and its note.
    - Suggestions: a 4-up grid from `candidates.keyframes[sid]` with Approve (`/keyframes/{sid}/use`), Variations (PricedButton, `keyframe_set`, `near_seed`), any `flag` badge, "None of these" (note → `/none`, then navigate to Shots with that shot selected), and a PricedButton "4 stills" (`keyframe_set`).
    - Manual: `Upload` (image) and a PricedButton "Generate one" (`continue` with `only=[sid]`).
    - "Changed since upload" banner from `keyframe.changed_since_upload`.
  - **Takes:**
    - `ShotStrip` including ⟳ for shots with a running job.
    - Per shot: the round counter (`round of cap`, or `round N` when `cap` is null) and 3 take cards. Each card loops its clip from `in` against the song window and has an `InPoint`.
    - Use → `/takes/{sid}/pick`.
    - "None of these" asks for a reason, offers an optional shot edit, calls `/reject`, then a PricedButton "Next round" (`take_round`, `continue` only=[sid]).
    - Back-to-keyframe and back-to-shot links. Manual: `Upload` (video, in-point).
  - `InPoint({clipDuration, window, value, onChange})`: a drag handle over a bar. Its value is clamped to `[0, clipDuration − window]`, in 1/24 s steps.
  - `Upload({accept, onFile, error})`: a file input plus drop zone, showing the server's 422 text.

- [ ] **Step 1: Write the failing tests**

```ts
test("in-point clamps to clip minus window", () => { /* clipDuration 4.8, window 2.5 → drag past end gives 2.3 */ });
test("in-point snaps to frames", () => { /* value 0.51 → 0.5 */ });
```

- [ ] **Step 2: Run** `npm --prefix web test`. Expected: FAIL.
- [ ] **Step 3: Implement** the Interfaces above.
- [ ] **Step 4: Run** `npm --prefix web test`. Expected: PASS.
- [ ] **Step 5: Commit**: "Add the keyframes and takes screens".

---

### Task 15: Rough cut dock and Cut screen

**Files:**
- Create: `web/src/RoughCut.tsx`, `web/src/roughcut.ts`, `web/src/steps/Cut.tsx`, `web/src/TryInPlace.tsx`
- Test: `web/src/roughcut.test.ts`

**Interfaces:**
- Consumes: `state.cut` (Task 6 `cut_plan`), `clock.ts`, `ScenePreview`.
- Produces:
  - `roughcut.itemAt(cut: CutItem[], t: number): {item: CutItem, local: number} | null`.
  - `roughcut.withOverride(cut, override: {shot: string, item: Partial<CutItem>} | null): CutItem[]`.
  - `TryInPlace` context: `setOverride(o | null)`, called by suggestion cards on hover / focus and cleared on leave / blur.
  - `RoughCut`:
    - docked at the bottom of every step screen, collapsible;
    - plays the song through the shared clock;
    - the stage shows the current item: take/render → `<video>` with `currentTime = in + local` kept within 0.08 s of the clock; still → `<img>` with a CSS push-in scale 1.0 → 1.06 over the window; card → the text on look background; blank → background;
    - an animation candidate in the override → `ScenePreview`;
    - a strip below shows every window by kind, with the current one highlighted; clicking seeks.
  - **Cut screen:**
    - big player (same stage) and strip with who-decided dots (`modes.*` + pick `quick` / `uploaded`);
    - "Needs a look" list built from: picks with `quick`; scenes whose `note` starts with `fallback:`; keyframe notes starting "accepted after"; `changed_since_upload`; cut items of kind still / card / blank. Each links to `#/p/<pid>/<step>?shot=<sid>`.
    - Summary: counts by who decided, plus spend.
    - Buttons: "Assemble draft" (`POST /assemble`), "Download draft" (`files/out/video.mp4`), "Report" (`files/out/report.md`), "Provenance" (`files/PROVENANCE.md`).

- [ ] **Step 1: Write the failing tests**

```ts
test("itemAt finds window and local time", () => { expect(itemAt(cut, 3.0)).toEqual({ item: cut[1], local: 0.5 }); });
test("itemAt past the end is null", () => { expect(itemAt(cut, 999)).toBeNull(); });
test("override swaps only its window", () => { /* other items identical by reference */ });
```

- [ ] **Step 2: Run** `npm --prefix web test`. Expected: FAIL.
- [ ] **Step 3: Implement** the Interfaces above.
- [ ] **Step 4: Run** `npm --prefix web test && npm --prefix web run build`. Expected: PASS.
- [ ] **Step 5: Commit**: "Add the rough cut on every screen and the cut screen".

---

### Task 16: End-to-end check and docs

**Files:**
- Create: `tests/test_ui_e2e.py`
- Modify: `README.md` (Quick start, the tastemaker table → three ways, "Project folder" gains `candidates/`, `fill/`, uploads; serving from a laptop with `ssh -N -L 8765:localhost:8765 <host>`), `CLAUDE.md` (front-end commands, `web/` layout, dist build)

**Interfaces:**
- Consumes: everything.

- [ ] **Step 1: Write the slow test**: `@pytest.mark.slow def test_tastemaker_walkthrough(tmp_path, song)`. It:
  - starts `create_app(tmp_path, FakeGenerator, fake_chats)` under uvicorn on a free localhost port in a thread;
  - uses Python Playwright to:
    1. create a project with kinds footage+animation;
    2. set a 0–8 s range;
    3. write a brief manually;
    4. set s01 footage manually, use an animation suggestion for s02, and leave s03 open;
    5. request keyframe suggestions for s01 and approve one;
    6. press Continue, pick a take with an in-point of 0.5;
    7. open Cut, assemble, and check that `out/video.mp4` exists and that "Needs a look" lists s03.

  The test skips if `celery_man/web/dist` is missing.
- [ ] **Step 2: Run** `npm --prefix web run build && uv run pytest tests/test_ui_e2e.py -m slow -q`. Fix any failure in the owning task's code and re-run until it passes.
- [ ] **Step 3: Update** README and CLAUDE.md as listed.
- [ ] **Step 4: Run** `uv run pytest -q -m "slow or not slow" && uv run ruff check && uv run ruff format --check && npm --prefix web test`. Expected: all PASS.
- [ ] **Step 5: Commit**: "Check the whole tastemaker path in a browser and document it".
