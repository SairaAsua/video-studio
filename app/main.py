#!/usr/bin/env python3
from __future__ import annotations
"""
video-studio dashboard — FastAPI server on localhost:8900.

Serves the two-pane editor (script + storyboard) and artifact CRUD endpoints.
Filesystem is the single source of truth — no database.

The data model is asset_timeline-based: each scene holds an asset_timeline[]
list of still assets (with per-asset fields for composition).

Endpoints:
    GET  /                                  → dashboard HTML
    GET  /api/projects                      → list project slugs
    GET  /api/project/watch                 → SSE stream (all projects)
    GET  /api/project/{slug}                → {script, storyboard, scene_media} (storyboard.scenes[].asset_timeline)
    PUT  /api/project/{slug}/script         → write script.md
    PUT  /api/project/{slug}/storyboard/{scene_id}              → update scene fields (incl. asset_timeline)
    POST /api/project/{slug}/storyboard/{scene_id}/image        → request generation (agent listens via SSE)
    GET  /api/project/{slug}/storyboard/{scene_id}/timeline     → get asset_timeline
    PUT  /api/project/{slug}/storyboard/{scene_id}/timeline     → set/replace asset_timeline (emits timeline-updated SSE)
    POST /api/project/{slug}/storyboard/{scene_id}/render-scene → render single-scene preview (asset_timeline-driven)
    POST /api/project/{slug}/render-scene-preview → render scene preview with export {scene_id,width,height,crf,preset} (maps preset)
    POST /api/project/{slug}/render         → trigger render (gated on still asset with path in asset_timeline); optional body for full export params
    GET  /api/project/{slug}/export-presets → list presets for Export tab
    POST /api/project/{slug}/render-scene-preview → export current scene with {width,height,crf,preset} + scene_id
    (full /render also accepts ExportParams body to drive assemble with custom settings)

Image paths in storyboard.json are always relative to the project directory
(e.g. "scenes/scene-01-v1.png") and served at /projects/{slug}/{path}.

Image generation is request-only — the agent (CompAII) listens on the SSE
stream and calls xAI Grok Imagine via the image_generate tool. FAL was
removed 2026-06-13. See MEMORY.md for rationale.

Usage:
    python app/main.py
    uvicorn app.main:app --host 127.0.0.1 --port 8900
"""

import asyncio
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

WORKSPACE = Path(__file__).resolve().parent.parent
PROJECTS = WORKSPACE / "projects"
STATIC = Path(__file__).resolve().parent / "static"

SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")

app = FastAPI(title="Video Studio")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def project_dir(slug: str) -> Path:
    if not SLUG_RE.match(slug):
        raise HTTPException(400, f"Invalid project slug '{slug}'")
    return PROJECTS / slug


def load_storyboard(slug: str) -> tuple[Path, dict]:
    sb_path = project_dir(slug) / "storyboard.json"
    if not sb_path.exists():
        raise HTTPException(404, "storyboard.json not found")
    return sb_path, json.loads(sb_path.read_text())


def save_storyboard(sb_path: Path, sb: dict) -> None:
    sb_path.write_text(json.dumps(sb, indent=2))


def find_scene(sb: dict, scene_id: int) -> dict:
    scene = next((s for s in sb.get("scenes", []) if s.get("id") == scene_id), None)
    if scene is None:
        raise HTTPException(404, f"Scene {scene_id} not found")
    return scene


def normalize_image_path(slug: str, raw: str) -> str:
    """Coerce an image path to project-relative form ("scenes/scene-01-v1.png").

    Accepts workspace-relative ("projects/<slug>/scenes/...") or leading-slash
    variants the agent might send; rejects anything escaping the project dir.
    """
    path = raw.strip().lstrip("/")
    for prefix in (f"projects/{slug}/", f"{slug}/"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    if not path or ".." in Path(path).parts or Path(path).is_absolute():
        raise HTTPException(400, f"Invalid image path '{raw}'")
    return path


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------

sse_clients: list[asyncio.Queue] = []


async def sse_notify(slug: str, event: str = "refresh") -> None:
    """Push an event to all connected dashboard clients."""
    data = json.dumps({"slug": slug, "event": event})
    for q in sse_clients:
        await q.put(data)


@app.get("/api/project/watch")
async def sse_watch(request: Request):
    """SSE stream for all projects. MUST be routed before /api/project/{slug}."""
    q: asyncio.Queue = asyncio.Queue()
    sse_clients.append(q)

    async def event_generator():
        try:
            yield f"data: {json.dumps({'event': 'connected'})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'event': 'ping'})}\n\n"
        finally:
            sse_clients.remove(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    # no-store: the dashboard is a single mutable HTML file; stale copies were
    # the source of "my fix isn't showing up" bugs.
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/projects")
async def list_projects():
    """List all project slugs."""
    if not PROJECTS.exists():
        return []
    return sorted(
        d.name for d in PROJECTS.iterdir()
        if d.is_dir() and not d.name.startswith(("_", "."))
    )


@app.get("/api/project/{slug}")
async def get_project(slug: str):
    """Return all artifacts for a project."""
    proj = project_dir(slug)
    if not proj.exists():
        raise HTTPException(404, f"Project '{slug}' not found")

    result = {"slug": slug}

    for artifact in ["brief.md", "DESIGN.md", "SPINE.md", "script.md"]:
        path = proj / artifact
        if path.exists():
            result[artifact.removesuffix(".md")] = path.read_text()

    sb_path = proj / "storyboard.json"
    if sb_path.exists():
        result["storyboard"] = json.loads(sb_path.read_text())

    # Generated images, project-relative — same convention as storyboard.json.
    # The bin is filesystem-sourced: any supported image/video type counts.
    scenes_dir = proj / "scenes"
    if scenes_dir.exists():
        media_exts = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".webm", ".mov"}
        result["scene_media"] = sorted(
            str(p.relative_to(proj)) for p in scenes_dir.glob("*.*")
            if p.suffix.lower() in media_exts
        )

    return result


class ScriptUpdate(BaseModel):
    content: str


@app.put("/api/project/{slug}/script")
async def update_script(slug: str, body: ScriptUpdate):
    """Write script.md."""
    proj = project_dir(slug)
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "script.md").write_text(body.content)
    await sse_notify(slug, "script-updated")
    return {"ok": True}


class SceneUpdate(BaseModel):
    text: str | None = None
    visual_direction: str | None = None
    asset_timeline: list[dict] | None = None


@app.put("/api/project/{slug}/storyboard/{scene_id}")
async def update_scene(slug: str, scene_id: int, body: SceneUpdate):
    """Update a single scene in storyboard.json."""
    sb_path, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)
    for field, value in body.model_dump(exclude_none=True).items():
        scene[field] = value
    save_storyboard(sb_path, sb)
    await sse_notify(slug, "storyboard-updated")
    return {"ok": True, "scene": scene_id}


# ---------------------------------------------------------------------------
# Image generation (request-only — agent does the work via Grok Imagine)
# ---------------------------------------------------------------------------
# The dashboard endpoint below enqueues a "scene-needs-image" SSE event. The
# agent (CompAII) listens on the SSE stream, calls `image_generate` with
# xAI Grok Imagine, saves the resulting image, and records it in the scene's
# asset_timeline. FAL is not used — see MEMORY.md for rationale (2026-06-13).
#
# Why request-only (not server-side FAL call):
# - xAI Grok OAuth tokens live in Hermes' auth pool, accessible only to the
#   agent runtime. The dashboard process cannot resolve them.
# - The agent can pick the right model (`grok-imagine-image-quality` for
#   hero shots, `grok-imagine-image` for fast iteration) per-scene.
# - Keeps image generation aligned with how the agent does everything else.

def _load_design_style(slug: str) -> tuple[str, int]:
    """Extract style suffix and seed from DESIGN.md.

    Kept as a helper for the agent (which uses it to build the prompt
    when it receives the SSE event). The dashboard does not call this
    directly anymore.
    """
    design_path = project_dir(slug) / "DESIGN.md"
    if not design_path.exists():
        return "", 42
    text = design_path.read_text()
    suffix = ""
    seed = 42
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("**Style suffix"):
            if "`" in line:
                suffix = line.split("`")[1]
        elif line.startswith("**Locked seed:**"):
            try:
                seed = int(line.split("`")[1])
            except (IndexError, ValueError):
                pass
    return suffix, seed


@app.post("/api/project/{slug}/storyboard/{scene_id}/image")
async def request_scene_image(slug: str, scene_id: int):
    """
    Enqueue a "generate image for this scene" request. The agent listens
    on the SSE stream, calls Grok Imagine via the image_generate tool,
    saves the image to scenes/, and records it in the scene's asset_timeline.
    """
    slug = slug.strip()
    project_dir(slug)  # validate

    sb_path, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)
    await sse_notify(slug, f"image-requested:{scene_id}")
    return {"ok": True, "scene": scene_id, "status": "requested", "provider": "xai-grok-imagine"}


@app.post("/api/project/{slug}/render")
async def trigger_render(slug: str, body: Optional["ExportParams"] = None):  # ExportParams defined later in file; string fwd ref for annotation eval
    """Trigger assembly. Gates on every scene having at least one 'still'
    asset with a path in its asset_timeline.
    If body with width/height/crf/preset provided (from Export tab), performs
    a synchronous full export render and returns the resulting file url.
    """
    _, sb = load_storyboard(slug)
    missing_still = [
        s["id"] for s in sb.get("scenes", [])
        if not any(
            a.get("type") == "still" and a.get("path")
            for a in s.get("asset_timeline", [])
        )
    ]
    if missing_still:
        raise HTTPException(400, f"Scenes missing still asset in timeline: {missing_still}")

    if body is None:
        # legacy / tools-render buttons: just queue notification (agent or manual may pick up)
        await sse_notify(slug, "render-queued")
        return {"ok": True, "message": "Render queued"}

    # Export full with params: run assemble synchronously
    exports_dir = project_dir(slug) / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    # Map the preset to assemble.py --preset flag (fast|balanced|quality)
    p = (body.preset or "medium").lower()
    if p in {"ultrafast", "superfast", "veryfast", "faster", "fast"}:
        mapped_preset = "fast"
    elif p in {"slow", "slower", "veryslow"}:
        mapped_preset = "quality"
    else:
        mapped_preset = "balanced"

    cmd = [
        sys.executable,
        "lib/assemble.py",
        slug,
        "--width", str(body.width),
        "--height", str(body.height),
        "--preset", mapped_preset,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(WORKSPACE),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "Full export render timed out")

    if result.returncode != 0:
        raise HTTPException(500, f"Full export failed: {result.stderr[-2000:]}")

    # assemble writes {slug}-export.mp4 when non-default params used
    out_path = exports_dir / f"{slug}-export.mp4"
    url = f"/projects/{slug}/exports/{out_path.name}"
    await sse_notify(slug, "render-complete")
    return {
        "ok": True,
        "message": "Full video exported",
        "url": url,
        "path": str(out_path),
        "width": body.width,
        "height": body.height,
        "crf": body.crf,
        "preset": body.preset,
    }


@app.get("/api/project/{slug}/storyboard/{scene_id}/timeline")
async def get_scene_timeline(slug: str, scene_id: int):
    """Return the current asset_timeline for the scene."""
    slug = slug.strip()
    project_dir(slug)  # validate

    sb_path, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)
    return {"ok": True, "scene": scene_id, "asset_timeline": scene.get("asset_timeline")}


@app.put("/api/project/{slug}/storyboard/{scene_id}/timeline")
async def update_scene_timeline(slug: str, scene_id: int, body: dict):
    """Replace the asset_timeline for a scene. Accepts body with "asset_timeline" key."""
    slug = slug.strip()
    project_dir(slug)  # validate

    if not isinstance(body, dict) or "asset_timeline" not in body:
        raise HTTPException(400, 'Body must include "asset_timeline" key')
    sb_path, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)
    scene["asset_timeline"] = body["asset_timeline"]
    save_storyboard(sb_path, sb)
    await sse_notify(slug, f"timeline-updated:{scene_id}")
    return {"ok": True, "scene": scene_id}


# ---------------------------------------------------------------------------
# Per-scene render preview
# ---------------------------------------------------------------------------


class RenderSceneRequest(BaseModel):
    quality: str = "draft"  # "draft" or "high"


class ExportParams(BaseModel):
    width: int = 1920
    height: int = 1080
    crf: int = 23
    preset: str = "medium"


@app.post("/api/project/{slug}/storyboard/{scene_id}/render-scene")
async def render_single_scene(slug: str, scene_id: int, body: RenderSceneRequest):
    """Render a single scene for preview. Caches on sha256 of asset_timeline
    + quality; skips re-render when inputs match."""
    # Validate scene exists
    _, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)

    renders_dir = project_dir(slug) / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = renders_dir / f"scene-{scene_id:02d}.mp4"
    cache_path = renders_dir / f"scene-{scene_id:02d}-cache.json"

    # Read scene["asset_timeline"] for cache key (sha256 of canonical json)
    asset_timeline = scene.get("asset_timeline")
    timeline_json = json.dumps(asset_timeline, sort_keys=True)
    current_sha = hashlib.sha256(timeline_json.encode("utf-8")).hexdigest()

    # Cache check: same sha256 AND same quality
    if mp4_path.exists() and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if (cached.get("sha256") == current_sha
                    and cached.get("quality") == body.quality):
                return {
                    "ok": True,
                    "path": str(mp4_path),
                    "cached": True,
                    "url": f"/projects/{slug}/renders/scene-{scene_id:02d}.mp4",
                }
        except (json.JSONDecodeError, KeyError):
            pass  # Corrupt cache — re-render

    # Build the CLI command (asset_timeline-driven; no legacy motion override)
    cmd = [
        sys.executable,
        "lib/assemble.py",
        slug,
        "--scene", str(scene_id),
        "--quality", body.quality,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(WORKSPACE),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(500, f"Render timed out for scene {scene_id}")

    if result.returncode != 0:
        raise HTTPException(500, f"Render failed for scene {scene_id}: {result.stderr[-2000:]}")

    # Update cache: stores {"sha256": hash, "quality": body.quality}
    cache_path.write_text(json.dumps({
        "sha256": current_sha,
        "quality": body.quality,
    }, indent=2))

    return {
        "ok": True,
        "path": str(mp4_path),
        "cached": False,
        "url": f"/projects/{slug}/renders/scene-{scene_id:02d}.mp4",
    }


@app.post("/api/project/{slug}/storyboard/{scene_id}/render-scene/delete")
async def delete_scene_render(slug: str, scene_id: int):
    """Delete a cached per-scene render and its cache metadata."""
    renders_dir = project_dir(slug) / "renders"
    mp4_path = renders_dir / f"scene-{scene_id:02d}.mp4"
    cache_path = renders_dir / f"scene-{scene_id:02d}-cache.json"

    if mp4_path.exists():
        mp4_path.unlink()
    if cache_path.exists():
        cache_path.unlink()

    return {"ok": True}


# ---------------------------------------------------------------------------
# Prompt templates (editable, per-project)
# ---------------------------------------------------------------------------
# Persisted to projects/<slug>/prompts.json. The agent (or the user, via
# this dashboard) can edit them in place. When generating an image or
# video, the agent prepends/uses these templates to build the actual
# prompt that goes to the model. The agent listens for SSE
# `image-requested:<scene_id>` events and reads this file to build the
# prompt; same shape for video.

DEFAULT_PROMPTS = {
    "image": {
        "beacon_world": (
            "Premium contemplative wellness aesthetic. Costa Rica tropical "
            "setting or warm-lit interior. Substance-free, non-ceremonial, "
            "no new-age imagery, no crystals or sound-bath cliches. Golden "
            "hour outdoor light or a single warm indoor light source "
            "against deep shadow. Materials: tropical hardwood, bamboo, "
            "raw linen, volcanic stone. Color palette: deep near-black "
            "backgrounds, warm gold/amber accent (#c9a03a), muted steel "
            "blue secondary, soft white highlights. No human faces "
            "visible; only backs of heads, silhouettes, or hands in "
            "stillness. Atmosphere of slow attention and care, not "
            "celebration. Do NOT invent or render any specific device, "
            "instrument, speaker, or piece of equipment — only natural "
            "materials (wood, linen, stone, water, plants) and "
            "architectural elements (palapas, benches, pavilions)."
        ),
        "video_style": (
            "Cinematic widescreen composition, 16:9, 1280x720 or higher. "
            "Contemplative stillness with a sense of held breath. Rule-"
            "of-thirds subject placement leaving room for text overlay "
            "in upper third. Three depth planes (foreground/midground/"
            "background) with atmospheric layering — mist, soft focus, "
            "or out-of-focus foreground. Negative space is intentional, "
            "not empty. The image should feel like a single frame from "
            "a slow-moving documentary about a careful practice, not a "
            "stock photo. No on-screen text, no UI elements, no logos, "
            "no watermarks, no invented technical equipment."
        ),
        "scene_template": (
            "[BEACON_WORLD]\n{beacon_world}\n\n[VIDEO_STYLE]\n{video_style}\n\n"
            "[SCENE]\n{visual_direction}"
        ),
    },
    "video": {
        "beacon_world": (
            "Premium contemplative wellness aesthetic, cinematic motion. "
            "Costa Rica tropical setting or warm-lit interior. Substance-"
            "free, non-ceremonial, no new-age imagery. Golden hour or a "
            "single warm light source. Materials: tropical hardwood, "
            "bamboo, raw linen, volcanic stone. Color palette: deep "
            "near-black, warm gold/amber (#c9a03a), muted steel blue, "
            "soft white. No human faces visible. Do NOT invent or "
            "render any specific device, instrument, or piece of "
            "equipment — only natural materials and architecture."
        ),
        "scene_template": (
            "[BEACON_WORLD]\n{beacon_world}\n\n[SCENE]\n{visual_direction}\n\n"
            "[MOTION]\n{motion_hint}"
        ),
        "motion_hints": {
            "drift_left": "Slow lateral camera drift to the left, subtle push-in. World passes by.",
            "drift_right": "Slow lateral camera drift to the right, subtle push-in. World passes by.",
            "zoom_in": "Slow push-in toward the focal subject. Attention gathers.",
            "push_in_left": "Slow push-in with leftward drift. Subjective, intimate, like leaning in.",
            "push_in_right": "Slow push-in with rightward drift. Subjective, intimate, like leaning in.",
            "parallax_lr": "Slow zoom in with lateral drift left. Depth between near and far planes.",
            "parallax_rl": "Slow zoom in with lateral drift right. Depth between near and far planes.",
        },
    },
}


def _prompts_path(slug: str) -> Path:
    return PROJECTS / slug / "prompts.json"


@app.get("/api/project/{slug}/prompts")
async def get_prompts(slug: str):
    """Read prompt templates for this project. If absent, return defaults."""
    slug = slug.strip()
    project_dir(slug)  # validate
    p = _prompts_path(slug)
    if not p.exists():
        return {"prompts": DEFAULT_PROMPTS, "is_default": True}
    return {"prompts": json.loads(p.read_text()), "is_default": False}


@app.put("/api/project/{slug}/prompts")
async def put_prompts(slug: str, body: dict):
    """Persist prompt templates for this project."""
    slug = slug.strip()
    project_dir(slug)  # validate
    if not isinstance(body, dict) or "prompts" not in body:
        raise HTTPException(400, "Body must be {\"prompts\": {...}}")
    _prompts_path(slug).write_text(json.dumps(body["prompts"], indent=2))
    return {"ok": True}


@app.delete("/api/project/{slug}/prompts")
async def delete_prompts(slug: str):
    """Reset prompt templates to defaults (delete the per-project file)."""
    slug = slug.strip()
    project_dir(slug)  # validate
    p = _prompts_path(slug)
    if p.exists():
        p.unlink()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Exports (list videos in projects/<slug>/exports/ for the dashboard)
# ---------------------------------------------------------------------------

@app.get("/api/project/{slug}/exports")
async def list_exports(slug: str):
    """List renderable video files under projects/<slug>/exports/.

    Returns each file's name, size, mtime, and the URL the dashboard
    uses to open it (via the /projects/{slug}/... static mount).
    """
    slug = slug.strip()
    base = project_dir(slug) / "exports"
    base.mkdir(parents=True, exist_ok=True)
    out = []
    # Walk one level deep so we can also surface subdirs like exports/tests/
    for root in [base, *base.iterdir()] if base.exists() else []:
        if root.is_file():
            files = [root]
        elif root.is_dir():
            files = list(root.rglob("*"))
        else:
            continue
        for f in files:
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if ext not in (".mp4", ".webm", ".mov", ".gif"):
                continue
            rel = f.relative_to(PROJECTS).as_posix()
            stat = f.stat()
            out.append({
                "name": f.name,
                "path": rel,
                "url": f"/projects/{rel}",
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "subdir": f.parent.relative_to(base).as_posix() if f.parent != base else "",
            })
    # Sort newest first
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return {"exports": out}


# ---------------------------------------------------------------------------
# Export presets (per-project)
# ---------------------------------------------------------------------------
# GET returns defaults, or custom from projects/{slug}/export-presets.json
# merged in for any missing keys. PUT accepts override presets (partial ok
# at the top level), validates shape/values, saves only the provided custom
# dict, and returns the full merged result.

DEFAULT_EXPORT_PRESETS = {
    "4k_landscape": {"width": 3840, "height": 2160, "crf": 18, "preset": "slow"},
    "1080p_landscape": {"width": 1920, "height": 1080, "crf": 23, "preset": "medium"},
    "720p_landscape": {"width": 1280, "height": 720, "crf": 28, "preset": "fast"},
    "1080p_portrait": {"width": 1080, "height": 1920, "crf": 23, "preset": "medium"},
    "1080p_square": {"width": 1080, "height": 1080, "crf": 23, "preset": "medium"}
}


def _export_presets_path(slug: str) -> Path:
    return PROJECTS / slug / "export-presets.json"


@app.get("/api/project/{slug}/export-presets")
async def get_export_presets(slug: str):
    """Return merged export presets for the project as an array for the Export tab dropdown."""
    slug = slug.strip()
    project_dir(slug)  # validate
    p = _export_presets_path(slug)
    if p.exists():
        try:
            custom = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            custom = {}
    else:
        custom = {}
    merged = dict(DEFAULT_EXPORT_PRESETS)
    if isinstance(custom, dict):
        for k, v in custom.items():
            if isinstance(v, dict) and all(k2 in v for k2 in ("width", "height", "crf", "preset")):
                merged[k] = v
    # Convert to array format for the frontend dropdown
    label_map = {
        "4k_landscape": "4K Landscape (3840×2160)",
        "1080p_landscape": "1080p Landscape (1920×1080)",
        "720p_landscape": "720p Landscape (1280×720)",
        "1080p_portrait": "1080p Portrait (1080×1920)",
        "1080p_square": "1080p Square (1080×1080)",
    }
    presets = []
    for key, cfg in merged.items():
        presets.append({
            "label": label_map.get(key, key),
            "width": cfg["width"],
            "height": cfg["height"],
            "crf": cfg["crf"],
            "preset": cfg["preset"],
        })
    presets.append({"label": "Custom", "custom": True})
    return {"presets": presets}


@app.put("/api/project/{slug}/export-presets")
async def put_export_presets(slug: str, body: dict):
    """Persist custom presets overrides (validated), return full merged presets."""
    slug = slug.strip()
    pd = project_dir(slug)  # validate
    if not isinstance(body, dict):
        raise HTTPException(400, "Body must be a dict of preset overrides")
    allowed = {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"}
    for name, cfg in body.items():
        if not isinstance(cfg, dict):
            raise HTTPException(400, f"Preset '{name}' must be an object")
        for key in ("width", "height", "crf", "preset"):
            if key not in cfg:
                raise HTTPException(400, f"Preset '{name}' missing required key '{key}'")
        w, h, crf, pr = cfg["width"], cfg["height"], cfg["crf"], cfg["preset"]
        if not isinstance(w, int) or w <= 0:
            raise HTTPException(400, f"Preset '{name}' width must be positive int")
        if not isinstance(h, int) or h <= 0:
            raise HTTPException(400, f"Preset '{name}' height must be positive int")
        if not isinstance(crf, int) or not (0 <= crf <= 51):
            raise HTTPException(400, f"Preset '{name}' crf must be int 0-51")
        if pr not in allowed:
            raise HTTPException(400, f"Preset '{name}' preset must be one of {sorted(allowed)}")
    # mkdir so first write succeeds even for brand-new slugs (matches script etc.)
    pd.mkdir(parents=True, exist_ok=True)
    p = _export_presets_path(slug)
    p.write_text(json.dumps(body, indent=2))
    # build + return merged
    merged = dict(DEFAULT_EXPORT_PRESETS)
    for k, v in body.items():
        if isinstance(v, dict):
            merged[k] = v
    return merged


# ---------------------------------------------------------------------------
# Export pipeline endpoints (render-scene-preview + updated /render)
# Added right before static mounts per spec. The /render update is at top.
# ---------------------------------------------------------------------------


class RenderScenePreviewRequest(BaseModel):
    scene_id: int
    width: int = 1920
    height: int = 1080
    crf: int = 23
    preset: str = "medium"


@app.post("/api/project/{slug}/render-scene-preview")
async def render_scene_preview(slug: str, body: RenderScenePreviewRequest):
    """Render single scene preview with explicit size/crf/preset from Export tab.
    Maps preset for assemble.py (fast/quality/balanced), calls with --scene --width --height --preset.
    Returns path/url like the storyboard render-scene endpoint.
    """
    slug = slug.strip()
    project_dir(slug)  # validate

    _, sb = load_storyboard(slug)
    scene = find_scene(sb, body.scene_id)

    renders_dir = project_dir(slug) / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = renders_dir / f"scene-{body.scene_id:02d}.mp4"

    # Map the preset to our assemble.py --preset flag
    p = (body.preset or "medium").lower()
    if p in {"ultrafast", "superfast", "veryfast", "faster", "fast"}:
        mapped_preset = "fast"
    elif p in {"slow", "slower", "veryslow"}:
        mapped_preset = "quality"
    else:
        mapped_preset = "balanced"

    cmd = [
        sys.executable,
        "lib/assemble.py",
        slug,
        "--scene", str(body.scene_id),
        "--width", str(body.width),
        "--height", str(body.height),
        "--preset", mapped_preset,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(WORKSPACE),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(500, f"Render timed out for scene {body.scene_id}")

    if result.returncode != 0:
        raise HTTPException(500, f"Render failed for scene {body.scene_id}: {result.stderr[-2000:]}")

    return {
        "ok": True,
        "path": str(mp4_path),
        "cached": False,
        "url": f"/projects/{slug}/renders/scene-{body.scene_id:02d}.mp4",
    }


# ---------------------------------------------------------------------------
# Static mounts
# ---------------------------------------------------------------------------

class NoCacheStaticFiles(StaticFiles):
    """Serve files with forced revalidation. Starlette still answers 304 via
    ETag/Last-Modified, so unchanged files stay cheap — but a regenerated
    image (same filename, new bytes) is never served stale from cache."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", NoCacheStaticFiles(directory=str(STATIC)), name="static")
app.mount("/projects", NoCacheStaticFiles(directory=str(PROJECTS)), name="projects")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8900)
