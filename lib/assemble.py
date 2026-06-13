#!/usr/bin/env python3
"""
assemble.py — ffmpeg video assembly.

Takes storyboard images + per-scene audio → renders final MP4.

Pipeline:
  1. Load storyboard.json → scene timings, image paths, motion_preset, captions
  2. Load per-scene audio files (Edge TTS .mp3)
  3. For each scene: still image + motion preset + drawtext captions + audio
     → temp clip (MP4 with h264 video + aac audio, re-encoded)
  4. Concat all clips with -c copy (stream copy, no re-encode) → final.mp4
  5. Update storyboard.json with the final path

This per-scene-then-concat approach is more robust than a single
complex -filter_complex graph: each scene is independent, can be
inspected individually, and concat with -c copy is trivial.

Usage:
    python lib/assemble.py <project-slug> [--quality draft|high]
    python lib/assemble.py <project-slug> --scene <id>  # render single scene for preview
"""

import json
import re
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
PROJECTS = WORKSPACE / "projects"
sys.path.insert(0, str(WORKSPACE / "lib"))

from motion import get_motion  # noqa: E402
from captions import build_caption_chain, Y_REST_DEFAULT  # noqa: E402


def get_paths(slug: str) -> dict:
    base = PROJECTS / slug
    return {
        "storyboard": base / "storyboard.json",
        "scenes": base / "scenes",
        "audio": base / "audio",
        "renders": base / "renders",
        "exports": base / "exports",
    }


def _strip_filter_for_audio_check(audio_path: Path, duration: float) -> list:
    """Build the audio part of a per-scene filter graph.

    If audio is missing, returns an empty filter (no audio). Otherwise
    returns a single-element list with anullsrc fallback to guarantee
    the audio track exists at the correct duration.
    """
    if not audio_path.exists():
        return [f"anullsrc=r=44100:cl=stereo[aud]"]
    return [f"[1:a]aresample=44100,apad,atrim=0:{duration},asetpts=PTS-STARTPTS[aud]"]


def render_scene(scene: dict, paths: dict, quality: str = "draft",
                 width: int = 1920, height: int = 1080,
                 motion_override: str | None = None) -> Path:
    """
    Render a single scene: still image + motion preset + captions + audio
    → mp4 (re-encoded, then concat'd later with -c copy).
    """
    scene_id = scene["id"]

    # Resolve image
    versions = scene.get("images", [])
    approved = next((v for v in versions if v.get("approved")), None) if versions else None
    current_idx = scene.get("current_image", 0)
    current = versions[current_idx] if versions and current_idx < len(versions) else None
    image_info = approved or current
    if not image_info:
        raise RuntimeError(f"Scene {scene_id}: no approved or current image")
    image_path = paths["scenes"].parent / image_info["path"]

    audio_path = paths["audio"] / f"scene-{scene_id:02d}.mp3"
    output_path = paths["renders"] / f"scene-{scene_id:02d}.mp4"
    paths["renders"].mkdir(parents=True, exist_ok=True)

    duration = scene.get("_duration_secs", 5.0)

    # Video: motion preset + (optional) caption chain
    motion_preset = motion_override if motion_override else scene.get("motion_preset", "drift_left")
    motion = get_motion(motion_preset, w=width, h=height)
    captions = scene.get("caption_phrases", [])
    if captions:
        caption_chain = build_caption_chain(captions)
        video_filter = f"[0:v]{motion},{caption_chain}[v]"
    else:
        video_filter = f"[0:v]{motion}[v]"

    # Audio
    audio_filter = _strip_filter_for_audio_check(audio_path, duration)
    audio_filter_str = ",".join(audio_filter)

    # Combine — single input if no audio
    if not audio_path.exists():
        cmd = [
            "ffmpeg", "-y",
            "-framerate", "30", "-loop", "1", "-i", str(image_path),
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-filter_complex", f"{video_filter};{audio_filter_str}",
            "-map", "[v]", "-map", "[aud]",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast" if quality == "draft" else "slow",
            "-crf", "28" if quality == "draft" else "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-framerate", "30", "-loop", "1", "-i", str(image_path),
            "-i", str(audio_path),
            "-filter_complex", f"{video_filter};{audio_filter_str}",
            "-map", "[v]", "-map", "[aud]",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast" if quality == "draft" else "slow",
            "-crf", "28" if quality == "draft" else "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(output_path),
        ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"--- ffmpeg STDERR for scene {scene_id} ---", file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed for scene {scene_id}")
    return output_path


def concat_scenes(scene_paths: list[Path], output_path: Path, quality: str = "draft"):
    """
    Concatenate rendered scene clips into the final video using
    -c copy (stream copy, no re-encode). Trivially robust.
    """
    if not scene_paths:
        raise RuntimeError("No scene paths to concatenate")

    concat_file = output_path.parent / "concat.txt"
    lines = []
    for p in scene_paths:
        # ffmpeg concat demuxer requires absolute paths or paths relative
        # to the concat file's location. Use absolute.
        lines.append(f"file '{p.resolve()}'")
    concat_file.write_text("\n".join(lines) + "\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        "-movflags", "+faststart",  # web-friendly MP4 with metadata at start
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"--- ffmpeg concat STDERR ---", file=sys.stderr)
        print(result.stderr[-1500:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg concat failed")
    return output_path


def main():
    if len(sys.argv) < 2:
        print("Usage: assemble.py <slug> [--quality draft|high] [--scene <id>]")
        sys.exit(1)

    slug = sys.argv[1]
    quality = "draft"
    single_scene = None
    motion_override = None

    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--quality" and i + 1 < len(args):
            quality = args[i + 1]; i += 2
        elif args[i] == "--scene" and i + 1 < len(args):
            single_scene = int(args[i + 1]); i += 2
        elif args[i] == "--motion" and i + 1 < len(args):
            motion_override = args[i + 1]; i += 2
        else:
            i += 1

    paths = get_paths(slug)
    sb = json.loads(paths["storyboard"].read_text())
    scenes = sb.get("scenes", [])

    if single_scene is not None:
        scene = next((s for s in scenes if s["id"] == single_scene), None)
        if not scene:
            print(f"Scene {single_scene} not found", file=sys.stderr)
            sys.exit(1)
        output = render_scene(scene, paths, quality, motion_override=motion_override)
        print(f"Rendered: {output}")
        return

    # All scenes must have approved images
    unapproved = [s for s in scenes if not any(v.get("approved") for v in s.get("images", []))]
    if unapproved:
        print(f"ERROR: {len(unapproved)} scenes without approved image: {[s['id'] for s in unapproved]}")
        sys.exit(1)

    print(f"Rendering {len(scenes)} scenes (quality={quality})…")
    rendered = []
    for scene in scenes:
        print(f"  scene {scene['id']:02d} ({scene['_duration_secs']:.1f}s, "
              f"motion={scene.get('motion_preset', '?')}, "
              f"captions={len(scene.get('caption_phrases', []))})")
        output = render_scene(scene, paths, quality)
        rendered.append(output)

    # Concat
    paths["exports"].mkdir(parents=True, exist_ok=True)
    final = paths["exports"] / f"{slug}-{quality}.mp4"
    concat_scenes(rendered, final, quality)
    print(f"\nFinal: {final} ({final.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
