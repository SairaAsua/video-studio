"""
captions.py — Kinetic typography overlays for video assembly.

Renders key phrases over a video stream as ffmpeg drawtext filters with
fade-in + slide-up animation. Synced to narration via time_in/time_out.

Why a separate module: ffmpeg drawtext syntax is finicky and easy to break
with escaping bugs. Centralising it here means we can test caption rendering
in isolation from the rest of the assembly pipeline.

Brand (per DESIGN.md):
  Gold  : #c9a03a (primary accent, key phrases)
  White : #e0e4ea (body text)
  Font  : Inter Tight Bold (display) / Inter Regular (body)

Animation: 0.6s fade-in + slide-up from 30px below. No fade-out (cuts feel
premium; let the next phrase replace it).
"""

# Brand colors (per projects/costa-rica/DESIGN.md)
COLOR_GOLD = "#c9a03a"
COLOR_WHITE = "#e0e4ea"

# Font paths — bundled with the repo
FONT_DISPLAY = "assets/fonts/InterTight-Bold.ttf"
FONT_BODY = "assets/fonts/Inter-Regular.ttf"

# Animation params
FADE_IN_SECS = 0.6
SLIDE_PX = 30  # how far below the resting position the text starts

# Pre-computed y positions (canvas 1920x1080, ~96px text height for fontsize 72)
# y_rest ≈ 10% above center: (1080-96)/2 - 108 = 378
Y_REST_DEFAULT = 378
Y_REST_LARGE = 340
Y_REST_SMALL = 410


def _escape_drawtext(s: str) -> str:
    """
    Escape a string for ffmpeg drawtext's `text=` option.

    ffmpeg drawtext interprets: \\ (backslash), : (colon, option separator),
    ' (single quote), % (expansion). We escape these in the order that
    matters for ffmpeg.
    """
    # Order matters: backslashes first, then colons, then percent, then quotes
    s = s.replace("\\", "\\\\")
    s = s.replace(":", "\\:")
    s = s.replace("%", "\\%")
    s = s.replace("'", "\\'")
    return s


def _format_time(seconds: float) -> str:
    """Format seconds as ffmpeg time (e.g. 12.345)."""
    return f"{seconds:.3f}"


def caption_filter(
    text: str,
    time_in: float,
    time_out: float,
    *,
    role: str = "display",  # "display" (Inter Tight, gold) or "body" (Inter, white)
    x: str = "(w-text_w)/2",  # centered horizontally
    y_rest: int = Y_REST_DEFAULT,
    font_size: int = 72,
    fade_in: float = FADE_IN_SECS,
    slide_px: int = SLIDE_PX,
) -> str:
    """
    Build a single drawtext filter string for one caption.

    The animation is:
      - alpha goes 0 → 1 over `fade_in` seconds starting at time_in
      - y interpolates from (y_rest + slide_px) → y_rest over the same window
      - visible from time_in to time_out

    Note: y_rest is an absolute pixel value (not an ffmpeg expression).
    Mixing math expressions with ffmpeg option parsing has bitten us before
    (multiplication/parentheses in option values confuse the parser).
    """
    font = FONT_DISPLAY if role == "display" else FONT_BODY
    color = COLOR_GOLD if role == "display" else COLOR_WHITE
    safe = _escape_drawtext(text)

    t_in = _format_time(time_in)
    t_out = _format_time(time_out)
    t_fade_end = _format_time(time_in + fade_in)
    fade_in_str = _format_time(fade_in)

    y_start = y_rest + slide_px
    # Inside a single-quoted option value in ffmpeg filter syntax, commas
    # are still treated as option-value separators by the outer filter
    # parser. They MUST be escaped as `\,` (single backslash before comma)
    # to be passed through as expression syntax.
    #
    # Python string -> ffmpeg character mapping:
    #   f"\\,"    -> string `\,`  (1 backslash + 1 comma)  ← what ffmpeg needs
    #   f"\\\,," -> string `\\,` (2 backslashes + 1 comma) ← wrong, double escape
    #
    # So we use single `\\,` in the f-string, which the runtime emits as `\,`.
    y_expr = (
        f"if(lt(t\\,{t_in})\\,{y_start}\\,"
        f"if(lt(t\\,{t_fade_end})\\,{y_start}-{slide_px}*(t-{t_in})/{fade_in_str}\\,"
        f"{y_rest}))"
    )

    # alpha: 0 before t_in, linear 0→1 from t_in to t_fade_end, 1 thereafter, 0 after t_out
    alpha_expr = (
        f"if(lt(t\\,{t_in})\\,0\\,"
        f"if(lt(t\\,{t_fade_end})\\,(t-{t_in})/{fade_in_str}\\,"
        f"if(lt(t\\,{t_out})\\,1\\,0)))"
    )

    # Same escape rule for `enable=between(t\,a\,b)`.
    enable_expr = f"between(t\\,{t_in}\\,{t_out})"

    parts = [
        f"text='{safe}'",
        f"fontfile={font}",
        f"fontsize={font_size}",
        f"fontcolor={color}",
        "borderw=0",
        "shadowcolor=black@0.7",
        "shadowx=2",
        "shadowy=2",
        f"x={x}",
        f"y='{y_expr}'",
        f"alpha='{alpha_expr}'",
        f"enable='{enable_expr}'",
    ]
    return "drawtext=" + ":".join(parts)


def build_caption_chain(captions: list[dict]) -> str:
    """
    Build a comma-separated chain of drawtext filters from a list of caption dicts.

    Each caption dict:
      {
        "text": "...",
        "time_in": float,   # seconds from scene start
        "time_out": float,  # seconds from scene start
        "role": "display" | "body",  # optional, default display
        "font_size": int,   # optional, default 72
      }
    """
    filters = []
    for cap in captions:
        f = caption_filter(
            text=cap["text"],
            time_in=cap["time_in"],
            time_out=cap["time_out"],
            role=cap.get("role", "display"),
            font_size=cap.get("font_size", 72),
        )
        filters.append(f)
    return ",".join(filters)


# Example / self-test
if __name__ == "__main__":
    sample = [
        {"text": "$1.2 billion market", "time_in": 0.5, "time_out": 3.5, "role": "display"},
        {"text": "growing at double digits", "time_in": 3.8, "time_out": 6.5, "role": "body", "font_size": 56},
        {"text": "That's the gap.", "time_in": 7.0, "time_out": 9.5, "role": "display"},
    ]
    print(build_caption_chain(sample))
