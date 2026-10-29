# Tastemaker front end — design

Date: 2026-10-28. Branch `tastemaker-ui`. Mockups from the session live in `.superpowers/brainstorm/` (git-ignored): `flow.html`, `shots.html`, `keyframes.html`, `takes.html`, `roughcut.html`, `cut.html`.

## Intent

celery-man is a general music-video tool, not a way to make any one video. Its pipeline already separates creative decisions from mechanical steps, and each decision can be made by a person ("tastemaker") or a model ("auto"). Today the tastemaker gets a set of plain server-rendered forms, one mode per step. This project replaces them with a real web front end. In it, a tastemaker walks a song through every decision in pipeline order. At each decision they can let the model decide, choose from a range of model suggestions, or supply the input themselves, and they always see and hear their choices against the song.

## Decided (Danny)

- A web interface. Danny's usual coding preferences (Python-only, minimal JS) do not bind this project.
- One screen per decision, in the order of the orchestration diagram: **Song → Brief → Shots → Keyframes → Takes → Cut**.
- Every decision offers **Auto / Suggestions / Manual**:
  - Auto: the model decides and the pipeline moves on.
  - Suggestions: a range of model alternatives to pick from, edit, or steer with a sentence and regenerate.
  - Manual: the tastemaker supplies the decision.
- On Shots, Keyframes and Takes, that switch is set **per shot**, not per step. Bulk actions: "suggest for all open shots", "auto the rest".
- Shots: suggestions for a window may mix footage and animation. Footage suggestions get a **paid quick still** (FLUX.1-schnell, about a cent each) so they can be judged by eye against animation.
- Keyframes: Manual includes **uploading your own image** as the keyframe (kept by name).
- Takes: Manual includes uploading your own clip.
- A **rough cut** is available on every screen. It plays the whole song with the best current material for each window, and it lets you try a suggestion in place before choosing it.
- The Cut screen as mocked: full player, who-decided markers per shot, a "needs a look" list linking back to the deciding step, spend summary, exports.
- Nothing missing from the playback (Danny, after the rough-cut playback).
- **Each shot moves on its own.** A shot can get keyframes and takes while other windows are still open in Shots.
- **No take-round cap for tastemaker shots.** Rounds continue as long as the tastemaker pays; each round shows its price, and the project cap and reserve still hold. Auto shots keep `MAX_ROUND = 2` and the existing fallback.
- **A draft can be assembled at any time.** Unfinished windows are filled the way the rough cut fills them, so a draft always exists.

## Calls I made (shown in mockups, not explicitly confirmed)

- Song is input only (upload, range on the waveform, kinds). No auto or suggestions there.
- Choosing a template or writing custom scene code is part of the Shots step, not its own screen.
- Cut is a screen even though it is not a decision in the diagram.
- A GPU spend meter on every paid screen, and each paid button shows its price. Pressing a priced button is the spend confirmation (this replaces the overview's "shown estimate" gate for those actions; the cap still holds).
- Counts: 3 brief suggestions, 3 shot ideas per window, 4 keyframe stills, 3 takes per round (today's `SEEDS`).
- Vision-model warning flags ("text in frame", "face warps") appear only when a vision model is configured. Without one, nothing is flagged and auto keyframe/take judging stays unavailable, as today.
- Stack: see "How it is built".

## The three ways, mapped onto the pipeline

The pipeline keeps two modes, `tastemaker` and `auto`. Mechanical steps still never branch on who wrote a decision file.

- **Auto** = `auto` for that shot and step (or for the step, at Brief).
- **Suggestions** and **Manual** = `tastemaker`. The difference is only in the UI: Suggestions asks the model for alternatives, which are kept as candidates outside the decision files. Whatever the tastemaker picks or types is written to the decision file through the existing validators, exactly like a manual edit.

Per-shot modes: `project.json` keeps `modes` as the step default and gains per-shot overrides, `shot_modes: {shot_id: {step: mode}}`. `advance` auto-decides a shot when its effective mode is `auto` and waits on it otherwise.

### Per-shot progression (the main pipeline change)

Today `shots.json` must cover every window, carries one `inputs` hash, and keyframes and takes start only when the whole shot list is done. That changes:

- `shots.json` holds every window. A window may be **open** (no decision yet: a status marker with no action or scene), and validation accepts open windows.
- Staleness becomes per shot. A shot is current when its own decision matches the brief it was made against. A brief edit marks every shot's downstream work stale, as today. A shot edit marks only that shot's keyframe, take and render stale.
- `status` reports per shot and per step. `advance` (and the keyframe, take and render steps) act on every decided shot whose effective mode allows it, and skip open shots.
- The 2-round take cap and quick fallback apply only to shots whose takes mode is `auto`. A tastemaker shot's round count is unlimited, gated by price, cap and reserve.

## Screens

Every screen shows the step strip (with done / waiting state), the song player, the spend meter on paid steps, and the docked rough cut.

**0 · Song.** Lists projects. A new project means uploading audio, entering the source (for provenance), choosing a range on the waveform, and ticking footage / animation. Timing analysis runs on create, and the windows show on the waveform.

**1 · Brief.**
- Auto: the text model writes it from an optional one-line seed.
- Suggestions: 3 briefs, each with logline, style, cast, places, avoid and look (colour swatches, typeface), with Use / Edit; a steer box regenerates the set.
- Manual: the existing brief fields as a form, prefilled.

**2 · Shots.** A timeline of the song with sections, the waveform and one block per window, coloured by kind and marked you / auto / open. Selecting a block loops that window. For the selected shot:
- Auto / Suggestions / Manual switch.
- Suggestions: 3 ideas, which may mix kinds within the project's `kinds`. Footage ideas show their quick still; animation ideas (template + params, or model-written code) show a live preview. A steer box applies per window.
- Manual: kind, action, cast, place, framing for footage; for animation, a template dropdown with its parameters, or a code editor with a live preview.

**3 · Keyframes.** Footage shots only, as a strip. For the selected shot:
- The composed prompt, editable. `PromptTooLong` is shown, never silently cut.
- Auto: today's behaviour (vision check, one redo, then accept with a note).
- Suggestions: 4 stills on different seeds, Approve / Variations (more seeds near the chosen one), "none of these" (back to the shot with a note), steer.
- Manual: upload an image, or generate exactly one from the edited prompt.

**4 · Takes.** Footage shots as a strip, including ones still generating. For the selected shot:
- 3 takes per round, each looping against its own window of the song. A drag handle sets the in-point, constrained to the window length.
- Round counter. "None of these" asks for a reason, `rewrite_shot` revises the motion, and the next round runs. Back-to-keyframe and back-to-shot links.
- Auto: the vision model picks a take and in-point, or rejects with a reason.
- Manual: upload a clip (trimmed to the window), or edit the motion prompt and generate one.

**5 · Cut.**
- The assembled draft against the song, with a per-shot strip and markers: you / auto / shipped unreviewed.
- "Needs a look": unreviewed takes, scene fallbacks, and vision flags. Each entry opens that shot at the step where it was last decided.
- Summary: decided by you vs auto, and spend by kind.
- Exports: download the draft, report + provenance.
- Assemble works at any time. Each window uses its pick or render if it has one, else the approved keyframe with a slow push-in, else a text card with the shot text (an open window: a blank card in the look's background). The report lists the windows filled this way.

**Rough cut (docked).** It plays the song with, per window:
- footage: the chosen take, else the approved keyframe with a slow push-in, else a card with the shot text;
- animation: the live scene.

Hovering a suggestion swaps it into its window for playback. This is assembled in the browser, not with ffmpeg.

## How it is built (my calls)

- **Back end stays the Python package.** The FastAPI app in `celery_man/web/` becomes a JSON API over the existing `Project`, `pipeline`, `steps`, `auto` and `takes` functions, and replaces the Jinja pages and templates. Same rules as today: bind to 127.0.0.1, the Host and same-origin guard, all writes through `project.write_json`, one run at a time. For use from a laptop: an SSH tunnel.
- **Front end: React + TypeScript, built with Vite**, in `web/` at the repo root. The production build is served as static files by the FastAPI app, so `celery-man serve` stays the single command. Node 24 is needed only to build.
- **Live animation previews run the scene in the browser.** The scene contract is plain JS over a 2D canvas, so the front end runs it in a sandboxed iframe (`sandbox="allow-scripts"`, CSP `default-src 'none'`, no network) and feeds it the per-frame features from the server (`features.py`). The Chromium render stays the source of the final clip.
- **Long jobs report progress.** Stills, takes, renders and suggestions run in the background, and the UI polls a status endpoint (or uses server-sent events). A page reload never loses a running job.
- **Candidates** (suggestions not yet chosen) live in a project folder, `candidates/`, with their own JSON written via `write_json`. Paid candidates (quick stills, keyframe seeds) are ledger rows like any other spend.
- **Uploads** are validated at the boundary: type by content (PNG/JPEG; MP4/MOV/WebM), size limit, and conversion to 960×544 / 24 fps with ffmpeg. They are recorded in the ledger and in `PROVENANCE.md` as "supplied by the tastemaker".

## Spend, jobs, projects, uploads (my calls, after the builder check)

- **What spends.** Nothing spends without an explicit priced click. Switching a shot to Auto queues it but doesn't spend. A **Continue** button on each paid step (and on the step strip) shows the price of all queued auto work plus pending tastemaker requests, and pressing it is the confirmation. The existing check survives: the run stops if its estimate rises above the shown price. Suggestion buttons (quick stills, keyframe seeds, variations, a take round) each carry their own price and spend on click.
- **Reserve.** Every UI spend keeps today's reserve of one quick take per shot that still lacks a pick, so the video can always ship. A button that would break into the reserve is disabled, with the reason shown. The meter shows spent / reserve / cap.
- **Jobs.** Per project, paid GPU work runs one batch at a time, in a queue: the existing run lock, ledger reservations and one Modal session per batch. Free work (text suggestions, renders, uploads, assemble) runs alongside it. Decision files written by both go through one per-project write lock. The UI can be used while a batch runs, and new paid requests join the queue.
- **Projects.** `celery-man serve ROOT` serves every project folder under `ROOT`, and the API routes carry a project id. `serve` on a single project folder still works and opens that project. One runner and queue per project. The other CLI commands are unchanged.
- **Uploads are pinned.** An uploaded keyframe or clip is never discarded automatically. If its shot or the brief changes later, it stays in use, marked "shot changed since upload" on the screen and in Cut's "needs a look"; the tastemaker keeps it or replaces it. An uploaded clip can be a shot's take without any keyframe; the Keyframes step then skips that shot. Uploads are keyed by content hash and recorded in the ledger (zero cost) so `pick_ok`-style checks accept them.

## Constraints

- Every repo rule in `CLAUDE.md` still holds, in particular: no theme / song / story / style / cast words in the repo (UI copy, fixtures and placeholders included), mechanical steps never branch on mode, prompts are never silently cut, and the server is localhost-only.
- The cap and the ledger are the only gates on spend. Nothing in the UI can spend without a visible price.
- The CLI (`new`, `modes`, `run`, `quick`, `status`) keeps working. `modes` gains a per-shot form.

## Non-goals

- Multi-user, accounts, hosting on the public internet.
- A video editor: no trimming beyond the in-point, no transitions, no reordering windows.
- Choosing or hosting the vision model (separate work; the UI degrades without one).
- Cast consistency across shots.

## Anti-goals (a failure even if it works)

- Any spend without a price shown at the moment of the click.
- A suggestion silently becoming a decision.
- Auto and tastemaker shots behaving differently downstream.
- The rough cut showing anything other than what the next assemble would use (apart from the hover preview).

## Left to the builder

- Polling vs server-sent events; component layout; the waveform library (or none).
- Exact API shapes, and how candidates are keyed and pruned.
- Text for the steer prompts, within the existing `auto.py` style.
- Test split: API tests with TestClient, front-end unit tests where logic lives (rough-cut timeline, in-point clamping), and one slow Playwright end-to-end run on a fake generator.
