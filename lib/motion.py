"""
motion.py — Ken Burns / cinematic motion presets for still images.

Each preset returns an ffmpeg zoompan expression that produces a smooth,
slow camera move across a still image. Always-moving video, even on stills.

Why this exists: a static still is dead. Slow motion gives the eye
something to track and makes the image feel like a held breath in cinema,
not a slide in a deck.

Presets:
  zoom_in        — classic push-in (subject reveals)
  zoom_out       — gentle pull-back (context reveals)
  drift_left     — lateral slide (pan, world passes by)
  drift_right    — lateral slide opposite
  push_in_left   — push-in with horizontal drift
  push_in_right  — push-in with horizontal drift opposite
  parallax_lr    — slow lateral parallax feel
  parallax_rl    — slow lateral parallax opposite

All presets are designed for ~30s+ durations; the motion is slow and
contemplative, not frantic. Speed is parameterised but defaults to "cinema"
(0.0005 zoom/frame, gentle x drift).
"""

# Default target canvas — match the assembly pipeline
DEFAULT_W = 1920
DEFAULT_H = 1080
# zoompan key insight (per ffmpeg filter docs + community recipes):
#   - With `-loop 1 -framerate 30` + `d=1`, each "input image" is the
#     same still and zoompan processes 1 output frame per image. The
#     internal `zoom` variable RESETS per input image — so a plain
#     `z='min(zoom+0.001, 1.5)'` produces a constant zoom of 1.001
#     (first iteration) for every output frame, no progressive motion.
#   - The fix: use `pzoom` (previous zoom = last output of previous
#     input image) AND/OR `d > 1` (multiple output frames per image,
#     accumulating `zoom` within the chunk). The combined formula
#     `z='min(max(zoom,pzoom)+step, max_zoom)'` works with `d=1` and
#     scales with `-framerate` (more fps → more steps per second).
#   - Source: https://video.stackexchange.com/q/18518
FRAMES = 3  # output frames per input image chunk. higher = smoother interpolation.

# Motion speed: zoom increment per output frame. 0.0005 ≈ 1.0 → 1.10 over 200 frames (~6.7s @ 30fps).
# 0.0003 = slower, more contemplative. 0.0008 = punchier.
SLOW_ZOOM = 0.00035
STD_ZOOM = 0.00055

# Horizontal drift: pixels per output frame. 0.4 ≈ 12px/s @ 30fps — barely perceptible, very cinema.
SLOW_DRIFT = 0.4
STD_DRIFT = 0.7


def zoom_in(w=DEFAULT_W, h=DEFAULT_H, speed=STD_ZOOM, frames=FRAMES):
    """Classic push-in: zoom slowly toward center. Subject reveals."""
    # Input is prescaled to 1.5x so zoompan has pixels to crop into.
    # Use pzoom to persist zoom between input images (loop case).
    return (
        f"scale={int(w*1.5)}:{int(h*1.5)}:force_original_aspect_ratio=increase,"
        f"crop={int(w*1.5)}:{int(h*1.5)},"
        f"zoompan=z='min(max(zoom\\,pzoom)+{speed}\\,1.12)':"
        f"d={frames}:"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"s={w}x{h}"
    )


def zoom_out(w=DEFAULT_W, h=DEFAULT_H, speed=None, duration_secs=None, frames=FRAMES):
    """Pull back: zoom from 1.12 down to 1.0. Context reveals.
    If duration_secs given, auto-calculates speed so last frame lands at zoom=1.0
    (exact match with video first frame — no transition jump)."""
    if speed is None:
        if duration_secs and duration_secs > 0:
            chunks = max(1, int(duration_secs * 30 / frames))
            speed = 0.12 / chunks  # 1.12 → 1.0 in exactly the still's duration
        else:
            speed = STD_ZOOM
    return (
        f"scale={int(w*1.5)}:{int(h*1.5)}:force_original_aspect_ratio=increase,"
        f"crop={int(w*1.5)}:{int(h*1.5)},"
        f"zoompan=z='max(1.0\\,min(max(zoom\\,pzoom)\\,1.12)-{speed})':"
        f"d={frames}:"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"s={w}x{h}"
    )


def drift_left(w=DEFAULT_W, h=DEFAULT_H, speed=STD_ZOOM, drift=SLOW_DRIFT, frames=FRAMES):
    """Slow zoom + horizontal drift left. The world passes by."""
    return (
        f"scale={int(w*1.5)}:{int(h*1.5)}:force_original_aspect_ratio=increase,"
        f"crop={int(w*1.5)}:{int(h*1.5)},"
        f"zoompan=z='min(max(zoom\\,pzoom)+{speed}\\,1.08)':"
        f"d={frames}:"
        f"x='iw/2-(iw/zoom/2)-on*{drift}':"
        f"y='ih/2-(ih/zoom/2)':"
        f"s={w}x{h}"
    )


def drift_right(w=DEFAULT_W, h=DEFAULT_H, speed=STD_ZOOM, drift=SLOW_DRIFT, frames=FRAMES):
    """Slow zoom + horizontal drift right."""
    return (
        f"scale={int(w*1.5)}:{int(h*1.5)}:force_original_aspect_ratio=increase,"
        f"crop={int(w*1.5)}:{int(h*1.5)},"
        f"zoompan=z='min(max(zoom\\,pzoom)+{speed}\\,1.08)':"
        f"d={frames}:"
        f"x='iw/2-(iw/zoom/2)+on*{drift}':"
        f"y='ih/2-(ih/zoom/2)':"
        f"s={w}x{h}"
    )


def push_in_left(w=DEFAULT_W, h=DEFAULT_H, speed=STD_ZOOM, drift=SLOW_DRIFT, frames=FRAMES):
    """Push-in with leftward drift. Subjective, intimate."""
    return (
        f"scale={int(w*1.5)}:{int(h*1.5)}:force_original_aspect_ratio=increase,"
        f"crop={int(w*1.5)}:{int(h*1.5)},"
        f"zoompan=z='min(max(zoom\\,pzoom)+{speed}*1.2\\,1.12)':"
        f"d={frames}:"
        f"x='iw/2-(iw/zoom/2)-on*{drift}':"
        f"y='ih/2-(ih/zoom/2)':"
        f"s={w}x{h}"
    )


def push_in_right(w=DEFAULT_W, h=DEFAULT_H, speed=STD_ZOOM, drift=SLOW_DRIFT, frames=FRAMES):
    """Push-in with rightward drift."""
    return (
        f"scale={int(w*1.5)}:{int(h*1.5)}:force_original_aspect_ratio=increase,"
        f"crop={int(w*1.5)}:{int(h*1.5)},"
        f"zoompan=z='min(max(zoom\\,pzoom)+{speed}*1.2\\,1.12)':"
        f"d={frames}:"
        f"x='iw/2-(iw/zoom/2)+on*{drift}':"
        f"y='ih/2-(ih/zoom/2)':"
        f"s={w}x{h}"
    )


def parallax_lr(w=DEFAULT_W, h=DEFAULT_H, speed=STD_ZOOM, drift=STD_DRIFT, frames=FRAMES):
    """Lateral parallax: zoom in slowly while drifting left. Gives depth feel."""
    return (
        f"scale={int(w*1.5)}:{int(h*1.5)}:force_original_aspect_ratio=increase,"
        f"crop={int(w*1.5)}:{int(h*1.5)},"
        f"zoompan=z='min(max(zoom\\,pzoom)+{speed}\\,1.10)':"
        f"d={frames}:"
        f"x='iw/2-(iw/zoom/2)-on*{drift}':"
        f"y='ih/2-(ih/zoom/2)':"
        f"s={w}x{h}"
    )


def parallax_rl(w=DEFAULT_W, h=DEFAULT_H, speed=STD_ZOOM, drift=STD_DRIFT, frames=FRAMES):
    """Lateral parallax, opposite direction."""
    return (
        f"scale={int(w*1.5)}:{int(h*1.5)}:force_original_aspect_ratio=increase,"
        f"crop={int(w*1.5)}:{int(h*1.5)},"
        f"zoompan=z='min(max(zoom\\,pzoom)+{speed}\\,1.10)':"
        f"d={frames}:"
        f"x='iw/2-(iw/zoom/2)+on*{drift}':"
        f"y='ih/2-(ih/zoom/2)':"
        f"s={w}x{h}"
    )


# Public registry
PRESETS = {
    "zoom_in": zoom_in,
    "zoom_out": zoom_out,
    "zoom_out_slow": lambda w=1920, h=1080: zoom_out(w=w, h=h, speed=0.00035),
    "drift_left": drift_left,
    "drift_right": drift_right,
    "push_in_left": push_in_left,
    "push_in_right": push_in_right,
    "parallax_lr": parallax_lr,
    "parallax_rl": parallax_rl,
}


def get_motion(name: str, w: int = DEFAULT_W, h: int = DEFAULT_H, duration_secs: float = 0.0) -> str:
    """Look up a motion preset by name. Falls back to zoom_in.
    Passes duration_secs to zoom_out for auto-calculated speed."""
    fn = PRESETS.get(name, zoom_in)
    if name == "zoom_out" and duration_secs > 0:
        return fn(w=w, h=h, duration_secs=duration_secs)
    return fn(w=w, h=h)


def list_presets() -> list[str]:
    """All available preset names."""
    return list(PRESETS.keys())
