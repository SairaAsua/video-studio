# Continue Here — Video Studio (2026-06-13, post-reset)

## Where we are

**Project reset.** Generated assets from previous session (FAL images, TTS audio,
draft video, storyboard, script, SPINE) all deleted. Reason: FAL outputs were
not satisfying, and the new canonical source corpus from MFM invalidates the
old SPINE/script that Claude Code wrote.

**This is a test project.** `costa-rica` is a sandbox for validating the
video pipeline. The final video is not the goal — the pipeline is. We iterate
on infra (motion, captions, b-roll, smooth rendering) with disposable content
until the machinery is good, then produce real video for real projects.

## Pipeline status (2026-06-13)

- ✅ `app/main.py` running on :8900, dashboard v2 (state-driven, dark theme)
- ✅ `lib/motion.py` — 8 motion presets (zoom_in, drift_l/r, push_in_l/r, parallax_l/r)
- ✅ `lib/captions.py` — kinetic typography (fade + slide-up, Inter Tight/Inter, brand colors)
- ✅ `lib/prototype.py` — mixed-reel validator (still+caption or still+broll+caption)
- ✅ `lib/assemble.py` — multi-scene assembly (needs motion+captions integrated; not yet done)
- ✅ xAI Grok Imagine for image gen (xai-oauth re-authed 2026-06-13, plugin shipped)
- ✅ xAI Grok Imagine for video gen (configured, not used yet)
- ⚠️ `prototype.py` 3-segment concat has a known ffmpeg parser issue — works for 1 segment reliably
- ❌ No images, no audio, no SPINE, no storyboard, no script yet — start from zero

## What to do next (in order)

### Phase 1: Re-synth from canonical sources (use MFM corpus)

1. Read all 6 sources in `sources/` (FROZEN_FACTS first — that's the authority)
2. Load `estilo-wellness` skill for voice/canon
3. Write new `SPINE.md` — 5-6 beats over ~5 min. Use `SHORT_THESIS_EN.md` as
   primary narrative (cleanest frame), pull details from `FROZEN_FACTS.md`
4. Write new `script.md` — per-scene narration following SPINE
5. Audit against `estilo-wellness` — tone, narrative openings, language rules

### Phase 2: Visual storyboard (use Grok Imagine only — FAL removed)

1. Update `DESIGN.md` to remove "FAL" language — say "xAI Grok Imagine" instead
2. Write `storyboard.json` from script — scene visual_direction fields
3. Agent generates stills via `image_generate` (Grok) per scene, quality model
4. Review, iterate, approve in dashboard
5. Optional: generate 1 b-roll with `grok-imagine-video` for a test scene

### Phase 3: Render

1. Apply motion + captions to `lib/assemble.py` (currently only `prototype.py` has them)
2. Re-render full video with new content
3. Compare against previous draft (no longer exists — that's fine)

## Key files

- `sources/` — canonical corpus, DO NOT EDIT
- `DESIGN.md` — visual identity (brand colors, typography, motion mood, style suffix)
- `SPINE.md` — narrative arc (5-6 beats)
- `script.md` — per-scene narration text
- `storyboard.json` — scene data, image versions
- `app/main.py` — FastAPI :8900, image-gen is request-only (agent picks up via SSE)
- `app/static/index.html` — dashboard
- `lib/assemble.py` — ffmpeg assembly (needs motion+captions integration)
- `lib/motion.py`, `lib/captions.py`, `lib/prototype.py` — already built
- `assets/fonts/InterTight-Bold.ttf`, `Inter-Regular.ttf` — bundled

## Commands

```bash
# Start dashboard (if not already running on :8900)
cd ~/Projects/video-studio
~/.hermes/hermes-agent/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8900

# Render video (after storyboard has approved images)
~/.hermes/hermes-agent/venv/bin/python3 lib/assemble.py costa-rica

# Regenerate TTS audio (if script changes)
# Then re-run assemble
```

## Skills to load (before Phase 1)

- `estilo-wellness` — AlterMundi brand voice canon, REQUIRED for any content writing
- `project-memory` — for MEMORY.md conventions

## Nico's preferences (carry over from before)

- English for US investors
- Positive, actionable tone (not "don't do X", but "here's the path")
- Honestidad epistémica as brand asset (candor risk self-disclosure in Beat 6)
- Premium, sober, no pseudoscience, no new-age aesthetic
- Edge TTS free by default, ElevenLabs for premium public videos
- Video should leave viewer wanting more, not exhaust them
- **NEW (2026-06-13): FAL removed — Grok Imagine only for images**
- **NEW (2026-06-13): Reset and start from canonical corpus, not from old SPINE**
