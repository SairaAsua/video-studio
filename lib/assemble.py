#!/usr/bin/env python3
"""
assemble.py — ffmpeg video assembly.

Takes storyboard images + per-scene audio → renders final MP4.

Usage:
    python lib/assemble.py <project-slug> [--quality draft|high]
    python lib/assemble.py <project-slug> --scene <id>  # render single scene for preview

Architecture:
    1. Load storyboard.json → scene timings, image paths
    2. Load per-scene audio files
    3. For each scene: still image + Ken Burns zoom + narration → temp clip
    4. Concat all clips → final.mp4
    5. Also produce vertical cut if configured

Quality modes:
    draft: 720p, CRF 28, fast (for iteration)
    high: 1080p, CRF 18, slow (for delivery)
"""

import json
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
PROJECTS = WORKSPACE / "projects"


def get_paths(slug: str) -> dict:
    """Resolve project paths."""
    base = PROJECTS / slug
    return {
        "storyboard": base / "storyboard.json",
        "scenes": base / "scenes",
        "audio": base / "audio",
        "renders": base / "renders",
        "exports": base / "exports",
    }


def render_scene(scene: dict, paths: dict, quality: str = "draft",
                 width: int = 1920, height: int = 1080) -> Path:
    """Render a single scene: still + Ken Burns + audio → mp4."""
    scene_id = scene["id"]
    
    # Get image from the versioned images array
    versions = scene.get("images", [])
    approved = next((v for v in versions if v.get("approved")), None) if versions else None
    current = versions[scene.get("current_image", 0)] if versions and scene.get("current_image", 0) < len(versions) else None
    image_info = approved or current
    if not image_info:
        raise RuntimeError(f"Scene {scene_id}: no approved or current image")
    image_path = paths["scenes"].parent / image_info["path"]
    
    audio_path = paths["audio"] / f"scene-{scene_id:02d}.mp3"
    output_path = paths["renders"] / f"scene-{scene_id:02d}.mp4"
    paths["renders"].mkdir(parents=True, exist_ok=True)

    # Get duration from storyboard or audio
    duration = scene.get("_duration_secs", 5.0)

    # Ken Burns effect: slow zoom + subtle pan
    # zoom from 1.0 to 1.1 over duration
    zoom_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"zoompan=z='min(zoom+0.0005,1.1)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-i", str(audio_path) if audio_path.exists() else None,
        "-filter_complex", zoom_filter,
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "fast" if quality == "draft" else "slow",
        "-crf", "28" if quality == "draft" else "18",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(output_path),
    ]

    # Remove None entries (when audio is missing)
    cmd = [c for c in cmd if c is not None]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for scene {scene_id}: {result.stderr[-500:]}")

    return output_path


def concat_scenes(scene_paths: list[Path], output_path: Path, quality: str = "draft"):
    """Concatenate rendered scene clips into final video."""
    concat_file = output_path.parent / "concat.txt"
    concat_file.write_text("".join(f"file '{p.resolve()}'\n" for p in scene_paths))

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {result.stderr[-500:]}")


def main():
    if len(sys.argv) < 2:
        print("Usage: assemble.py <slug> [--quality draft|high] [--scene <id>]")
        sys.exit(1)

    slug = sys.argv[1]
    quality = "draft"
    single_scene = None

    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--quality" and i+1 < len(args):
            quality = args[i+1]; i += 2
        elif args[i] == "--scene" and i+1 < len(args):
            single_scene = int(args[i+1]); i += 2
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
        output = render_scene(scene, paths, quality)
        print(f"Rendered: {output}")

    else:
        # Render all scenes that have an approved image version
        unapproved = [s for s in scenes if not any(v.get("approved") for v in s.get("images", []))]
        if unapproved:
            print(f"ERROR: {len(unapproved)} scenes without approved image: {[s['id'] for s in unapproved]}")
            sys.exit(1)

        rendered = []
        for scene in scenes:
            output = render_scene(scene, paths, quality)
            rendered.append(output)

        # Concat
        paths["exports"].mkdir(parents=True, exist_ok=True)
        final = paths["exports"] / f"{slug}-{quality}.mp4"
        concat_scenes(rendered, final, quality)
        print(f"Final: {final}")


if __name__ == "__main__":
    main()
