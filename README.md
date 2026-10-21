# celery-man

A music-video pipeline with no theme built in. You give it a song; it times the song, writes a brief and a shot list, makes one still per shot (FLUX.1-schnell), makes draft clips from those stills (LTX-2.5, image-to-video), and assembles the video on the beat. Generation runs on Modal. Everything else runs on your machine.

Each creative decision is made by a person (**tastemaker**, in a local web page) or by a model (**auto**). The mode is set per decision, so you can mix them. Mechanical steps only read the decision files and never know which mode wrote them.

| # | Decision | Tastemaker | Auto |
|---|---|---|---|
| 1 | Brief (logline, style, cast, places, avoid) | fills in a form | text model writes it from an optional one-line seed |
| 2 | Shot list (one shot per window) | edits shots against the song | text model drafts it from brief + timing |
| 3 | Keyframes (one still per shot) | approves, or asks for a new seed or edited shot | vision model checks each still, may redo once |
| 4 | Takes (one take per shot, with an in-point) | picks from contact sheets, or rejects all | vision model picks or rejects |

## One-time setup

1. `uv sync`
2. Modal: `uv run modal token new`.
3. Create the Modal secret `huggingface` holding your `HF_TOKEN`.
4. On Hugging Face, accept the terms for the LTX-2.x and FLUX.1-schnell repos with the same account.
5. Check access: `uv run modal run celery_man/gen_modal.py::access` (both repos should print `OK`).
6. Optional end-to-end check: `uv run modal run celery_man/gen_modal.py::smoke`. The first run downloads the weights into the Volume `celery-man-weights`. Verified on 2026-10-20: one 960x544, 121-frame, 5.04 s clip on an L4 in 173 s wall time, peak RAM 77.2 GiB.

## Quick start

```
uv run celery-man new projects/mine --song path/to/song.mp3 --source "where the song came from"
uv run celery-man modes projects/mine brief=auto shots=auto keyframes=auto takes=auto
uv run celery-man serve projects/mine      # page at http://127.0.0.1:8765
uv run celery-man run projects/mine        # advance as far as the modes allow
uv run celery-man quick projects/mine      # one cheap take per shot, whole song
uv run celery-man status projects/mine
```

`modes` takes `STEP=MODE` pairs; steps are `brief`, `shots`, `keyframes`, `takes`; modes are `tastemaker` or `auto`. New projects start all-tastemaker. `run` and `quick` ask before spending; `--yes` skips the question. `run` stops (exit 2) when a tastemaker decision is waiting; make it in the web page, then run again. Reruns never regenerate a finished take. Editing the brief marks keyframes and picks stale, and nothing regenerates until you run again. `serve --port N` changes the port; the server binds to 127.0.0.1 only.

## Project folder

| File | Content |
|---|---|
| `project.json` | song, source, range, cap, modes, seed, models |
| `timing.json` | beats, downbeats, tempo, sections |
| `brief.json`, `shots.json` | decisions 1 and 2 |
| `keyframes.json`, `picks.json` | decisions 3 and 4 |
| `ledger.jsonl` | every job and verdict, with cost |
| `keys/`, `takes/`, `sheets/` | stills, clips, contact sheets |
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

## Costs

- A draft clip on an L4 costs about $0.07.
- A 3.5-minute song costs about $4 for `quick` and about $11 per judged round.
- The cap is $25 per project (`cap_usd`). When the cap runs out between rounds, each unfinished shot gets one quick take and the video still assembles. `run` refuses to start if a quick pass over the range cannot fit.

## Limits

- The cast is text only, so faces drift between shots.
- Songs need a steady pulse; windows are cut on detected beats.
- Draft resolution is 960x544, 24 fps. Shots are at most 4.54 s.

## Licenses

LTX-2.x is under the LTX-2.x Community License. FLUX.1-schnell is Apache-2.0. Each project writes a `PROVENANCE.md` with these and the song source. Check the licenses before publishing a video.

## Development

`uv run pytest -m "not slow"`, `uv run ruff check`, `uv run ruff format`.
