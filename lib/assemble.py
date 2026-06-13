#!/usr/bin/env python3
"""
assemble.py — ffmpeg video assembly for the asset_timeline pipeline.

Multi-layer per-scene composition + concat with optional cross-scene transitions.

Pipeline:
  - Per scene: black 1920x1080 base + multi-layer composition from
    asset_timeline[] (not images[])
  - Per-asset motion (via `motion` field; uses motion.py presets or "none")
  - Per-asset opacity (colorchannelmixer)
  - Per-asset captions (offset by asset time_in → build_caption_chain)
  - Bottom-up overlay by layer asc (blend_mode support with fallback)
  - Audio unchanged (per-scene narration + anullsrc fallback)
  - Per-scene mp4 (re-encoded) → xfade transitions + acrossfade audio for final export

Note: engine currently uses sequential `duration_secs` per-asset for
time windows; absolute `time_in`/`time_out` overlay support is a known gap.

Drops all references to: images[], motion_preset, approved, motion_override.
The data model field for motion is `motion` (per-asset).

Transition support (cross-scene):
  Storyboard JSON may include top-level field:
      "transition": {"type": "fade|wipe_left|wipe_right|dissolve|none", "duration_secs": 0.5}
  Default (if absent or null): {"type": "fade", "duration_secs": 0.5}
  "none" (or 1 scene) falls back to stream-copy concat demuxer (original fast path, no re-encode).
  Supported xfade names are mapped: "fade"→"fade", "wipe_left"→"wipeleft",
  "wipe_right"→"wiperight", "dissolve"→"dissolve".
  Duration is probed per input scene via ffprobe; total output duration is
  sum(durs) - (N-1)*transition_dur to account for overlaps.

Usage:
    python lib/assemble.py <project-slug> [--quality draft|high] [--debug-filter]
    python lib/assemble.py <project-slug> --scene <id> [--debug-filter]  # render single scene for preview
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
                 debug_filter: bool = False) -> Path:
    """
    Render a single scene from asset_timeline (multi-still layered composition):
    black canvas + per-asset looped stills (with per-asset motion + offset captions + opacity)
    + optional audio → mp4 (re-encoded).
    Full asset_timeline only.
    """
    scene_id = scene["id"]

    audio_path = paths["audio"] / f"scene-{scene_id:02d}.mp3"
    output_path = paths["renders"] / f"scene-{scene_id:02d}.mp4"
    paths["renders"].mkdir(parents=True, exist_ok=True)

    timeline = scene.get("asset_timeline", [])
    if not timeline:
        raise RuntimeError(f"Scene {scene_id}: asset_timeline is empty")

    # Build asset list with original positions (for input indices); sort by layer asc for timeline + z-order
    asset_list = [{"pos": pos, "asset": asset} for pos, asset in enumerate(timeline)]
    sorted_assets = sorted(asset_list, key=lambda x: x["asset"].get("layer", 0))

    # Validate types and compute cumulative timeline positions + total
    cum = 0.0
    for item in sorted_assets:
        a = item["asset"]
        t = a.get("type")
        if t not in ("still", "video"):
            raise RuntimeError(f"Scene {scene_id}: unsupported asset type '{t}' (only 'still' and 'video' supported)")
        dur = float(a.get("duration_secs", 0.0))
        item["time_in"] = cum
        item["dur"] = dur
        cum += dur
    total_duration = cum if cum > 0 else 5.0

    # Input layout (indices):
    # 0: black canvas (lavfi color with explicit d=total)
    # 1: audio (real or anullsrc placeholder for _strip compatibility)
    # 2+: still inputs (in original asset_timeline order)
    canvas_idx = 0
    audio_slot_idx = 1
    first_still_idx = 2

    # Build input args list (order determines indices)
    cmd_inputs: list[str] = [
        "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=30:d={total_duration}"
    ]
    if audio_path.exists():
        cmd_inputs += ["-i", str(audio_path)]
    else:
        cmd_inputs += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]

    for entry in asset_list:
        asset = entry["asset"]
        resolved = paths["scenes"].parent / asset["path"]
        atype = asset.get("type") or "still"
        if atype == "video":
            t_in = float(asset.get("time_in", 0.0))
            dsecs = float(asset.get("duration_secs", 5.0))
            cmd_inputs += ["-ss", str(t_in), "-t", str(dsecs), "-i", str(resolved)]
        else:
            cmd_inputs += ["-loop", "1", "-framerate", "30", "-i", str(resolved)]

    # Audio filter (unchanged helper; relies on audio at index 1 when real audio present)
    audio_filter = _strip_filter_for_audio_check(audio_path, total_duration)
    audio_filter_str = ",".join(audio_filter)

    # Per-asset processing filters -> [l{pos}]
    processing_filters: list[str] = []
    for entry in asset_list:
        pos = entry["pos"]
        asset = entry["asset"]
        still_idx = first_still_idx + pos
        layer = int(asset.get("layer", 0))
        t_offset = entry["time_in"]

        # motion per-asset (spec field is `motion`; legacy `motion_preset` fallback for data compat)
        atype = asset.get("type") or "still"
        if atype == "still":
            m_preset = asset.get("motion") or asset.get("motion_preset")
            if m_preset:
                motion = get_motion(m_preset, w=width, h=height)
            else:
                motion = None
        else:
            # video: passthrough scale+crop instead of get_motion
            motion = f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease,crop={width}:{height}"

        # captions: offset relative times by this asset's scene time_in
        caps = asset.get("caption_phrases", []) or []
        if caps:
            adjusted = []
            for c in caps:
                c2 = dict(c)
                c2["time_in"] = float(c.get("time_in", 0.0)) + t_offset
                c2["time_out"] = float(c.get("time_out", 0.0)) + t_offset
                adjusted.append(c2)
            cap_chain = build_caption_chain(adjusted)
        else:
            cap_chain = None

        # opacity for overlays (layer > 0); default 1.0
        opacity = float(asset.get("opacity", 1.0))

        # build chain
        in_ref = f"[{still_idx}:v]"
        l_label = f"[l{pos}]"
        parts: list[str] = []
        if motion:
            parts.append(motion)
        if cap_chain:
            parts.append(cap_chain)
        if layer > 0 or opacity != 1.0:
            # apply for layer>0 blending (even at 1.0 is harmless)
            parts.append(f"colorchannelmixer=aa={opacity}")

        if parts:
            chain = ",".join(parts)
            filt = f"{in_ref}{chain}{l_label}"
        else:
            filt = f"{in_ref}null{l_label}"
        processing_filters.append(filt)

    # Supported blend modes (verified with ffmpeg 6.x+)
    # Unsupported by ffmpeg blend=all_mode: color-dodge, color-burn, hard-light, soft-light
    SUPPORTED_BLEND_MODES = {"normal", "multiply", "screen", "overlay", "darken", "lighten", "difference", "exclusion"}
    # Progressive overlay chain following sorted_assets order (first in sorted is base on canvas, no enable)
    overlay_filters: list[str] = []
    current = "[bg]"
    for i, item in enumerate(sorted_assets):
        pos = item["pos"]
        t_in = item["time_in"]
        t_out = t_in + item["dur"]
        l_label = f"[l{pos}]"
        is_last = (i == len(sorted_assets) - 1)
        out_label = "[v]" if is_last else f"[ov{i}]"
        t_in_s = f"{t_in:.3f}"
        t_out_s = f"{t_out:.3f}"
        # Blend mode support (T4): read from asset dict, default "normal"
        blend_mode = (item["asset"].get("blend_mode") or "normal")
        if blend_mode != "normal" and blend_mode not in SUPPORTED_BLEND_MODES:
            print(f"WARNING: blend_mode '{blend_mode}' not supported by ffmpeg -- using normal overlay", file=sys.stderr)
        use_blend = blend_mode != "normal" and blend_mode in SUPPORTED_BLEND_MODES

        if i == 0:
            # base (lowest layer after sort) onto black; no enable (persists)
            if use_blend:
                ov = f"{current}{l_label}blend=all_mode={blend_mode}{out_label}"
            else:
                ov = f"{current}{l_label}overlay=0:0{out_label}"
        else:
            # higher (later in sorted) with time window enable
            if use_blend:
                ov = f"{current}{l_label}blend=all_mode={blend_mode}:enable='between(t" + chr(92) + ",{t_in_s}" + chr(92) + ",{t_out_s})'{out_label}"
            else:
                ov = f"{current}{l_label}overlay=0:0:enable='between(t" + chr(92) + ",{t_in_s}" + chr(92) + ",{t_out_s})'{out_label}"
        overlay_filters.append(ov)
        current = out_label

    # Full filter_complex: label canvas as [bg]; per-asset stills; overlays; audio
    filter_parts: list[str] = [f"[0:v]null[bg]"] + processing_filters + overlay_filters
    filter_complex = ";".join(filter_parts) + ";" + audio_filter_str

    # Assemble ffmpeg cmd
    cmd = [
        "ffmpeg", "-y",
        *cmd_inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[aud]",
        "-t", str(total_duration),
        "-c:v", "libx264", "-preset", "fast" if quality == "draft" else "slow",
        "-crf", "28" if quality == "draft" else "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]

    if debug_filter:
        print(" ".join(cmd))
        return output_path

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"--- ffmpeg STDERR for scene {scene_id} ---", file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed for scene {scene_id}")
    return output_path


def _probe_duration(path: Path) -> float:
    """Probe media duration in seconds using ffprobe (format duration)."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"--- ffprobe STDERR for {path} ---", file=sys.stderr)
        print(result.stderr[-1500:], file=sys.stderr)
        raise RuntimeError(f"ffprobe failed for {path}")
    try:
        return float(result.stdout.strip())
    except Exception:
        raise RuntimeError(f"Could not parse duration for {path}: {result.stdout.strip()!r}")


def concat_scenes(scene_paths: list[Path], output_path: Path,
                  transition: dict | None = None, quality: str = "draft") -> Path:
    """
    Concatenate rendered scene clips.

    If transition is None, type=="none", or only 1 scene: use legacy stream-copy
    concat demuxer (fast, no re-encode, writes concat.txt sidecar).

    Otherwise: build xfade + acrossfade filter_complex pipeline (re-encodes).
    Probed durations are used to compute exact xfade offsets and total output -t.
    Prints a one-line progress message before encoding.
    """
    if not scene_paths:
        raise RuntimeError("No scene paths to concatenate")

    # Normalize transition spec (caller may pass None or omit)
    if transition is None:
        transition = {"type": "none", "duration_secs": 0.5}
    t_type = (transition.get("type") or "fade") if isinstance(transition, dict) else "none"
    t_dur = float(transition.get("duration_secs", 0.5)) if isinstance(transition, dict) else 0.5

    N = len(scene_paths)
    if t_type == "none" or N < 2:
        # Fallback: original stream-copy concat (preserves exact behavior)
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

    # --- xfade / acrossfade path (N >= 2 and transition type != none) ---
    xfade_map = {
        "fade": "fade",
        "wipe_left": "wipeleft",
        "wipe_right": "wiperight",
        "dissolve": "dissolve",
    }
    xname = xfade_map.get(t_type, "fade")

    # Probe all input durations
    durs = [_probe_duration(p) for p in scene_paths]
    total_dur = sum(durs) - t_dur * (N - 1)
    if total_dur <= 0:
        # Degenerate (over-long transition); fall back to sum without overlap trim
        total_dur = sum(durs)

    print(f"Concatenating {N} scenes with {t_type} {t_dur}s transitions (total: {total_dur:.1f}s)…")

    # Video xfade chain:
    # [0:v][1:v]xfade=...[x1]
    # [x1][2:v]xfade=...[x2]
    # ... last -> [vout]
    v_parts: list[str] = []
    prev = "[0:v]"
    for i in range(N - 1):
        nxt = f"[{i+1}:v]"
        out = "[vout]" if i == N - 2 else f"[x{i+1}]"
        offset = sum(durs[:i+1]) - (i + 1) * t_dur
        v_parts.append(
            f"{prev}{nxt}xfade=transition={xname}:duration={t_dur:.3f}:offset={offset:.3f}{out}"
        )
        prev = out
    video_chain = ";".join(v_parts)

    # Audio acrossfade chain (no explicit offset; acrossfade aligns to input lengths):
    # [0:a][1:a]acrossfade=d=...[a1]
    # [a1][2:a]acrossfade=...[a2]
    # ... -> [aout]
    a_parts: list[str] = []
    prev = "[0:a]"
    for i in range(N - 1):
        nxt = f"[{i+1}:a]"
        out = "[aout]" if i == N - 2 else f"[a{i+1}]"
        a_parts.append(f"{prev}{nxt}acrossfade=d={t_dur:.3f}{out}")
        prev = out
    audio_chain = ";".join(a_parts)

    filter_complex = f"{video_chain};{audio_chain}"

    # Build input list (-i for every scene mp4)
    cmd_inputs: list[str] = []
    for p in scene_paths:
        cmd_inputs += ["-i", str(p)]

    preset = "fast" if quality == "draft" else "slow"
    crf = "28" if quality == "draft" else "18"

    cmd = [
        "ffmpeg", "-y",
        *cmd_inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", preset,
        "-crf", crf, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-t", f"{total_dur:.3f}",
        str(output_path),
    ]

    # Trivial support for quality="debug" (print only; caller normally skips via debug_filter)
    if quality == "debug":
        print(" ".join(cmd))
        return output_path

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        print(f"--- ffmpeg xfade concat STDERR ---", file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg xfade concat failed")
    return output_path


def main():
    if len(sys.argv) < 2:
        print("Usage: assemble.py <slug> [--quality draft|high] [--scene <id>] [--debug-filter]")
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

    # Top-level transition (optional); default to fade 0.5s cross-scene xfade
    transition = sb.get("transition")
    if transition is None:
        transition = {"type": "fade", "duration_secs": 0.5}

    if single_scene is not None:
        scene = next((s for s in scenes if s["id"] == single_scene), None)
        if not scene:
            print(f"Scene {single_scene} not found", file=sys.stderr)
            sys.exit(1)
        output = render_scene(scene, paths, quality, debug_filter=debug_filter)
        print(f"Rendered: {output}")
        return

    # All scenes must have at least one 'still' or 'video' asset with a valid path
    # in their asset_timeline before a full render.
    invalid = []
    for s in scenes:
        assets = [a for a in s.get("asset_timeline", [])
                  if (a.get("type") in ("still", "video")) and a.get("path")]
        if not assets:
            invalid.append(s["id"])
    if invalid:
        print(f"ERROR: {len(invalid)} scenes without a valid 'still' or 'video' asset (type+path): {invalid}")
        sys.exit(1)

    print(f"Rendering {len(scenes)} scenes (quality={quality})…")
    rendered = []
    for scene in scenes:
        asset = (scene.get("asset_timeline") or [{}])[0]
        print(f"  scene {scene['id']:02d} ({asset.get('duration_secs', 0.0):.1f}s, "
              f"motion={asset.get('motion') or asset.get('motion_preset', '?')}, "
              f"captions={len(asset.get('caption_phrases', []))})")
        output = render_scene(scene, paths, quality, debug_filter=debug_filter)
        rendered.append(output)

    if debug_filter:
        print("\n[debug-filter] Commands printed; no files rendered, concat skipped.")
        return

    # Concat
    paths["exports"].mkdir(parents=True, exist_ok=True)
    final = paths["exports"] / f"{slug}-{quality}.mp4"
    concat_scenes(rendered, final, transition, quality)
    print(f"\nFinal: {final} ({final.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
