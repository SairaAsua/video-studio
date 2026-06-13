#!/usr/bin/env python3
"""
prototype.py — Build a 10-12s mixed reel to validate visual direction.

Combines:
  - Cinematic still (scene-01-v2.png) with motion preset (push_in_left)
  - Kinetic typography overlay (key phrase fading in mid-clip)
  - A short video b-roll clip (grok-imagine-video, 3-4s)
  - Audio: first ~10s of scene-01 narration

Output: ~/Projects/video-studio/projects/costa-rica/exports/prototype-reel.mp4

This is a one-off design validation. If Nico likes the feel, we port
the motion+caption pattern to lib/assemble.py and replicate across all 6
scenes.
"""
import json
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
PROJECT = WORKSPACE / "projects" / "costa-rica"
ASSETS = WORKSPACE / "assets" / "fonts"
BROLLS = WORKSPACE / "projects" / "costa-rica" / "brolls"
EXPORTS = PROJECT / "exports"
EXPORTS.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(WORKSPACE / "lib"))
from motion import get_motion  # noqa: E402
from captions import build_caption_chain  # noqa: E402


def build_still_segment(in_label: str, out_label: str, motion: str, captions: list[dict]) -> str:
    """Build an ffmpeg filter chain for a still-with-motion + captions segment."""
    motion_filter = get_motion(motion)
    caption_chain = build_caption_chain(captions)
    if caption_chain:
        return f"{in_label}{motion_filter},{caption_chain}{out_label}"
    return f"{in_label}{motion_filter}{out_label}"


def build_video_segment(in_label: str, out_label: str, duration: float) -> str:
    """Build filter for a video b-roll segment — no motion, no captions, just trimmed + scaled."""
    return (
        f"{in_label}trim=0:{duration},setpts=PTS-STARTPTS,"
        f"scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=30{out_label}"
    )


def _split_filter_chain(chain: str) -> list[str]:
    """
    DEPRECATED — kept for reference only. The current approach relies on
    bare commas inside single-quoted option values (e.g. y='if(lt,a,b)')
    being parsed correctly by ffmpeg, which is true when the surrounding
    filter chain is well-formed. We do not split chains in production.

    If future ffmpeg versions break this assumption, this function is
    the recovery path: split on bare commas at paren-depth=0, in_quote=False,
    inject intermediate link labels between the resulting entries, and
    join with `;` instead of `,`.
    """
    parts: list[str] = []
    buf: list[str] = []
    in_quote = False
    paren_depth = 0
    i = 0
    while i < len(chain):
        ch = chain[i]
        if ch == "\\" and i + 1 < len(chain):
            buf.append(ch)
            buf.append(chain[i + 1])
            i += 2
            continue
        if ch == "'":
            in_quote = not in_quote
            buf.append(ch)
            i += 1
            continue
        if not in_quote:
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth = max(0, paren_depth - 1)
        if ch == "," and not in_quote and paren_depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def concat_segments(segment_specs: list[dict], audio_source: Path, output: Path):
    """
    Build full ffmpeg command from segment specs.

    Each spec: {type: 'still'|'video', source: Path, duration: float, motion?: str, captions?: list}
    The audio is sliced from audio_source across the total duration.
    """
    inputs = []
    filter_parts = []
    concat_inputs = []

    total = sum(s["duration"] for s in segment_specs)

    for i, seg in enumerate(segment_specs):
        out_label = f"[v{i}]"
        if seg["type"] == "still":
            inputs.extend(["-loop", "1", "-i", str(seg["source"])])
            inner = build_still_segment(
                in_label=f"[{i}:v]",
                out_label=out_label,
                motion=seg.get("motion", "zoom_in"),
                captions=seg.get("captions", []),
            )
            filter_parts.append(inner)
        else:
            inputs.extend(["-i", str(seg["source"])])
            inner = build_video_segment(
                in_label=f"[{i}:v]",
                out_label=out_label,
                duration=seg["duration"],
            )
            filter_parts.append(inner)
        concat_inputs.append(out_label)

    concat_filter = "".join(concat_inputs) + f"concat=n={len(segment_specs)}:v=1:a=0[outv]"
    filter_complex = ";\n".join(filter_parts) + ";\n" + concat_filter

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-i", str(audio_source),
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", f"{len(segment_specs)}:a",
        "-t", str(total),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(output),
    ]
    print("CMD:", " ".join(cmd[:8]) + " ...")
    print(f"Filter complex length: {len(filter_complex)} chars")
    # print last 600 of filter_complex for debugging
    print(f"--- filter_complex (last 600) ---\n{filter_complex[-600:]}")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        print("FFMPEG STDERR (first 2500):", file=sys.stderr)
        print(result.stderr[:2500], file=sys.stderr)
        print("FFMPEG STDERR (last 1500):", file=sys.stderr)
        print(result.stderr[-1500:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed for prototype render")
    return output


def main():
    # Find source assets
    still = PROJECT / "scenes" / "scene-01-v2.png"
    audio = PROJECT / "audio" / "scene-01.mp3"
    broll = BROLLS / "clip-01.mp4"  # expected: a 3-4s b-roll from grok-imagine-video

    assert still.exists(), f"Missing still: {still}"
    assert audio.exists(), f"Missing audio: {audio}"

    segments = []

    # Segment 1: still with motion + first kinetic phrase (~4s)
    if broll.exists():
        # Mixed reel: still → broll → still
        segments = [
            {
                "type": "still", "source": still, "duration": 4.0,
                "motion": "drift_left",
                "captions": [
                    {"text": "$1.2 billion market", "time_in": 0.4, "time_out": 3.5, "role": "display"},
                ],
            },
            {
                "type": "video", "source": broll, "duration": 3.5,
            },
            {
                "type": "still", "source": still, "duration": 3.0,
                "motion": "push_in_right",
                "captions": [
                    {"text": "That's the gap.", "time_in": 0.5, "time_out": 3.0, "role": "display"},
                ],
            },
        ]
    else:
        print(f"[info] No broll at {broll} — still-only reel.", file=sys.stderr)
        segments = [
            {
                "type": "still", "source": still, "duration": 4.0,
                "motion": "drift_left",
                "captions": [
                    {"text": "$1.2 billion market", "time_in": 0.4, "time_out": 3.5, "role": "display"},
                ],
            },
            {
                "type": "still", "source": still, "duration": 3.5,
                "motion": "zoom_in",
                "captions": [
                    {"text": "growing at double digits", "time_in": 0.3, "time_out": 3.2, "role": "body", "font_size": 56},
                ],
            },
            {
                "type": "still", "source": still, "duration": 3.0,
                "motion": "push_in_right",
                "captions": [
                    {"text": "That's the gap.", "time_in": 0.5, "time_out": 3.0, "role": "display"},
                ],
            },
        ]

    output = EXPORTS / "prototype-reel.mp4"
    print(f"Rendering prototype → {output}")
    concat_segments(segments, audio, output)
    print(f"Done: {output} ({output.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
