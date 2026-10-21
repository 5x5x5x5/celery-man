# celery-man: design

A new repo, `~/projects/celery-man`, private on GitHub as `5x5x5x5/celery-man`. Date 2026-10-20.
Source of the carried-over code: `~/projects/paper-machine/footage/` (commit ba313d1).

## Intent

The paper-machine generated-footage trial (`paper-machine/footage/`, merged 2026-10-17) proved the mechanics:
- FLUX keyframes and LTX-2.5 clips on Modal
- a ledger with a budget cap
- clips cut to the beat with the song laid under them

The theme was baked into the code and data. 20 shot prompts each repeat the palette and character text, the song and timing paths are hard-coded, and an agent in a Claude Code session made every creative call.

Danny wants a **clean separation between creative decisions and a mechanical pipeline**. The production will not run in a Claude Code session.

celery-man is that pipeline. It has no song, theme or story built in. Each creative decision is made either by a person (**tastemaker**) or by a model (**auto**), and the mode is set separately for each decision.

## Decided (Danny)

- **New repo.** It contains no song, theme or story. paper-machine stays as it is.
- **Two modes, set per decision point:** tastemaker (a person decides) or auto (a model decides). They can be mixed, for example a hand-written brief and shots with takes picked by auto.
- **Auto model is configurable.** It sits behind one interface that points at either a local model (Danny's llama-server, plus a local vision model) or a cloud model.
- **Any audio file.** The pipeline detects tempo, downbeats and sections itself. Tempos other than 120 BPM must work, and so must tempo changes.
- **Tastemaker works in a local web page.** It holds the brief form, the shot list laid against the song, and grids of keyframes and takes to approve or pick.
- **No test video yet.** No song or story ships with the repo, and none is chosen as the first run.
- **Whole song or a range.** By default a video covers the full track. A project can set a start and an end to make an excerpt, and both snap to downbeats.
- **Out of money: fall back to quick.** When the next round won't fit under the cap, it is not launched. Each unfinished shot gets one quick take, used as is, and the video ships. The report names those shots.
  - *My call:* before any spend, the pipeline checks that a quick pass over the whole range fits under the cap. If it doesn't, it refuses to start, so a video is never left with a hole.
  - For scale, at measured L4 rates (about $0.07 per draft clip), a 3.5-minute song at 120 BPM is about 50 shots. That's about $4 for a quick run, and about $11 for each judged round of 3 takes.

## The four decision points

| # | Decision | Tastemaker | Auto |
|---|---|---|---|
| 1 | **Brief:** theme/logline, style (medium, palette, lighting, camera language), cast (name + description), places (name + description), avoid list | fills in a form | text model writes it from an optional one-line seed (empty seed allowed) |
| 2 | **Shot list:** one shot per window: action, which cast, which place, framing | edits shots against the song's sections | text model drafts it from the brief + timing |
| 3 | **Keyframes:** one still per shot | approves, or asks for a new seed or an edited shot | vision model checks each still against its shot and may regenerate once, then accepts *(my call)* |
| 4 | **Takes:** pick one take per shot, with an in-point | picks from contact sheets, or rejects all for another round | vision model picks from contact sheets, or rejects all *(my call)* |

Every decision point writes a plain file in the project folder. The mechanical steps read only those files and never know which mode wrote them. A tastemaker can open a file an auto step wrote and change it.

**Reruns are started by the user *(my call)*.** An edit marks the work downstream of it as out of date, in the files and on the page. Nothing regenerates until the user starts it. The start shows an estimated cost first, because editing the brief changes every prompt and so redoes every keyframe and take.

**Round 2 needs a changed shot *(my call: kept from the trial, where only the rewritten shot passed)*.** A second round is refused unless the shot's text or keyframe changed. In tastemaker mode, rejecting all takes opens the shot for editing. In auto mode, the text model rewrites a rejected shot, using the vision model's reason for rejecting it, into a simpler single action. That rewrite is part of decision point 4.

**Auto models *(my call)*.** No vision model is set up locally yet. The builder chooses one that runs on the RTX 3080 (10 GB) behind llama-server with image input. The text role and the vision role make the same calls whether the backend is local or cloud.

## Prompt composition

A shot never contains style words. The prompt sent to FLUX or LTX is `compose(shot, brief)`. It joins the shot's action and framing, the description of each named cast member and place (word for word every time), the style block, and the avoid list. The avoid list goes into the text as "no …", because LTX distilled runs at guidance 1 and FLUX schnell at guidance 0, so neither has a real negative prompt.

Changing the style means editing the brief once and regenerating. No shot text changes.

**Length *(my call)*.** FLUX.1-schnell cuts T5 text at 256 tokens, and the cut falls on the end of the prompt, which is the style block and the avoid list. So `compose` counts tokens, and a brief or shot that goes over the limit is rejected with a message naming the longest fields. Nothing is silently cut. The web page shows a running count, and auto-written text is checked the same way.

## Timing

`timing` takes any audio file and writes `timing.json`: tempo or tempos, beat times, downbeat times, bar times, and sections (start, end, label). Bars are measured, not computed as `bar_index × bar_seconds`, so tempo changes are handled.

Shot windows are whole bars, ending on a downbeat. A window can be no longer than the clip length minus the trim handle (5.04 s − 0.5 s at 121 frames, 24 fps today). When even one bar is too long, which happens below roughly 53 BPM in 4/4, the window ends at the beat nearest the limit. Both modes are held to this when planning shots, and the pipeline rejects a shot list that breaks it.

Songs with no steady pulse (ambient, rubato) may not get usable downbeats. The detector reports its confidence, and a song below threshold stops with a clear message instead of producing a bad cut. Hand-correcting timing is not in v1.

## Mechanical pipeline

Steps in order. Each one resumes: finished work keyed in the ledger is never redone.

1. `timing`: song to `timing.json`
2. brief, then shots (decision points 1 and 2, in whichever mode is set)
3. keyframes: FLUX.1-schnell on Modal, then decision point 3
4. takes: LTX-2.5 image-to-video drafts on Modal (one L4 with group offload, as in paper-machine), then decision point 4. At most 2 rounds of 3 takes per shot, enforced across reruns. After round 2 the best take ships and the report says so. If the cap runs out first, the quick fallback (see Decided) applies.
5. assemble: ffmpeg trims each pick to its window, joins them with hard cuts, and lays the original song under them
6. report: spend, takes per shot, which mode made each decision, failures

A **quick** run (1 take per shot, used without review) stays for testing the pipeline itself.

## Carried over from paper-machine/footage (copy, then strip the theme)

- `ledger.py` (append-only jsonl, flock, job key = shot + seed + hash of prompt and keyframe, cap checked before launch)
- `assemble.py`
- `gen_modal.py`: Keyframer (FLUX on L40S) and Animator (LTX on L4 + 88 GiB, group offload, diffusers pinned to `b800f828…`)
- the quick/rounds logic from `direct.py`
- the lessons in paper-machine's memory and spec: no on-screen text, simple locked actions beat abstract ones, cold start costs

Not carried over: `shots.json`, `timeline.json`, the riso renderer, `music.py`, and any paper-machine wording.

## Project folder

One folder per video: song, `timing.json`, brief, shots, keyframes, a modes file (decision point → tastemaker | auto), picks/timeline, ledger, and outputs. The repo ships with no project folders. Where they live, and whether they are git-ignored, is left to the builder.

## Constraints

- **Budget cap per project**, checked before launch. Default $25 *(my call: kept from the trial)*.
- **Auto model config:** an OpenAI-compatible base URL, model name and optional key, set separately for the text role and the vision role. Local and cloud differ only in config.
- **Local web page:** binds to localhost only, so the server is not reachable from other machines. It shows stills and contact sheets and writes the same files the auto steps write. Uses Danny's editorial look: light, cream and ink, one accent, never neon.
- **Repo conventions:** uv, ruff, pytest in tests/, type hints on public functions, logging rather than print, small focused files, main branch.
- **Licenses:** a provenance file per project records the model licenses (LTX-2.x Community, FLUX.1-schnell Apache-2.0) and the song's source as the user states it.

## Non-goals (v1)

- Reference-image character consistency. Cast stays text-only, so faces can drift between shots, as the printer did in paper-machine.
- Hand-correcting timing, lyrics or singer performance shots, transitions other than hard cuts, colour matching, upscaling
- MiniMax H3 or any second video model
- Multi-user or hosted deployment: the web page is for one person on one machine
- Any bundled demo song or story

## Anti-goals (a failure even if it runs)

- Any theme, song, story or style hard-coded in the repo
- A mechanical step that branches on which mode made a decision
- Cuts that miss downbeats, or generated audio in the output
- Spend over the cap, or finished takes lost when a later job fails
- An auto run that stalls waiting for a person, or a tastemaker step that a model silently fills in

## Left to the builder

- The beat, downbeat and section detection library *(my lean: beat_this for beats and downbeats; any MIT/BSD segmenter for sections)*, and where it runs (local CPU or Modal)
- The web stack and page layout, and how the page waits on Modal jobs
- File formats and names inside the project folder, and the CLI shape
- The auto prompts, and how the vision model is shown takes (contact sheet vs. frames)
- How a section map turns into shot windows (shot length, 1 vs. 2 bars)
- Test shape. At a minimum:
  - timing on a synthetic click track at two tempos, one with a tempo change
  - `compose` keeps style words out of shots
  - each decision file is readable whichever mode wrote it
  - ledger resume
  - cut points land on the detected downbeats
