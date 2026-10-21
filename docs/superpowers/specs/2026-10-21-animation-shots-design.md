# celery-man: animation shots

An addition to `~/projects/celery-man` (public, `5x5x5x5/celery-man`, main at fcf5211). Date 2026-10-21.
Builds on `docs/superpowers/specs/2026-10-20-celery-man-design.md`; everything there still holds unless this document changes it.
Prior art: paper-machine's riso renderer (`~/projects/paper-machine/render.py`, `engine/`), which draws Canvas2D frames in headless Chromium through Playwright and pipes them to ffmpeg.

## Intent

celery-man makes music videos from generated footage. Danny wants it to make **code-rendered animation** too: scenes drawn by code, frame by frame, that move with the music. Both kinds live in one pipeline and cut together in one video.

The separation from the first design stays: creative decisions are made by a person (tastemaker) or a model (auto), and the mechanical pipeline only reads the files those decisions write. The repo still ships no theme, song or story, and that now includes no themed scenes.

## Decided (Danny)

- **Code-rendered scenes.** An animation shot is drawn by code. It is not a video model with an animated look, and it is not a moving still.
- **Mixed per shot.** Each shot in the shot list is either footage or animation, and one video can cut between them.
- **Templates plus custom scenes.** The repo ships a small library of parametric scene templates. Any shot can use a custom scene file instead.
- **The auto model may write custom scene code.** The pipeline test-renders what it writes and sends any errors back to the model to retry.
- **JS Canvas2D**, rendered in headless Chromium, as in paper-machine.
- **Beats and loudness.** On every frame a scene receives the time, its beat and bar position, and the song's loudness and bass/mid/high energy.
- **No review step for animation.** Rendering is deterministic, so an animation shot goes straight into the cut. To change one, you edit the shot list.
- **Three template families:** type, geometric, and particles/fields. No image-driven templates.
- **Type scenes show words written in the shot.** The tastemaker or the auto model writes the text. Nothing is transcribed.

## Calls I made (Danny agreed to the playback)

- **Rendering is local.** It uses Playwright Chromium on the machine running celery-man, with no GPU and no Modal. Animation costs $0 against the cap and adds nothing to the ledger.
- **Animation shots skip the keyframes and takes steps.**
- **Same size and frame rate as footage:** 960x544 at 24 fps, taken from `gen_modal.W, H, FPS`, so a cut between kinds never rescales or drops frames.
- **The brief gains a shared look,** a short colour palette and a typeface. Every template reads it, so a video's animation shots match each other, and match the footage when the brief's style text describes the same colours.
- **Sandboxed scene code.** A scene runs only inside the browser page. It has no network access and a time limit per frame.
- **Quick mode renders animation shots normally,** since they cost nothing.

## The shot list changes

A shot gains two fields:

- `kind`: `"footage"` or `"animation"`. A shots.json written before this change has no `kind`, and every shot in it is read as footage, so existing projects keep working.
- `scene`: `null` for footage. For animation, exactly one of:
  - `{"template": "<name>", "params": {…}}`
  - `{"file": "scenes/<name>.js", "params": {…}}`, a custom scene in the project folder

`action`, `cast`, `place` and `framing` stay on every shot.
- **Animation shots:** `action` says in words what the scene should do. It is what the auto model writes code from, and what a person reads in the shot list. `cast`, `place` and `framing` may be empty and are ignored.
- **Footage shots:** unchanged.

Shot windows are planned exactly as now, before anyone chooses kinds. The clip-length limit does not apply to animation, but keeping one window plan keeps the timing code and the shot ids unchanged.

**Which kinds a project may use** is a project setting, `kinds` in project.json: `["footage"]`, `["animation"]` or both. `celery-man new` takes `--kinds`, and the default is footage only, so existing behaviour and cost do not change. The auto shot writer only uses the kinds allowed.

*My call:* `kinds` can be edited later, in project.json or on the page. It is part of the shots step's input stamp, so changing it makes the shot list stale. A shots.json that uses a kind the project doesn't allow is rejected with a message naming the shot, whether it is new or saved earlier.

## The brief changes

The brief gains `look`:

```json
"look": {"background": "#f3ecdc", "colors": ["#1c1a17", "#2f5d8a", "#d9482b"], "typeface": "serif"}
```

- `background` and `colors` are hex colours, with 1 to 5 entries in `colors`.
- `typeface` is the name of one of the fonts bundled in the repo.

Footage prompts never see `look`. `compose` and its token budget are unchanged, and the style text stays the only style input for footage. The tastemaker form and the auto brief writer both fill in `look`. A brief.json written before this change has no `look`; it gets a neutral default (ink on paper, the bundled serif), so old briefs stay valid.

Editing `look` makes every animation render stale, and no footage. Editing style text or descriptions makes footage stale, as now, and no animation.

## Scenes

A scene is one JS file. The contract is small enough for a model to write from one page of instructions:

- **Exports.** The file defines `setup(env)` and `draw(ctx, f, env)`.
  - `setup` runs once per shot.
  - `draw` runs once per frame, in order from the shot's first frame, so a scene can keep state such as particle positions between frames.
- **`env`** holds the shot's `params`, the brief's `look`, `width`, `height`, `fps`, the shot's duration, and a seeded random number generator `env.random()`.
- **`f`** is the frame, with these fields:
  - `t`: seconds since the shot started
  - `song_t`: seconds in the song
  - `progress`: 0 to 1 through the shot
  - `beat` and `bar`: indexes in the song
  - `beat_phase` and `bar_phase`: 0 to 1
  - `on_beat` and `on_downbeat`: true on the first frame at or after each beat or downbeat
  - `level`, `low`, `mid` and `high`: each 0 to 1, normalised over the whole song

**Determinism.** The same scene file, params, look, window and song always give the same frames. `Math.random` is replaced by the seeded generator, and the clock functions are pinned to the frame time. A render is keyed by a hash of these inputs, and an unchanged shot is never re-rendered.

*My call:* the key also covers the code behind a render: the template or scene source, the renderer page and the bundled fonts. Editing a template in the repo therefore re-renders the shots that use it, so a stale render is never mistaken for a fresh one.

**Sandbox.** The page loads only the renderer, the scene and the bundled fonts:
- Every network request is refused.
- A frame that takes longer than the per-frame limit fails the shot, and so does a scene that throws.
- The error is recorded where the person or the auto model will see it.

*My call:* 2 s per frame, so a stuck loop is caught quickly.

**Audio features** (`level`, `low`, `mid`, `high`) are computed once per song in Python with librosa, at the video frame rate, and stored next to timing.json. They are stamped with the song hash, like timing.

*My call:* the timing step always writes the features, even in a footage-only project. They take seconds, and turning animation on later then needs no extra step.

## Templates

The repo ships **2 or 3 templates in each family**:
- **type:** words placed and timed on beats or bars
- **geometric:** shapes, grids, lines and rings that pulse, rotate or tile
- **particles/fields:** particles, flow fields and noise

Each template comes with a **parameter schema**: names, types, ranges and defaults. The web form, the auto writer and the validator all read that schema.

*My call:* a value outside its range or of the wrong type is rejected with a message naming the shot and the parameter, the same way a bad shot field is rejected now. In auto mode the rejection goes back to the model as retry feedback. A missing parameter gets its default.

Templates must stay theme-neutral. They draw only with the brief's `look` and their params, and contain no baked-in colours, words or imagery. Their sizes and speeds respond to the beat and loudness.

**Fonts.** A few OFL-licensed fonts are bundled in the repo, because the page has no network access. *My call:* one serif, one sans, one mono and one display face. The provenance file lists them.

## Who decides what

Animation adds no new decision point. It widens decision point 2 (the shot list):

| | Tastemaker | Auto |
|---|---|---|
| Kind per shot | chooses footage or animation per shot, among the allowed kinds | text model chooses, among the allowed kinds |
| Scene | picks a template and fills in its params, or uploads or pastes a custom scene file | picks a template and params, or writes a custom scene |
| Type text | writes it in the template params | writes it in the template params |

**Auto custom scenes.** The model writes the code, and the auto shots step test-renders it in full: every frame of the shot, through the same renderer the render step uses. Any error text goes back to the model, up to 3 tries in total, the same way `ask_json` retries. If all 3 fail, the shot falls back to a template the model picks, so an auto run never stalls on a broken scene. The report names every fallback.

A full test render, rather than a sample, catches a scene that only breaks late in the shot. Renders are cheap and cached by their key, so the render step then reuses the clip the test made. Custom scenes go in `scenes/` in the project folder, and a person can open and edit them.

**Tastemaker custom scenes** are not rendered when the shot list is saved, so the web request stays quick. The render step finds any errors. A shot whose render fails is marked failed, with the error on the shots page and in `status`, and the other shots still render. Assemble waits until every shot has a clip. The person fixes the scene or switches the shot to a template.

**Render step.** Rendering never branches on which mode wrote the shot list. In auto mode a render failure is already ruled out by the test render, apart from a bug in the renderer itself, which stops the run with an error like any other step failure.

## Pipeline

```
timing → brief → shots → render (animation only) → keyframes (footage only) → takes (footage only) → assemble
```

- **timing** also writes the audio features.
- **keyframes and takes** skip animation shots. Their estimates, quick pass, budget preflight and report count footage shots only.
- **render** is new, and runs before keyframes and takes. *My call:* broken scenes then surface before any money is spent on footage. It renders each animation shot whose render is missing or stale to `renders/<shot>-<hash>.mp4`, exactly as many frames as the window, at 960x544 and 24 fps.
  - A crash keeps the shots already finished.
  - It runs locally in the CLI and from the web page, under the same run lock.
- **assemble** takes each shot's clip: the picked take for footage (`in` as now), the render for animation (`in` = 0). Cutting, contiguity checks and laying the song underneath are unchanged.
- **quick** renders animation shots the same way, and footage gets its one quick take each.
- **report and provenance** list each shot's kind and, for animation, its template or file. Provenance adds the bundled fonts' licenses.

## Web page

- **Shots page.** Each shot gets a kind toggle (only allowed kinds). Animation shots get:
  - a template menu with a form built from the template's parameter schema
  - or a custom file, uploaded or pasted
  - the last render's error, if any
  - *My call:* a small poster frame of the last render, so a person can see what a template does without opening the video
- **Brief page.** A `look` section with colour pickers and a typeface menu.
- **No new review page.** It keeps the existing editorial look.

## Constraints

- Everything in the first design's Constraints still holds: uv, ruff, pytest, the localhost-only page, the cap and the editorial look.
- **New dependency:** Playwright with Chromium, installed with uv and `playwright install chromium`. The README says so.
- Rendering must not need a GPU or network access.
- **Speed.** A 4-second shot renders in well under a minute on a normal CPU. The paper-machine renderer did 80 s of 1080p in about 2 minutes.

## Non-goals

- WebGL or shaders
- Image-driven templates: halftone, riso, glitch, treating keyframes
- Lyrics transcription, and timing words to the singing
- Reviewing animation renders or picking among variants
- Rendering on Modal, or any paid rendering
- Transitions other than hard cuts, and blending footage with animation within one shot

## Anti-goals (a failure even if it runs)

- A template with a theme, colours or words baked in
- Scene code reaching the network or the filesystem outside the browser page
- Two renders of an unchanged shot that differ
- An auto run that stalls on a broken custom scene
- Animation shots spending money, or footage estimates counting animation shots
- An existing footage-only project that changes behaviour or breaks

## Left to the builder

- The renderer page and how frames leave the browser (paper-machine's `toDataURL` → ffmpeg pipe is the known-good path)
- The parameter-schema format, and the exact list of 6 to 9 templates
- How many frames the test render samples
- The auto prompt for writing scenes, including the one-page scene contract given to the model
- The exact fonts, as long as they are OFL and bundled
- How a stale render shows on the overview page
- Tests. At a minimum:
  - an old shots.json and an old brief.json still load
  - kinds the project doesn't allow are rejected
  - each template renders without errors at two tempos
  - tests that launch Chromium are marked `slow`, so the default `pytest` run needs no browser; the rest use a fake renderer
  - the same inputs render to identical frames
  - a scene that fetches the network, throws, or loops forever fails cleanly
  - the auto fallback after 3 failed scenes
  - cuts in a mixed footage and animation video land on the downbeats
