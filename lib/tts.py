#!/usr/bin/env python3
"""
tts.py — Text-to-speech generation for video narration.

Uses Edge TTS (free, default) to generate per-scene audio files.

Usage:
    python lib/tts.py <project-slug> [--voice <voice>]
    python lib/tts.py <project-slug> --scene <id> --text "..." [--voice <voice>]

Architecture:
    1. Load storyboard.json → get per-scene text
    2. Call edge-tts CLI for each unlocked scene
    3. Save audio to projects/<slug>/audio/<scene-id>.mp3
    4. Measure durations → update storyboard.json time_in/time_out

Supports: edge-tts (free, default), plus openai, elevenlabs, neutts as
configured providers (agent-driven, not direct API calls).
"""

import json
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
PROJECTS = WORKSPACE / "projects"

DEFAULT_VOICE = "es-MX-JorgeNeural"


def get_audio_dir(slug: str) -> Path:
    d = PROJECTS / slug / "audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def synthesize_edge(text: str, output_path: Path, voice: str = DEFAULT_VOICE) -> float:
    """Synthesize text with edge-tts. Returns duration in seconds."""
    result = subprocess.run(
        [
            "edge-tts",
            "--voice", voice,
            "--text", text,
            "--write-media", str(output_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"edge-tts failed: {result.stderr}")

    # Get duration via ffprobe
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if probe.returncode == 0 and probe.stdout.strip():
        return float(probe.stdout.strip())
    return 0.0


def update_scene_timing(sb: dict, scene_id: int, duration: float, start_time: float):
    """Update scene time_out based on actual TTS duration."""
    for scene in sb.get("scenes", []):
        if scene["id"] == scene_id:
            scene["time_out"] = f"{start_time + duration:.1f}"
            scene["_duration_secs"] = duration
            break


def main():
    if len(sys.argv) < 2:
        print("Usage: tts.py <slug> [--voice <voice>] [--scene <id> --text '...']")
        sys.exit(1)

    slug = sys.argv[1]
    voice = DEFAULT_VOICE

    args = sys.argv[2:]
    single_scene = None
    single_text = None

    i = 0
    while i < len(args):
        if args[i] == "--voice" and i+1 < len(args):
            voice = args[i+1]; i += 2
        elif args[i] == "--scene" and i+1 < len(args):
            single_scene = int(args[i+1]); i += 2
        elif args[i] == "--text" and i+1 < len(args):
            single_text = args[i+1]; i += 2
        else:
            i += 1

    audio_dir = get_audio_dir(slug)

    if single_scene is not None and single_text is not None:
        # Single scene mode
        output_path = audio_dir / f"scene-{single_scene:02d}.mp3"
        duration = synthesize_edge(single_text, output_path, voice)
        print(json.dumps({"scene": single_scene, "duration": duration, "path": str(output_path)}))

    else:
        # Batch mode: read storyboard, synthesize all unlocked scenes
        sb_path = PROJECTS / slug / "storyboard.json"
        if not sb_path.exists():
            print("storyboard.json not found", file=sys.stderr)
            sys.exit(1)

        sb = json.loads(sb_path.read_text())
        scenes = sb.get("scenes", [])
        cumulative = 0.0
        results = []

        for scene in scenes:
            if scene.get("locked"):
                continue
            text = scene.get("text", "")
            if not text.strip():
                continue

            output_path = audio_dir / f"scene-{scene['id']:02d}.mp3"
            duration = synthesize_edge(text, output_path, voice)
            scene["time_in"] = f"{cumulative:.1f}"
            cumulative += duration
            scene["time_out"] = f"{cumulative:.1f}"
            scene["locked"] = True
            results.append({"scene": scene["id"], "duration": duration, "time_in": scene["time_in"], "time_out": scene["time_out"]})

        # Update storyboard with actual timings
        sb_path.write_text(json.dumps(sb, indent=2))
        print(json.dumps({"total_duration": cumulative, "scenes": results}, indent=2))


if __name__ == "__main__":
    main()
