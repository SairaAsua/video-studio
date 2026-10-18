# AGENTS.md — Video Studio

> For AI agents (Hermes, Claude Code, Codex CLI, Grok) entering this project.

## Project identity

Video Studio is a web dashboard + ffmpeg backend for AI-assisted video production. It produces narrated explainer videos from scripts and AI-generated b-roll. The dashboard is the primary collaboration tool between the human and the agent.

The `workbench` branch also supports music-led narrative films in the same
runtime and storyboard. Read `reference/workbench-contract.txt` for scene editing,
versioned continuity, separate film exports and recoverable paid generation.
Use `/studio?project=SLUG` for this workflow. Preserve the user's explicit host,
project and authorization choices; a prepared local request is not Matrix delivery.

## Entry protocol

When entering this project, do these in order:

1. **Read `MEMORY.md` if supplied locally** — operational state, active features, known bugs. Missing local memory is not proof that a project lacks a script.
2. **Read the relevant skill** — `skills/video-workbench/SKILL.md` for this branch's workbench; `video-studio-e2e` where installed and applicable to narrated PDF explainers.
3. **Check Kanban board** `video-studio` — active/blocked/triage cards
4. **Check wiki** `~/wiki/projects/video-studio/` — research notes, design docs
5. **Verify server** is running on `:8900`:
   ```bash
   curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8900/
   # expect 200
   ```

## Server command

```bash
./start.sh
```

## Critical rules

### Never do a full project render without asking
Scene partials and individual renders are fine. Full-film rendering needs the
project owner's authorization; reuse scope already explicitly granted in the
session. Keep independently planned films separate. Paid generation requires
concrete cost authorization even when a full film was requested.

### Server must restart after ANY code change
FastAPI StaticFiles caches `index.html` at startup. After any change to `app/static/index.html`, restart the server.

### Every change must have tests
Run `pytest tests/ --ignore=tests/test_e2e.py -q` before declaring done.

### Never delete previous generations
Save every iteration. Move old files to `*_iterN/` directories. Each project iteration gets its own dashboard project slug.

### Don't claim something works without verifying
Check actual state with tools (DOM inspection, ffprobe, API calls), not visual screenshots. The subtitle track at 10px font is invisible in screenshots — query `.timeline-sub-block` count in the DOM instead.

## Architecture constraints

- **Audio-first**: narration projects follow narration duration. Music workbench projects preserve the edited music clock and explicit source trims; align the picture instead of padding or stretching music.
- **No video loops**: pre-extend with `video_extend()` (ping-pong). The filter chain has no loop filter.
- **No zoom_in**: zoom_out only on stills, auto-calcs speed to land at native 1.0.
- **Placas max 3-4 per scene**: ffmpeg filter_complex times out >120s with 6+ overlays.
- **Placa alpha**: requires `format=rgba` before `colorchannelmixer=aa=0.7`.
- **Video ambient audio**: mix at 20% gain with TTS narration. Prompt: "No music. Ambient nature sounds only."

## Prompt engineering (Grok Imagine)

- **SCENE first, Style: last** — Grok interprets linearly
- **Character sheets**: embed verbatim in every prompt. Painterly language, not catalog descriptions. Color triad (3 distinctive items). "Small against landscape."
- **No clinical cues, no hype, no magic frequencies**
- **Imagination scenes**: camera LOCKED STILL, painting breathes
- **Reality scenes**: camera MOTION (gentle pan, slow tilt)
- **Critical trap**: "blurred faces" → faceless figures. "rainbow arc" → vivid double-rainbow. "painted faces" → face paint.

## Key files

| File | Purpose |
|---|---|
| `app/main.py` | FastAPI server, all endpoints, SSE bridge |
| `app/static/index.html` | Dashboard (~5600 lines vanilla JS) |
| `lib/assemble.py` | ffmpeg compositor: filter chain, audio mixing |
| `lib/motion.py` | 8 motion presets for stills |
| `lib/tts.py` | Edge TTS + SRT generation |
| `lib/gen_bridge.py` | Agent → dashboard image registration |
| `projects/<slug>/storyboard.json` | Scene definitions, asset timelines |
| `projects/<slug>/MEMORY.md` | Per-project operational state |
| `assets/fonts/` | InterTight-Bold + Inter-Regular (bundled) |

## Known bugs (2026-06-15)

- Subtitle track DOM elements render correctly but are hard to see (10px font, low contrast). Verify with DOM query, not visual.
- `\n` in Python source strings renders as literal newline, breaking embedded JS. Use `String.fromCharCode(10)` or `'\\n'`.

## Project structure for new projects

```
projects/<slug>/
├── SPINE.md            # Narrative map (required)
├── script.md           # Per-scene narration + visual direction
├── DESIGN.md           # Visual design brief (optional)
├── storyboard.json     # PUT by agent via API
├── audio/              # scene-NN.mp3 + scene-NN.srt (server expects this path)
├── scenes/             # Generated stills, videos, placas
├── renders/            # scene-NN.mp4 output (gitignored)
├── exports/            # Final concat videos (gitignored)
└── sources/            # Source documents (PDFs, markdown)
```
