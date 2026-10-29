# celery-man

A music-video pipeline with no theme built in. You give it a song; it times the song, writes a brief and a shot list, makes one still per shot (FLUX.1-schnell), makes draft clips from those stills (LTX-2.5, image-to-video), and assembles the video on the beat. Generation runs on Modal. Everything else runs on your machine.

A shot is either **footage** (generated, as above) or **animation**: a small JavaScript scene drawn on a canvas in headless Chromium, driven by the song's beats and loudness. Animation is free and rendered locally; it never touches Modal and writes no ledger rows.

Each creative decision is made by a person (**tastemaker**, in a local web page) or by a model (**auto**). Mechanical steps only read the decision files and never know which mode wrote them.

## The web page

`celery-man serve` opens a page that walks a song through every decision in pipeline order, one screen each: **Song → Brief → Shots → Keyframes → Takes → Cut**. Every decision offers three ways:

- **Auto**: the model decides when you press Continue (the project's `auto` mode).
- **Suggestions**: the model offers alternatives; you pick one, edit it, or steer the next set with a sentence.
- **Manual**: you supply the decision yourself.

Suggestions and Manual are both `tastemaker` mode; what you pick or type is written to the decision file like any other edit. On Shots, Keyframes and Takes the choice is made **per shot**, and each shot moves on its own: one shot can have takes while another window is still open.

| Screen | Auto | Suggestions | Manual |
|---|---|---|---|
| Song | | | upload the audio, its source, footage and/or animation; drag a range on the waveform |
| Brief | text model writes it from an optional seed | 3 briefs (with look swatches) | the brief form |
| Shots | text model drafts the shot | 3 ideas per window, mixing kinds; footage ideas get a paid quick still, animation ideas a live preview | kind, action, cast, place, framing; or a template with its params, or your own scene code |
| Keyframes (footage) | vision model checks, may redo once | 4 stills on new seeds; Variations near one; "none of these" sends a note back to the shot | upload an image, or generate one |
| Takes (footage) | vision model picks a take and in-point, or rejects | 3 takes per round, each looping against its window; drag the in-point; "none of these" with a reason starts the next round | upload a clip |
| Cut | | | assemble a draft at any time; "needs a look" links back to the step to revisit |

A rough cut is docked on every screen: it plays the song with the best current material for each window (take, else the approved keyframe, else a text card; animation plays live), and hovering a suggestion tries it in place. Every paid button shows its price, and pressing it is the confirmation; a spend meter shows spent, reserve and cap. Auto takes keep the 2-round cap and the quick fallback; tastemaker takes have no round cap, only the price, the cap and the reserve.

## One-time setup

1. `uv sync`
2. Modal: `uv run modal token new`.
3. Create the Modal secret `huggingface` holding your `HF_TOKEN`.
4. On Hugging Face, accept the terms for the LTX-2.x and FLUX.1-schnell repos with the same account.
5. Animation shots: `uv run playwright install chromium` (headless Chromium renders them locally).
6. The web page (needs Node 24): `npm --prefix web ci && npm --prefix web run build`. The build lands in `celery_man/web/dist`, which `serve` hands out.
7. Check access: `uv run modal run celery_man/gen_modal.py::access` (both repos should print `OK`).
8. Optional end-to-end check: `uv run modal run celery_man/gen_modal.py::smoke`. The first run downloads the weights into the Volume `celery-man-weights`. Verified on 2026-10-20: one 960x544, 121-frame, 5.04 s clip on an L4 in 173 s wall time, peak RAM 77.2 GiB.

## Quick start

```
uv run celery-man new projects/mine --song path/to/song.mp3 --source "where the song came from" [--kinds footage,animation]
uv run celery-man modes projects/mine brief=auto shots=auto keyframes=auto takes=auto
uv run celery-man serve projects           # page at http://127.0.0.1:8765, every project under projects/
uv run celery-man run projects/mine        # advance as far as the modes allow
uv run celery-man quick projects/mine      # one cheap take per shot, whole song
uv run celery-man status projects/mine
```

`serve ROOT` takes one project folder or a folder of project folders; the page creates new projects there. The server binds to 127.0.0.1 only, so from a laptop use an SSH tunnel and open http://localhost:8765 there:

```
ssh -N -L 8765:localhost:8765 <host>
```

`--kinds` is a comma list of `footage` and `animation` (default `footage`); it tells the auto shot planner and the web page which kinds of shot may be used. `modes` takes `STEP=MODE` pairs (steps `brief`, `shots`, `keyframes`, `takes`) or per-shot `SID.STEP=MODE` pairs (steps `shots`, `keyframes`, `takes`); modes are `tastemaker` or `auto`. New projects start all-tastemaker. `run` and `quick` ask before spending; `--yes` skips the question. `run` stops and exits 0 when a tastemaker decision is waiting; make it in the web page (or press Continue there), then run again. `quick` exits 2 in that case. Reruns never regenerate a finished take. Editing the brief marks keyframes and picks stale, and nothing regenerates until you run again. `serve --port N` changes the port.

## Project folder

| File | Content |
|---|---|
| `project.json` | song, source, range, cap, modes and per-shot `shot_modes`, seed, models |
| `timing.json` | beats, downbeats, tempo, sections |
| `brief.json`, `shots.json` | decisions 1 and 2 |
| `keyframes.json`, `picks.json` | decisions 3 and 4 |
| `ledger.jsonl` | every job and verdict, with cost |
| `keys/`, `takes/`, `sheets/` | stills, clips, contact sheets; uploads land here too (`keys/<shot>-<hash>.png`, `takes/upload-<hash>.mp4`), converted to 960x544, 24 fps, and are never discarded automatically |
| `candidates/` | suggestions not yet chosen (`brief.json`, `shots/<shot>.json`, `keyframes/<shot>.json`) |
| `fill/` | the stand-in clips a draft uses for windows with no take or render (keyframe push-in, text card) |
| `scenes/`, `renders/`, `renders.json` | your custom scene files (`scenes/<name>.js`); rendered animation clips and their keys |
| `out/video.mp4`, `out/report.md` | the result and a report (spend, takes per shot, mode per decision) |
| `PROVENANCE.md` | song source, models, licenses |

## Models (`project.json`)

```json
"models": {
  "text":   {"base_url": "http://127.0.0.1:8080/v1", "model": "local", "api_key_env": ""},
  "vision": {"base_url": "http://127.0.0.1:8081/v1", "model": "local", "api_key_env": ""}
}
```

Any OpenAI-compatible endpoint works. `api_key_env` names the environment variable holding the key (empty for a local server).

- **text** is used for auto brief, auto shots and shot rewrites. Any instruct model works, for example a local `llama-server`.
- **vision** is used for auto keyframe checks and auto take picks. It needs a vision-capable model: a `llama-server` started with `--mmproj`, or a cloud model.
- `quick` needs only the text role (and only when brief or shots are set to auto).

## Animation shots

A shot with `kind: "animation"` has a `scene`: `{"template": name, "params": {...}}` or `{"file": "scenes/<name>.js", "params": {...}}`. The brief's `look` (background, 1 to 5 colours, typeface: `serif`, `sans`, `mono` or `display`) styles every scene. Custom scene files go in the project's `scenes/` folder. Edit a scene or the look and only the affected shots re-render. Animation renders at 960x544, 24 fps, like footage.

Templates (params, with defaults):

| Template | Draws | Params |
|---|---|---|
| `bars` | stripes whose heights follow low, mid, high | `count` 3..64 (16), `orientation` horizontal/vertical (vertical) |
| `burst` | particles thrown from the centre on each downbeat | `count` 10..1000 (150), `gravity` 0..3 (0.4) |
| `drift` | particles along a flowing noise field | `count` 100..4000 (1200), `trail` 0..0.98 (0.85) |
| `grid` | tiles that flash on beats, turn with the mids | `cols` 2..24 (8), `pulse` 0..1 (0.6) |
| `rings` | rings born on each beat, expanding with loudness | `count` 1..32 (8), `thickness` 0.002..0.1 (0.012) |
| `title` | one line of text, pulsing on downbeats | `text` (empty), `pulse` 0..1 (0.3), `fade` 0..32 (4) |
| `words` | one word at a time on the beat | `text` (empty), `every` beat/bar (beat), `size` 0.04..0.4 (0.14), `position` center/lower/upper (center) |

The scene contract (the same text the auto mode gives the model, `auto_scene.CONTRACT`):

> A scene is plain JavaScript that defines two globals, no imports. `setup(env)` runs once (may be async). `draw(ctx, f, env)` draws one frame on a 2D canvas context; the canvas is NOT cleared between frames, so paint the background yourself.
>
> `env`: `params` (object), `look` (`{background: hex, colors: [hex], typeface}`; `typeface` is a font family for `ctx.font`), `width`, `height` (960x544), `fps` (24), `duration` (seconds), `random()` (seeded; `Math.random` is the same).
>
> `f`: `t` (s since shot start), `song_t` (s in the song), `progress` (0..1), `beat`, `bar` (indexes), `beat_phase`, `bar_phase` (0..1), `on_beat`, `on_downbeat` (bool), `level`, `low`, `mid`, `high` (0..1 loudness and band energy).
>
> `Date.now` and `performance.now` follow song time. Never use `new Date()`, `crypto`, `fetch` or any network. Each frame must draw in under 2 s. Draw with `env.look` colours.

In auto mode the text model may write a custom scene; it is test-rendered, fixed up to 3 times on errors, then replaced by a template (the report lists these under "Scene fallbacks").

## Costs

- Animation shots cost nothing.
- A draft clip on an L4 costs roughly $0.07.
- A 3.5-minute song costs roughly $4 for `quick` and roughly $11 per judged round.
- The cap is $25 per project (`cap_usd`). When the cap runs out between rounds, each unfinished shot gets one quick take and the video still assembles. `run` refuses before its first spend if a quick pass over the range cannot fit.

## Limits

- The cast is text only, so faces drift between shots.
- Songs need a steady pulse; windows are cut on detected beats.
- Draft resolution is 960x544, 24 fps. Shots are at most 4.54 s.

## Licenses

LTX-2.x is under the LTX-2.x Community License. FLUX.1-schnell is Apache-2.0. Each project writes a `PROVENANCE.md` with these and the song source. Check the licenses before publishing a video.

## Development

`uv run pytest` (skips slow tests; `uv run pytest -m slow` runs them, including a browser walkthrough of the whole page that needs the front end built), `uv run ruff check`, `uv run ruff format`.

Front end (React + TypeScript + Vite, in `web/`): `npm --prefix web ci`, `npm --prefix web test`, `npm --prefix web run build`.
