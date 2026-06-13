# DESIGN — Beacon Costa Rica Investors Video

> Project: `costa-rica` · Visual identity for all generated media.
> Status: **Test material** (2026-06-13). This project is a sandbox for
> validating the video pipeline — content, images, audio, and final render
> are disposable, intended to be re-generated as the pipeline matures.

## 0. Image generation provider

- **Provider:** xAI `grok-imagine-image-quality` (default) / `grok-imagine-image` (fast).
- **Auth:** xAI OAuth (`xai-oauth`, SuperGrok subscription).
- **Why Grok and not FAL:** FAL outputs weren't satisfying. Grok is the
  only image provider in this project. (FAL removed 2026-06-13.)
- **Resolution:** 1280x720 landscape (Grok's default landscape output).
  ffmpeg upscale to 1920x1080 happens in `assemble.py` via `scale` filter.

---

## 1. The Beacon wellness world (brand-level visual canon)

This is the **generic Beacon aesthetic** — what the brand looks like across
all contexts. The video-style section (§2) layers on top of this.

### Atmosphere

- **Premium, sober, contemplative.** Not ecstatic, not manic, not new-age.
- **Substance-free, careful, intentional.** No flowers, no mandalas, no
  crystals, no auroras, no purple chakras. No "vibe" aesthetics.
- **Warm but grounded.** The world of a serious practitioner, not a
  festival. Think: a thoughtful room, not a ceremony.

### Setting & architecture

- **Costa Rica / Latin American tropical landscape** with the kind of
  architecture and materials that signal *premium wellness* without
  reading as a generic eco-resort:
  - Bamboo, reclaimed tropical hardwood, stone, raw linen textiles
  - Open-air pavilions, palapas, palenques, floor-to-ceiling openings
  - Interiors that feel like *listening rooms* — minimal, considered,
    not decorated. Dark wood, low warm light, no clutter on surfaces.
- **The Beacon device** (when present): not yet defined as a finished
  design. Until an official render exists, the video **must not show
  any invented device, instrument, speaker, or piece of equipment**.
  Scenes that would otherwise center a device instead use the
  **materials and architecture of a careful practice**: wood grain,
  linen, water in a ceramic bowl, light through palapa thatch. The
  device appears only as **negative space** — a place where it
  *could* be, or as a small warm light source that *could* be from
  it. When an official design is approved, replace these stand-ins
  with reference images. See the `estilo-wellness` skill (anti-pseudoscience
  rule) and FROZEN_FACTS §6: not ambient background music, not a
  generic sound bath, not a medical treatment, not a finished
  commercial product.
- **No people, or only backs of heads / silhouettes / hands.** When a
  human is present, they are *in* the experience — head bowed, eyes
  closed, hands resting. Never a smiling "wellness model" pose.

### Color palette

- **Background:** deep near-black `#0b0f14` and warm dark browns
  `#2a1f15` / `#3d2f20` (the color of shadowed tropical hardwood).
- **Primary accent:** warm gold/amber `#c9a03a` — the Beacon's light, the
  text overlay color, the brand identifier.
- **Secondary:** muted steel blue `#6a7a8e` — for body text and quiet
  elements (subtle reflections, water, fog).
- **Highlight:** soft white `#e0e4ea` — for human skin, hands, linen
  highlights. Never cold or blue-tinted.
- **Forbidden colors:** saturated teal/aqua (Instagram wellness filter),
  purple/magenta gradients (new-age), neon anything, rainbow gradients
  (sound bath generic), overly warm orange (drone real estate).

### Light

- **Golden hour** is the default outdoor light. Soft, low, from the side.
- **Indoor scenes are dim, with one warm light source** (a lamp, the
  Beacon itself, a candle). No overhead lighting. No fluorescent.
- **Mist, fog, low cloud** are common atmospheric layers.
- **No god rays / volumetric drama** unless paired with a contemplative
  stillness; when present, they are subtle, not theatrical.

### Materials & textures (for close-up or detail shots)

- **Wood grain** (tropical hardwood, bamboo, reclaimed beams) — the
  most "Beacon" texture in nature.
- **Raw linen / canvas / undyed cotton** — soft, unbleached, wrinkled.
- **Skin in low warm light** — backs of hands, forehead, shoulder.
- **Water** — flat ocean surface at golden hour, water in a stone bowl,
  dew on a leaf. Not splashing, not dramatic.
- **Stone** — volcanic, dark, unpolished. Costa Rican river stones.

### What NOT to do (Beacon world anti-patterns)

- ❌ Sound bath clichés: floating crystals, cosmic backgrounds, rainbows
- ❌ New-age: chakras, auras, mandalas, lotus flowers, "vibrations"
- ❌ Wellness influencer: bright "lifestyle" photography, white robes,
  smoothie bowls, yoga-pose smiles
- ❌ Tech / startup: blue glow, circuit boards, app UI overlays, screens
- ❌ Real estate drone: hyper-saturated tropical aerial "for sale" shots
- ❌ Stock-photo people: any face visible, any direct eye contact with
  camera
- ❌ Ceremonial: candles arranged, sage burning, group hands stacked,
  "healing circle" framing
- ❌ Sterile / clinical: white rooms, lab equipment, medical aesthetic

---

## 2. Video style (this project's specific visual treatment)

Layered on top of §1. This is **how the video uses the brand world**
across 5-6 minutes.

### Pacing & motion

- **Slow, contemplative camera moves.** Each shot breathes.
- **Allowed motions** (per `lib/motion.py` presets):
  - `drift_left`, `drift_right` — slow lateral pan with subtle zoom in
  - `push_in_left`, `push_in_right` — slow push toward subject with drift
  - `parallax_lr`, `parallax_rl` — zoom in + lateral drift (depth feel)
  - `zoom_in` — slow push toward a focal point
- **Forbidden:** fast zoom, whip pans, jump cuts, shaky-cam, time-lapse
  sped up, fast motion blur.
- **Each shot has motion from the moment it appears.** A still image is
  dead. Slow motion is the minimum.

### Composition

- **16:9 widescreen, 1920×1080 final output.**
- **Subject placement:** rule of thirds, often the subject is left or
  right of center, leaving negative space for the kinetic text overlay
  (which appears in the upper third or center-left/right).
- **Depth layering:** at least 3 planes in most shots — foreground
  (often atmospheric, e.g. out-of-focus leaves, mist), midground
  (subject or scene action), background (context — sky, mountains,
  far trees).
- **Negative space is sacred.** Don't fill the frame; let the image
  breathe. The text overlay needs room to land.

### Color grading (post-look, applied during assembly if desired)

- **Warm shadows** (lift shadows toward `#3d2f20` brown, not gray).
- **Cool highlights desaturated** (highlights lean toward `#e0e4ea`
  white, not blue).
- **Slight overall desaturation** — about 80% saturation. The Beacon
  world is not Technicolor; it's golden hour with restraint.
- **Optional vignette** — subtle, only in dark/indoor shots.

### Text overlays (kinetic typography)

- **Display font:** Inter Tight Bold 700, tight letter-spacing, color
  `#c9a03a` (gold) for key phrases.
- **Body font:** Inter Regular 400, color `#e0e4ea` (soft white) for
  supporting text.
- **Animation:** fade in (0.6s) + slide up (30px) on entry, no exit
  animation (cuts are premium).
- **Position:** centered horizontally, ~10% above center vertically
  (leaves room for any bottom-third caption if needed).
- **Size:** 72px for display phrases, 56px for body phrases, 96px for
  hero/title statements.
- **Shadow:** subtle black shadow for legibility on light backgrounds.
- **No more than 2 phrases visible at once.** Never crowd the frame.

### Continuity & variety across scenes

The video must feel like **one continuous contemplative world**, not
6 disconnected pretty images. Two principles:

1. **Consistent light logic.** All outdoor shots are golden hour (or
   pre-golden-hour). All indoor shots are dim with one warm source.
   No shot breaks this rule.
2. **Visual variation across scenes** — every scene should have a
   different *focal subject, composition, and palette accent* within
   the shared light logic. Examples:
   - Scene 1: aerial coast, gold + teal, wide
   - Scene 2: interior listening room, gold + dark wood, intimate
   - Scene 3: detail (hands, device, water), close, gold-only
   - Scene 4: open architecture (palapa, pavilion), gold + sky, medium
   - Scene 5: people from behind in landscape, gold + earth, wide
   - Scene 6: device glowing in deep dark, gold-only, very intimate

### Motion-mood pairing

| Scene type | Motion preset | Why |
|---|---|---|
| Aerial / wide landscape | `drift_left` or `drift_right` | Mimics a slow-moving drone pass |
| Interior / device | `zoom_in` (slow push) | Attention gathers toward the focal object |
| Detail (hands, water) | `push_in_left` or `push_in_right` | Subjective, intimate, like leaning in |
| People in landscape | `parallax_lr` | Depth between figure and far plane |
| Closing / signature | `zoom_in` (slowest) | Held breath, the heart of the video |

---

## 3. Per-scene prompt (what the artist sees)

The model gets **three blocks** when generating an image:

1. **Beacon world (style canon):** A condensed version of §1 — the
   5-7 most important visual constraints (premium, sober, no faces,
   no new-age, warm light, dark woods, the Beacon device, etc.).
2. **Video style:** A condensed version of §2 — golden hour, contemplative,
   16:9 cinematic, depth layering, negative space for text overlay.
3. **Per-scene visual_direction:** The unique seed for THIS scene — the
   specific subject, composition, focal point, and palette emphasis.

**Critical: the per-scene prompt is the only thing that varies
between regenerations.** Blocks 1 and 2 are constant — they guarantee
visual continuity. Block 3 is the artistic space — the model is invited
to explore within the canon, not to invent outside it.

A regeneration of the same scene should produce a **different image
that still belongs to the same world**. That's the test of a good
prompt: not deterministic replication, but coherent variation.

### Prompt template

```
[BEACON_WORLD_BLOCK]
[VIDEO_STYLE_BLOCK]
[SCENE_VISUAL_DIRECTION_BLOCK]
```

### Beacon world block (template, copy verbatim into image_generate)

```
Premium contemplative wellness aesthetic. Costa Rica tropical setting
or warm-lit interior. Substance-free, non-ceremonial, no new-age
imagery, no crystals or sound-bath clichés. Golden hour outdoor light or
a single warm indoor light source against deep shadow. Materials: tropical
hardwood, bamboo, raw linen, volcanic stone. Color palette: deep near-
black backgrounds, warm gold/amber accent (#c9a03a), muted steel blue
secondary, soft white highlights. A geometric wooden-and-metal device
(the Beacon) may be present as a sculptural anchor — solid, not flashy.
No human faces visible; only backs of heads, silhouettes, or hands
in stillness. Atmosphere of slow attention and care, not celebration.
```

### Video style block (template, copy verbatim)

```
Cinematic widescreen composition, 16:9, 1280x720 or higher. Contemplative
stillness with a sense of held breath. Rule-of-thirds subject placement
leaving room for text overlay in upper third. Three depth planes
(foreground/midground/background) with atmospheric layering — mist, soft
focus, or out-of-focus foreground. Negative space is intentional, not
empty. The image should feel like a single frame from a slow-moving
documentary about a careful practice, not a stock photo. No on-screen
text, no UI elements, no logos, no watermarks.
```

### Per-scene visual_direction

This is the *unique* part per scene. It's written in storyboard.json
under `visual_direction`. It should be ~50-100 words and cover:

- **Subject** — what the image is *of*
- **Composition** — framing, subject placement, foreground/midground/background
- **Light & time of day** — golden hour / dim interior / etc.
- **Palette emphasis** — which accent color leads
- **Mood texture** — the emotional register of this specific shot
- **Specific exclusion** — what to definitely NOT show in this scene

The model is invited to interpret within these constraints — that is
where the artistic exploration lives. **Don't be over-prescriptive.**
Trust the canon; describe the unique seed; let the artist work.

---

## 4. Asset conventions

### Files & paths

- Stills: `projects/costa-rica/scenes/scene-{id:02d}-v{n}.{ext}` (Grok
  outputs JPEG by default, kept as `.jpg`; converted to PNG only if
  ffmpeg requires it for alpha)
- Audio: `projects/costa-rica/audio/scene-{id:02d}.mp3` (Edge TTS)
- Renders: `projects/costa-rica/renders/scene-{id:02d}.mp4`
- Final video: `projects/costa-rica/exports/costa-rica-{quality}.mp4`
  (quality = `draft` or `high`)

### Versioning

- Each scene has an `images[]` array in storyboard.json
- `v1`, `v2`, ... appended as new generations are added
- `current_image` is the index shown in the dashboard
- `approved: true` locks the scene (no more generations)

### Storyboard state

- `locked: true` on a scene → no more generation
- `text` → narration audio
- `visual_direction` → per-scene prompt seed
- `images[].seed` → for cross-scene consistency if model supports it
  (Grok does not expose seed control — this is for future reference)

---

## 5. Style suffix (legacy, kept for back-compat)

The legacy `Style suffix` block from the v1 DESIGN.md is preserved here
verbatim so any in-flight prompts that reference it still work. **New
prompts should use the §3 template blocks, not this single-line suffix.**

```
Premium cinematic photography, warm golden hour light, Costa Rica
tropical setting, high-end wellness retreat aesthetic, photorealistic,
8K, Zeiss lens, natural lighting, shallow depth of field, no text
overlays, no people faces visible, elegant composition
```

---

*DESIGN.md gates all visual generation. Every renderer reads this.
Update here, not in per-scene prompts. The Beacon world canon (§1) and
video style (§2) are stable; the per-scene prompt structure (§3) is
where creative iteration happens.*
