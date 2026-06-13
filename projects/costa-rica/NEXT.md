# Continue Here — Video Studio (2026-06-13)

## Where we are

Video pipeline is functional end-to-end. First investor pitch video for Beacon+PMP in Costa Rica rendered at `exports/costa-rica-draft.mp4` (4:08 min, 6 scenes). But the current render is just static images with Ken Burns zoom — boring.

## What was accomplished this session

1. **Dashboard** built and refactored (Claude Code v2) — state-driven, dark theme, versioned image stacks
2. **Gen flow** fixed — synchronous FAL via Nous gateway, ~6s per scene, no manual intervention
3. **TTS** — AndrewMultilingualNeural voice (best free Edge option; ElevenLabs available as premium)
4. **Assembly** — ffmpeg Ken Burns + audio concat
5. **SPINE.md** audited with `estilo-wellness` skill (canonical AlterMundi brand voice)
6. **Noustiny** cloned to `~/opt/Noustiny/` for reference

## What to do next: KINETIC TYPOGRAPHY

The video is boring. Priority: make it dynamic.

**Plan:**
1. Add ffmpeg `drawtext` overlays to `lib/assemble.py` — key phrases synced to narration
2. Each scene gets a `captions: [{text, time_in, time_out}]` field in storyboard.json
3. Animate text entrance (fade in, slide up) with ffmpeg filter chains
4. Re-render and compare

**After that:** Option C — mix kinetic typography + video b-roll (`grok-imagine-video`) + stills

## Commands

```bash
# Start dashboard
cd ~/Projects/video-studio
~/.hermes/hermes-agent/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8900

# Render video
~/.hermes/hermes-agent/venv/bin/python3 lib/assemble.py costa-rica

# Regenerate audio (if script changes)
# Then re-run assemble
```

## Key files

- `projects/costa-rica/SPINE.md` — narrative spine (English, 6 beats)
- `projects/costa-rica/DESIGN.md` — visual identity + style suffix
- `projects/costa-rica/storyboard.json` — scene data, images, versions
- `projects/costa-rica/script.md` — per-scene narration text
- `app/main.py` — FastAPI server, FAL gen endpoint
- `app/static/index.html` — dashboard frontend (state-driven)
- `lib/assemble.py` — ffmpeg assembly (needs drawtext)
- `projects/costa-rica/exports/costa-rica-draft.mp4` — current render

## Skills to load

- `estilo-wellness` — AlterMundi brand voice canon
- `hermes-kanban` — board `video-studio`, epic `t_b56cf660`
- `project-memory` — `~/Projects/video-studio/MEMORY.md`

## Nico's preferences for this project

- English for US investors
- Positive, actionable tone (not "don't do X", but "here's the path")
- Honestidad epistémica as brand asset (candor risk self-disclosure in Beat 6)
- Premium, sober, no pseudoscience, no new-age aesthetic
- Edge TTS free by default, ElevenLabs for premium public videos
- Video should leave viewer wanting more, not exhaust them
