#!/usr/bin/env python3
"""
fal_image.py — FAL still image generation for storyboard scenes.

Reads DESIGN.md style-suffix and locked seed. Generates a still image
per scene via the FAL API (backed by Nous subscription).

Usage:
    python lib/fal_image.py <project-slug> <scene-id> [--prompt <override>]
    python lib/fal_image.py <project-slug> --test-pair --seed 42
    python lib/fal_image.py <project-slug> --batch

Architecture:
    1. Load DESIGN.md → extract style_suffix, seed
    2. Load storyboard.json → get scene visual_direction
    3. Build prompt: style_suffix + " " + visual_direction
    4. Call FAL API (via Hermes' image_generate tool — agent-driven, not direct API)
    5. Save image to projects/<slug>/scenes/<id>.png
    6. Update storyboard.json with image_path, seed, locked status

The actual FAL call is made by the agent via the image_generate tool, not
by this script directly. This script manages the prompt construction,
file I/O, and storyboard state.
"""

import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
PROJECTS = WORKSPACE / "projects"


def load_design(slug: str) -> dict:
    """Load DESIGN.md and extract style params."""
    design_path = PROJECTS / slug / "DESIGN.md"
    if not design_path.exists():
        raise FileNotFoundError(f"DESIGN.md not found for {slug}")

    text = design_path.read_text()
    design = {"style_suffix": "", "seed": 42, "reference_image": None}

    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("**Style suffix"):
            design["style_suffix"] = line.split("`")[1] if "`" in line else ""
        elif line.startswith("**Locked seed:**"):
            try:
                design["seed"] = int(line.split("`")[1])
            except (IndexError, ValueError):
                pass
        elif line.startswith("**Reference image"):
            design["reference_image"] = line.split("`")[1] if "`" in line else None

    return design


def load_storyboard(slug: str) -> dict:
    """Load storyboard.json."""
    sb_path = PROJECTS / slug / "storyboard.json"
    if not sb_path.exists():
        return {"scenes": []}
    return json.loads(sb_path.read_text())


def save_storyboard(slug: str, sb: dict):
    """Write storyboard.json."""
    sb_path = PROJECTS / slug / "storyboard.json"
    sb_path.write_text(json.dumps(sb, indent=2))


def build_prompt(design: dict, scene: dict) -> str:
    """Build FAL prompt: style-suffix + scene visual direction."""
    suffix = design.get("style_suffix", "")
    direction = scene.get("visual_direction", "")
    return f"{suffix} {direction}".strip()


def get_scene_dir(slug: str) -> Path:
    """Ensure and return scenes directory."""
    d = PROJECTS / slug / "scenes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_pair(slug: str, seed: int = 42) -> list[dict]:
    """Generate two images with same seed+style but different prompts to test cross-scene consistency."""
    design = load_design(slug)
    design["seed"] = seed

    pair = [
        {
            "id": 0,
            "time_in": "0:00",
            "time_out": "0:05",
            "text": "Test scene A",
            "visual_direction": "A serene meditation space in Costa Rica at sunset, bamboo architecture, ocean view, warm light",
        },
        {
            "id": 1,
            "time_in": "0:05",
            "time_out": "0:10",
            "text": "Test scene B",
            "visual_direction": "The same meditation space at night, the Beacon device glowing softly, stars visible, contemplative atmosphere",
        },
    ]

    results = []
    for scene in pair:
        prompt = build_prompt(design, scene)
        results.append({
            "scene_id": scene["id"],
            "prompt": prompt,
            "seed": seed,
            "visual_direction": scene["visual_direction"],
        })

    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: fal_image.py <slug> [--test-pair --seed N] [--prompt-list]")
        sys.exit(1)

    slug = sys.argv[1]
    args = sys.argv[2:]

    if "--test-pair" in args:
        seed = 42
        if "--seed" in args:
            try:
                seed = int(args[args.index("--seed") + 1])
            except (IndexError, ValueError):
                pass
        results = test_pair(slug, seed)
        print(json.dumps(results, indent=2))

    elif "--prompt-list" in args:
        # Output all prompts for the agent to feed to image_generate
        design = load_design(slug)
        sb = load_storyboard(slug)
        prompts = []
        for scene in sb.get("scenes", []):
            if not scene.get("locked"):
                prompts.append({
                    "id": scene["id"],
                    "prompt": build_prompt(design, scene),
                    "seed": design["seed"],
                })
        print(json.dumps(prompts, indent=2))

    else:
        print("Specify --test-pair or --prompt-list", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
