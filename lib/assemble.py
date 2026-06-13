#!/usr/bin/env python3
"""
assemble.py — ffmpeg video assembly using the asset_timeline model.

Takes storyboard.json (scenes with "asset_timeline" + optional "_duration_secs")
+ per-scene Edge TTS .mp3 audio → per-scene re-encoded MP4 clips → concat
(with -c copy) to final export.

Pipeline (asset_timeline only, no legacy code paths):
  1. Load storyboard.json for scenes.
  2. For each scene, render_scene() builds a filter_complex from
     scene["asset_timeline"]:
       - black base canvas (color=c=black:s=1920x1080:r=30:d=...)
       - CLI inputs: one -framerate 30 -loop 1 -t <span> -i <path> per asset
       - assets sorted by layer ASC (0 = bottom/base)
       - per asset: motion (get_motion or "none" scale/crop) → [mv{i}]
         → opacity via format=rgba,colorchannelmixer=aa= → [mo{i}]
         → setpts shift to asset.time_in → [s{i}]
         → timed overlay onto accumulating [base{i}]/[comp{i}] → [comp{i+1}]
       - after overlays: collect ALL caption_phrases across assets, offset
         each phrase by its asset's time_in (asset-local → scene time),
         call build_caption_chain() to produce final [v]
       - audio: _strip_filter_for_audio_check (unchanged) for [aud];
         if no audio file, inject "anullsrc" lavfi input
  3. Encode per-scene clip (libx264 + aac, draft/high presets, -shortest)
  4. Concat all scene clips with stream copy (-c copy).

Supported: multi-layer assets, staggered/overlapping timing (time_in>0),
per-asset motion or "none", per-asset opacity (incl. 0), asset-local
captions, zero-asset (black+audio), single-asset full-span.
Only "still" assets. No backward compatibility.

Usage:
    python lib/assemble.py <project-slug> [--quality draft|high]
    python lib/assemble.py <project-slug> --scene <id> [--quality draft|high]
    python lib/assemble.py <project-slug> --debug-filter --scene <id>
"""

import json
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


def _norm_asset(a: dict, scene_dur: float = 0.0) -> dict:
    """Normalise one asset dict from the timeline.

    Handles legacy field names from the T1 fixture (duration_secs,
    motion_preset) → contract field names (time_out, motion).
    """
    if a.get("type") != "still":
        raise RuntimeError(
            f"unsupported asset type {a.get('type')!r} (only 'still' supported)"
        )
    t_in = float(a.get("time_in", 0.0))
    if "time_out" in a and a["time_out"] is not None:
        t_out = float(a["time_out"])
    elif "duration_secs" in a and a["duration_secs"] is not None:
        t_out = t_in + float(a["duration_secs"])
    else:
        t_out = t_in + max(scene_dur, 5.0)

    motion = a.get("motion") or a.get("motion_preset") or "drift_left"
    return {
        "layer": int(a.get("layer", 0)),
        "path": a.get("path"),
        "time_in": t_in,
        "time_out": t_out,
        "motion": motion,
        "opacity": float(a.get("opacity", 1.0)),
        "caption_phrases": a.get("caption_phrases") or [],
    }


def render_scene(scene: dict, paths: dict, quality: str = "draft",
                 width: int = 1920, height: int = 1080,
                 debug_filter: bool = False) -> Path:
    """
    Render a single scene from its asset_timeline → mp4 (re-encoded).

    Builds a single ffmpeg filter_complex:
      black base → sorted-by-layer stills (motion → opacity → setpts shift
      → timed overlay) → caption chain over final composite → audio.
    """
    scene_id = scene["id"]

    audio_path = paths["audio"] / f"scene-{scene_id:02d}.mp3"
    output_path = paths["renders"] / f"scene-{scene_id:02d}.mp4"
    paths["renders"].mkdir(parents=True, exist_ok=True)

    raw_assets = scene.get("asset_timeline", [])
    if not raw_assets:
        raise RuntimeError(f"Scene {scene_id}: asset_timeline is empty")

    scene_dur = float(scene.get("_duration_secs", 0.0))

    # Normalize all assets (includes type check)
    norm = [_norm_asset(a, scene_dur) for a in raw_assets]

    # Sort by layer ascending (0 = bottom)
    norm.sort(key=lambda x: x["layer"])

    # Derive scene duration if missing
    if scene_dur <= 0:
        scene_dur = max(a["time_out"] for a in norm) if norm else 5.0

    # ---- Build CLI inputs and filter_complex graph ----
    inputs = []
    filter_parts = []

    # a. Black base (synthetic, no CLI -i)
    filter_parts.append(
        f"color=c=black:s={width}x{height}:r=30:d={scene_dur}[base0]"
    )

    # b. Per-asset processing
    for i, asset in enumerate(norm):
        span = asset["time_out"] - asset["time_in"]
        if span <= 0:
            span = 0.001
        rel = asset["path"]
        if not rel:
            raise RuntimeError(
                f"Scene {scene_id}: asset at layer {asset['layer']} has no path"
            )
        resolved = paths["scenes"].parent / rel
        inputs.extend([
            "-framerate", "30", "-loop", "1",
            "-t", f"{span:.6f}",
            "-i", str(resolved),
        ])

        # Motion
        if asset["motion"] == "none":
            mv = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
        else:
            mv = get_motion(asset["motion"], w=width, h=height)
        filter_parts.append(f"[{i}:v]{mv}[mv{i}]")

        # Opacity
        filter_parts.append(
            f"[mv{i}]format=rgba,colorchannelmixer=aa={asset['opacity']}[mo{i}]"
        )

        # Time-shift to asset's scene position
        filter_parts.append(
            f"[mo{i}]setpts=PTS-STARTPTS+{asset['time_in']}/TB[s{i}]"
        )

        # Timed overlay (overlay needs single-backslash comma escaping:
        #   f"between(t\\,{v1}\\,{v2})"  →  between(t\,v1\,v2)
        prev = "base0" if i == 0 else f"comp{i}"
        filter_parts.append(
            f"[{prev}][s{i}]overlay="
            f"enable='between(t\\,{asset['time_in']}\\,{asset['time_out']})'"
            f":x=0:y=0[comp{i + 1}]"
        )

    # c. Captions — collect all, offset asset-local → scene time
    n = len(norm)
    last_comp = f"comp{n}" if n > 0 else "base0"
    all_captions = []
    for asset in norm:
        off = asset["time_in"]
        for cap in asset["caption_phrases"]:
            c = dict(cap)
            c["time_in"] = off + float(cap.get("time_in", 0.0))
            c["time_out"] = off + float(cap.get("time_out", 0.0))
            all_captions.append(c)

    if all_captions:
        caption_chain = build_caption_chain(all_captions)
        filter_parts.append(f"[{last_comp}]{caption_chain}[v]")
    else:
        filter_parts.append(f"[{last_comp}]copy[v]")

    # d/e. Audio — reuse unchanged helper; patch [1:a] for multi-input layout
    audio_filters = _strip_filter_for_audio_check(audio_path, scene_dur)
    audio_filter_str = ",".join(audio_filters)
    if "[1:a]" in audio_filter_str:
        audio_filter_str = audio_filter_str.replace("[1:a]", f"[{n}:a]")

    filter_complex = ";".join(filter_parts) + ";" + audio_filter_str

    # f. Audio input (or anullsrc lavfi fallback)
    if audio_path.exists():
        inputs.extend(["-i", str(audio_path)])
    else:
        inputs.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])

    # ---- Assemble command ----
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[aud]",
        "-t", str(scene_dur),
        "-c:v", "libx264",
        "-preset", "fast" if quality == "draft" else "slow",
        "-crf", "28" if quality == "draft" else "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]

    if debug_filter:
        print(filter_complex, file=sys.stderr)
        return output_path

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"--- ffmpeg STDERR for scene {scene_id} ---", file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed for scene {scene_id}")
    return output_path


def concat_scenes(scene_paths: list[Path], output_path: Path,
                  quality: str = "draft"):
    """
    Concatenate rendered scene clips into the final video using
    -c copy (stream copy, no re-encode).
    """
    if not scene_paths:
        raise RuntimeError("No scene paths to concatenate")

    concat_file = output_path.parent / "concat.txt"
    lines = [f"file '{p.resolve()}'" for p in scene_paths]
    concat_file.write_text("\n".join(lines) + "\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"--- ffmpeg concat STDERR ---", file=sys.stderr)
        print(result.stderr[-1500:], file=sys.stderr)
        raise RuntimeError("ffmpeg concat failed")
    return output_path


def main():
    if len(sys.argv) < 2:
        print("Usage: assemble.py <slug> [--quality draft|high] "
              "[--scene <id>] [--debug-filter]")
        sys.exit(1)

    slug = sys.argv[1]
    quality = "draft"
    single_scene = None
    debug_filter = False

    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--quality" and i + 1 < len(args):
            quality = args[i + 1]; i += 2
        elif args[i] == "--scene" and i + 1 < len(args):
            single_scene = int(args[i + 1]); i += 2
        elif args[i] == "--debug-filter":
            debug_filter = True; i += 1
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
        output = render_scene(scene, paths, quality,
                              debug_filter=debug_filter)
        print(f"Rendered: {output}")
        return

    # Pre-render gate: every scene must have ≥1 approved still asset
    invalid, unapproved = [], []
    for s in scenes:
        stills = [a for a in s.get("asset_timeline", [])
                  if a.get("type") == "still" and a.get("path")]
        if not stills:
            invalid.append(s["id"])
        elif not any(a.get("approved") for a in stills):
            unapproved.append(s["id"])
    if invalid:
        print(f"ERROR: {len(invalid)} scenes without a valid 'still' asset "
              f"(type+path): {invalid}")
    if unapproved:
        if debug_filter:
            print(f"DEBUG: {len(unapproved)} scenes without approved asset "
                  f"(proceeding for filter debug): {unapproved}")
        else:
            print(f"ERROR: {len(unapproved)} scenes without an approved "
                  f"asset: {unapproved}")
    if invalid or (unapproved and not debug_filter):
        sys.exit(1)

    print(f"Rendering {len(scenes)} scenes (quality={quality})…")
    rendered = []
    for scene in scenes:
        n_assets = len(scene.get("asset_timeline", []))
        dur = scene.get("_duration_secs", 0.0)
        print(f"  scene {scene['id']:02d} ({dur:.1f}s, {n_assets} asset(s))")
        output = render_scene(scene, paths, quality,
                              debug_filter=debug_filter)
        rendered.append(output)

    if debug_filter:
        print("\n[debug-filter] Filter graphs printed; "
              "no files rendered, concat skipped.")
        return

    # Concat
    paths["exports"].mkdir(parents=True, exist_ok=True)
    final = paths["exports"] / f"{slug}-{quality}.mp4"
    concat_scenes(rendered, final, quality)
    print(f"\nFinal: {final} ({final.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
