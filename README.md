# Video Studio

FastAPI + vanilla JS dashboard for AI-assisted video production. Audio-first timeline editor with ffmpeg assembly, Grok Imagine b-roll, SRT subtitle tracks, and a proven end-to-end PDF→video pipeline.

> **Active project** · 2026-06-15 · Hermes Agent integration via `kanban-video-orchestrator` and `video-studio-e2e` skill.

## What it does

You bring a script or PDF. The dashboard helps you:
1. Extract source material (PDF→PNG, OCR)
2. Write a scene-by-scene SPINE + narration script
3. Generate still images and b-roll video fragments (xAI Grok Imagine)
4. Build a timeline per scene with drag-and-drop blocks
5. Layer text placas, image overlays, and subtitle tracks
6. Assemble everything with ffmpeg: stills + motion presets + video clips + narration audio + ambient mixing
7. Export a single 1920×1080 AAC mp4 with crossfade transitions and painterly post-processing

## Quick start

```bash
cd ~/Projects/video-studio

# Install deps (Python 3.11+)
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# Start the dashboard
HERMES_HOME=~/.hermes .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8900

# Open http://127.0.0.1:8900
```

**Requirements:**
- Python 3.11+
- ffmpeg 7.1+
- xAI OAuth token (for image/video gen)
- Edge TTS (bundled in venv)
- Hermes Agent runtime (for agent-driven workflows)

## Architecture

```
~/Projects/video-studio/
├── app/
│   ├── main.py              # FastAPI :8900. SSE bridge for agent-driven generation.
│   └── static/index.html    # Dashboard (~5600 lines vanilla JS). State-driven.
├── lib/
│   ├── assemble.py           # ffmpeg compositor: audio-first, alpha RGBA, ambient mixing
│   ├── motion.py             # 8 motion presets (zoom_out, drift, push_in, parallax)
│   ├── captions.py           # ffmpeg drawtext kinetic typography
│   ├── tts.py                # Edge TTS with SRT subtitle generation
│   ├── gen_bridge.py         # Agent ↔ dashboard image registration bridge
│   ├── source_synth.py       # Generic source → chunks
│   └── prototype.py          # Mixed reel validator
├── projects/
│   └── _templates/           # DESIGN.md, SPINE.md, brief, storyboard schema templates
├── assets/fonts/             # InterTight-Bold.ttf, Inter-Regular.ttf (bundled for drawtext)
├── tests/                    # pytest + Playwright (API + e2e)
├── scripts/                  # CLI utilities
└── state/                    # JSON state files
```

## Stack

| Capability | Provider |
|---|---|
| Dashboard | FastAPI + vanilla JS, `localhost:8900` |
| Still images | xAI Grok Imagine (`grok-imagine-image-quality`) |
| Video generation | xAI Grok Imagine (`grok-imagine-video`) |
| Voiceover | Edge TTS (en-US-AndrewMultilingualNeural) |
| Subtitle SRT | Edge TTS word/sentence boundary extraction |
| Assembly | ffmpeg 7.1 (filter_complex, zoompan, drawtext, amix, xfade) |
| Agent orchestration | Hermes Kanban (Robin → Grok/Claude for feature work) |

## Proven workflow: PDF → video

The full pipeline is production-verified on 3 projects (beacon-wellness, beacon-journey, costa-rica):

1. **PDF → PNG** — PyMuPDF renders pages at DPI 120
2. **SPINE.md** — 7-10 narrative beats
3. **script.md** — per-scene narration with visual direction
4. **Storyboard** — PUT per scene via API (integer scene_id required)
5. **Anchor stills** — use PDF page renders directly
6. **B-roll** — 2-4 video fragments per scene (5s, 720p)
7. **TTS + SRT** — `lib/tts.py` → `audio/scene-NN.mp3` + `.srt`
8. **Timeline** — drag-and-drop blocks with explicit `time_in`, placas on layer 2
9. **Render scenes** — POST `/render-scene` per scene (draft/high quality)
10. **Concat + post** — ffmpeg concat with xfade transitions + painterly filter

Real-world: 7 scenes, 14 b-roll videos, ~3 minutes, ~25-30 min end-to-end.

## Key design decisions

**Audio-first.** Scene duration is authoritative from the narration audio, not the timeline sum. The ffmpeg canvas matches the audio file; assets shorter than audio get black padding.

**No video loops.** Videos are pre-extended via `video_extend()` (ping-pong: forward→reverse→forward) instead of ffmpeg loop filters, which produce visible glitches.

**Placas on layer 2.** Text overlays use dark indigo background + feathered edges + direct overlay at 70% opacity. Pre-scaled to 75% width with transparent padding. Max 3-4 placas per scene (ffmpeg filter chain timeout).

**Fast switching.** 4-6 visual beats per ~20s scene, alternating stills and video fragments with different images each beat. No same-image still+video back-to-back.

**Explicit time_in.** Base layer assets are sequential; placas have explicit `time_in` for mid-scene appearance. Timeline rendering respects this in the dashboard.

## Dashboard features

- **Script editor** with AI generation from uploaded sources
- **Storyboard gallery** — scene thumbnails with drag reorder
- **Timeline** — drag/trim/snap/undo blocks, color-coded borders (gold=video, blue=still), subtitle track from SRT
- **Image generation** — composition toggles (Beacon World, Image Style, Scene context), multi-select, edit
- **Video generation** — inline preview, edit, extend, catbox.moe upload
- **Resources tab** — drag-drop upload, file preview
- **Exports tab** — full project render with quality selector
- **New project wizard** — one-click from dropdown

## Common pitfalls

- **Integer scene_id required** — PUT endpoints reject string IDs like "s1"
- **Audio path is `audio/scene-NN.mp3`** — zero-padded 2-digit format expected by render
- **Server restart needed after code changes** — FastAPI StaticFiles caches index.html at startup
- **Video URLs expire quickly** — download immediately after `video_generate` returns
- **No zoom_in** — zoom_out only on stills, auto-calculates speed to land at native 1.0
- **Placa alpha requires `format=rgba`** — before `colorchannelmixer=aa=0.7` in filter chain
- **Max 3-4 sequential placas** — 6+ overlays cause ffmpeg filter_complex timeout at 120s

## Hermes Agent integration

This project integrates with Hermes Agent through:

- **Skill:** `video-studio-e2e` — full workflow documentation (loaded via `skill_view`)
- **Kanban board:** `video-studio` — feature development via Robin orchestration
- **Memory:** `MEMORY.md` — operational state, synced by `compaii-state/sync.py`
- **Wiki:** `~/wiki/projects/video-studio/` — research notes, release notes, design docs

### Agent consumption

Agents entering this project should:

1. Read `MEMORY.md` for current operational state + known bugs
2. Load `video-studio-e2e` skill for the canonical workflow
3. Check the Kanban board (`video-studio`) for active/blocked tasks
4. Read wiki at `~/wiki/projects/video-studio/` for design context
5. Verify server is running on `:8900` before testing

### Server lifecycle

```bash
# Start
HERMES_HOME=~/.hermes .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8900

# Restart after code changes (FastAPI caches static HTML)
pkill -f "uvicorn.*8900" && sleep 1 && \
  HERMES_HOME=~/.hermes .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8900

# Test suite
.venv/bin/pytest tests/ -v                         # API tests
.venv/bin/pytest tests/ -v -m slow                 # e2e tests (needs server running)
```

## Rebirth sync

This project is backed up by `compaii-state/sync.py`:
- `MEMORY.md` → operational state snapshot
- Git-tracked files → GitHub remote (`nicoechaniz/video-studio`)
- Wiki notes → `~/wiki/projects/video-studio/`

Run `compaii-sync` or `rebirth` to trigger a full backup.

## Projects directory

The `projects/` directory contains generated project assets (renders, exports, source files). It is **gitignored** — too large for version control (~1.8GB across 7 projects). Only `_templates/` is tracked.

## License

Internal project. Not currently licensed for redistribution.
