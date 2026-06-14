# video-studio

End-to-end pipeline for turning narrative documents (PDFs, slide decks, briefs)
into pitch-style videos. FastAPI dashboard + xAI Grok Imagine + Edge TTS.

## What it does

Takes a PDF or slide deck as input and produces a 2-3 minute 1920x1080 AAC mp4
with:
- 5-7 narrative scenes
- Still "thesis cards" (PDF page renders) as anchors
- 5-second video b-roll fragments from Grok Imagine per scene
- Edge TTS narration per scene
- Simple cut transitions (xfade in the pipeline, crossfades in final)

## The proven workflow

See `skills/creative/video-studio-e2e/SKILL.md` for the full 11-step procedure.
The first proven run was 2026-06-14 with `projects/beacon-wellness/`:
- Input: 12-slide PDF
- Output: `projects/beacon-wellness/exports/beacon-wellness-pilot-v1.mp4`
- Duration: 2:57
- Cost: ~$1-1.50 in Grok Imagine video + free TTS
- Wall clock: 20-30 min for the full build

## Project layout

```
projects/<slug>/
├── sources/              # Source documents (PDFs, briefs)
│   └── presentation-pdf/ # PDF + page renders (PNG, DPI 120)
├── SPINE.md              # 5-7 scene narrative map
├── script.md             # Per-scene narration + display phrase + b-roll prompts
├── storyboard.json       # Server-side storyboard (7 scenes, integer ids)
├── scenes/               # All visual assets
│   ├── s{N}_anchor.png   # Per-scene thesis card (PDF page render)
│   ├── s{N}_v{1,2}.mp4   # Per-scene Grok b-roll fragments
│   └── s{N}_narration.mp3  # (optional) TTS, before move to audio/
├── audio/                # Server-read location for TTS
│   └── scene-NN.mp3      # Zero-padded, what render-scene reads
├── renders/              # Per-scene rendered mp4
│   └── scene-0{N}.mp4
└── exports/              # Final concat
    └── <slug>-pilot-v{N}.mp4
```

## Server endpoints (FastAPI on :8900)

```
GET    /api/project/{slug}                      # full state
PUT    /api/project/{slug}/script               # body: {content: "..."}
PUT    /api/project/{slug}/storyboard/{sid}     # body: scene fields (integer sid)
PUT    /api/project/{slug}/storyboard/{sid}/timeline  # body: {asset_timeline: [...]}
POST   /api/project/{slug}/storyboard/{sid}/render-scene  # body: {quality: draft|high}
POST   /api/project/{slug}/storyboard/{sid}/render-scene/delete
GET    /api/project/{slug}/media/{file_path:path}  # serve any project file
```

## Running the server

```bash
HERMES_HOME=/home/nicolas/.hermes ~/Projects/video-studio/.venv/bin/python \
  -m uvicorn app.main:app --host 127.0.0.1 --port 8900
```

## Required config

`~/.hermes/config.yaml`:
```yaml
image_gen:
  provider: xai
  model: grok-imagine-image
  xai:
    model: grok-imagine-image-quality
video_gen:
  provider: xai
  model: grok-imagine-video
```

xAI auth via `xai-oauth` (handled by `hermes auth add xai-oauth --type oauth`).

## Skills

- `creative/video-studio-e2e` — the full workflow. Load when starting a new
  PDF→video project or sharing with a new agent.
- `estilo-wellness` — required for any Beacon / wellness content. Catches
  clinical framing, magic frequencies, hype words.

## Tests

```bash
cd ~/Projects/video-studio
.venv/bin/pytest tests/ -v -m "not slow"          # API tests, fast
.venv/bin/pytest tests/ -v -m slow                # E2E (needs server running)
```

## Status (2026-06-14)

- v4.x dashboard shipped (Resources, AI Script gen, test suite, image/video
  editing, Export, Captions, xfade, blend modes)
- BUG t_bb025a7b (timeline properties + bin duplicate) fixed
- E2E PDF→Video pipeline proven on beacon-wellness project
- 1 follow-up card: distribute workflow to tribe (README + skill + Robin test)
