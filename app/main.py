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
    POST /api/project/{slug}/storyboard/{scene_id}/image/edit   → edit existing image via xAI Grok Imagine Image Edit (registers in asset_timeline)
    GET  /api/project/{slug}/storyboard/{scene_id}/timeline     → get asset_timeline
    PUT  /api/project/{slug}/storyboard/{scene_id}/timeline     → set/replace asset_timeline (emits timeline-updated SSE)
    POST /api/project/{slug}/storyboard/{scene_id}/render-scene → render single-scene preview (asset_timeline-driven)
    POST /api/project/{slug}/render-scene-preview → render scene preview with export {scene_id,width,height,crf,preset} (maps preset)
    POST /api/project/{slug}/render         → trigger render (gated on still asset with path in asset_timeline); optional body for full export params
    GET  /api/project/{slug}/export-presets → list presets for Export tab
    POST /api/project/{slug}/render-scene-preview → export current scene with {width,height,crf,preset} + scene_id
    (full /render also accepts ExportParams body to drive assemble with custom settings)
    GET  /api/project/{slug}/media/{file_path:path}             → serve project files (images, videos) as public URLs for external APIs (e.g. xAI image edit)

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

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List

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
    atomic_json(sb_path, sb)


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


# ── Media serving (for xAI image edit API which needs public URLs) ──

@app.get("/api/project/{slug}/media/{file_path:path}")
async def serve_project_media(slug: str, file_path: str):
    """Serve project files (images, videos) as public URLs for external APIs.
    
    Resolves file_path relative to projects/{slug}/. Returns 404 for 
    non-existent files or paths that escape the project directory.
    """
    slug = slug.strip()
    proj = project_dir(slug)  # validates slug
    if not proj.exists():
        raise HTTPException(404, f"Project '{slug}' not found")
    
    # Security: resolve and verify path stays within project dir
    resolved = (proj / file_path).resolve()
    if not resolved.is_relative_to(proj.resolve()):
        raise HTTPException(404, "Path escapes project directory")
    if not resolved.is_file():
        raise HTTPException(404, "File not found")
    
    return FileResponse(resolved)


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


@app.post("/api/projects")
async def create_project(body: dict = None):
    """Create a new project from templates."""
    slug = (body or {}).get("slug", "").strip()
    if not slug or not re.match(r'^[a-z0-9][a-z0-9-]*$', slug):
        raise HTTPException(400, "Invalid slug: use lowercase letters, numbers, and hyphens")
    proj = PROJECTS / slug
    if proj.exists():
        raise HTTPException(409, f"Project '{slug}' already exists")
    proj.mkdir(parents=True)
    for sub in ("scenes", "audio", "renders", "exports", "sources"):
        (proj / sub).mkdir()
    (proj / "storyboard.json").write_text(json.dumps({"project": slug, "scenes": []}, indent=2))
    (proj / "SPINE.md").write_text(f"# SPINE — {slug}\n\n> Project: `{slug}` · Narrative spine.\n")
    return {"ok": True, "slug": slug}


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


class ImageEditRequest(BaseModel):
    source_image_path: str
    prompt: str


@app.post("/api/project/{slug}/storyboard/{scene_id}/image/edit")
async def edit_scene_image(slug: str, scene_id: int, body: ImageEditRequest):
    """
    Edit an existing image using xAI Grok Imagine Image Edit.
    Takes a source image path and a natural language edit prompt.
    Returns the edited variant path and URL.
    """
    slug = slug.strip()
    project_dir(slug)  # validate
    
    sb_path, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)
    
    # Resolve source path relative to project directory
    source_path = Path(normalize_image_path(slug, body.source_image_path))
    source_abs = project_dir(slug) / source_path
    if not source_abs.exists():
        raise HTTPException(404, f"Source image not found: {source_path}")
    
    await sse_notify(slug, f"image-editing:{scene_id}")
    
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            _edit_image_sync,
            str(source_abs),
            body.prompt.strip(),
            str(project_dir(slug) / "scenes"),
            slug,
        )
    except Exception as e:
        await sse_notify(slug, f"image-edit-failed:{scene_id}")
        raise HTTPException(500, f"Edit failed: {e}")
    
    if not result.get("ok"):
        await sse_notify(slug, f"image-edit-failed:{scene_id}")
        raise HTTPException(500, result.get("error", "Unknown edit error"))
    
    # Register edited image in asset_timeline
    img_path = Path(result["path"])
    rel_path = f"scenes/{img_path.name}"
    entry = {
        "type": "still",
        "path": rel_path,
        "layer": 0,
        "caption_phrases": [],
        "prompt": body.prompt.strip(),
        "edited_from": body.source_image_path,
    }
    if not isinstance(scene.get("asset_timeline"), list):
        scene["asset_timeline"] = []
    scene["asset_timeline"].append(entry)
    save_storyboard(sb_path, sb)
    
    await sse_notify(slug, f"still-registered:{scene_id}")
    return {
        "ok": True,
        "scene": scene_id,
        "path": rel_path,
        "url": result.get("url"),
        "revised_prompt": result.get("revised_prompt"),
    }


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
async def request_scene_image(slug: str, scene_id: int, body: dict = None):
    """
    Generate an image for this scene using xAI Grok Imagine.
    Accepts optional prompt in request body; falls back to scene.visual_direction.
    Runs inline — progress streamed via SSE.
    """
    slug = slug.strip()
    project_dir(slug)  # validate

    sb_path, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)
    prompt = (body or {}).get("prompt", "").strip() or scene.get("visual_direction", "")
    await sse_notify(slug, f"image-generating:{scene_id}")

    try:
        # Run generation in thread pool (blocking HTTP call)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            _generate_image_sync,
            prompt,
            str(project_dir(slug) / "scenes"),
        )
    except Exception as e:
        await sse_notify(slug, f"image-failed:{scene_id}")
        raise HTTPException(500, f"Generation failed: {e}")

    if not result["ok"]:
        await sse_notify(slug, f"image-failed:{scene_id}")
        raise HTTPException(500, result.get("error", "Unknown generation error"))

    # Register in asset_timeline
    img_path = Path(result["image_path"])
    rel_path = f"scenes/{img_path.name}"
    entry = {
        "type": "still",
        "path": rel_path,
        "layer": 0,
        "caption_phrases": [],
        "prompt": prompt,
    }
    if not isinstance(scene.get("asset_timeline"), list):
        scene["asset_timeline"] = []
    scene["asset_timeline"].append(entry)
    save_storyboard(sb_path, sb)

    await sse_notify(slug, f"still-registered:{scene_id}")
    return {"ok": True, "scene": scene_id, "path": rel_path, "status": "generated"}


def _generate_image_sync(prompt: str, output_dir: str) -> dict:
    """Synchronous wrapper — called from thread pool."""
    import os as _os, sys as _sys
    _sys.path.insert(0, str(Path(_os.environ.get('HERMES_HOME', _os.path.expanduser('~/.hermes'))) / 'hermes-agent'))
    import importlib.util
    bridge = Path(__file__).resolve().parent.parent / "lib" / "gen_bridge.py"
    spec = importlib.util.spec_from_file_location("gen_bridge", bridge)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ok, result = mod.generate_image(prompt, output_dir)
    return {"ok": ok, "image_path" if ok else "error": result}


def _generate_video_sync(prompt: str, output_dir: str, image_path: str | None, duration: float, aspect: str, resolution: str, slug: str = "") -> dict:
    """Synchronous video wrapper — called from thread pool."""
    import os as _os, sys as _sys
    _sys.path.insert(0, str(Path(_os.environ.get('HERMES_HOME', _os.path.expanduser('~/.hermes'))) / 'hermes-agent'))
    import importlib.util
    bridge = Path(__file__).resolve().parent.parent / "lib" / "gen_bridge.py"
    spec = importlib.util.spec_from_file_location("gen_bridge", bridge)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ok, result = mod.generate_video(prompt, output_dir, image_path, duration, aspect, resolution, slug)
    return {"ok": ok, "video_path" if ok else "error": result}


def _edit_video_sync(source_path: str, prompt: str, output_dir: str) -> dict:
    """Synchronous video edit wrapper — called from thread pool."""
    import os as _os, sys as _sys
    _sys.path.insert(0, str(Path(_os.environ.get('HERMES_HOME', _os.path.expanduser('~/.hermes'))) / 'hermes-agent'))
    import importlib.util
    bridge = Path(__file__).resolve().parent.parent / "lib" / "gen_bridge.py"
    spec = importlib.util.spec_from_file_location("gen_bridge", bridge)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ok, result = mod.edit_video(source_path, prompt, output_dir)
    return {"ok": ok, "video_path" if ok else "error": result}


def _edit_image_sync(source_path: str, prompt: str, output_dir: str, slug: str) -> dict:
    """Synchronous wrapper for edit_image — called from thread pool."""
    import os as _os, sys as _sys
    _sys.path.insert(0, str(Path(_os.environ.get('HERMES_HOME', _os.path.expanduser('~/.hermes'))) / 'hermes-agent'))
    import importlib.util
    bridge = Path(__file__).resolve().parent.parent / "lib" / "gen_bridge.py"
    spec = importlib.util.spec_from_file_location("gen_bridge", bridge)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.edit_image(source_path, prompt, output_dir, slug)
    return result


def _extend_video_sync(source_path: str, prompt: str, output_dir: str, duration_secs: int) -> dict:
    """Synchronous extend wrapper — called from thread pool."""
    import os as _os, sys as _sys
    _sys.path.insert(0, str(Path(_os.environ.get('HERMES_HOME', _os.path.expanduser('~/.hermes'))) / 'hermes-agent'))
    import importlib.util
    bridge = Path(__file__).resolve().parent.parent / "lib" / "gen_bridge.py"
    spec = importlib.util.spec_from_file_location("gen_bridge", bridge)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ok, result = mod.extend_video(source_path, prompt, output_dir, duration_secs)
    return {"ok": ok, "video_path" if ok else "error": result}


class VideoGenRequest(BaseModel):
    prompt: str = ""
    reference_image_paths: list[str] = []
    duration_secs: float = 5.0
    aspect_ratio: str = "16:9"
    resolution: str = "720p"


@app.post("/api/project/{slug}/storyboard/{scene_id}/video")
async def request_scene_video(slug: str, scene_id: int, body: VideoGenRequest):
    """
    Generate a video for this scene using xAI Grok Imagine Video.
    Runs inline — progress streamed via SSE.
    """
    slug = slug.strip()
    project_dir(slug)

    sb_path, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)

    prompt = body.prompt or scene.get("visual_direction", "")
    await sse_notify(slug, f"video-generating:{scene_id}")

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            _generate_video_sync,
            prompt,
            str(project_dir(slug) / "scenes"),
            (project_dir(slug) / body.reference_image_paths[0]).as_posix() if body.reference_image_paths else None,
            body.duration_secs,
            body.aspect_ratio,
            body.resolution,
            slug,
        )
    except Exception as e:
        await sse_notify(slug, f"video-failed:{scene_id}")
        raise HTTPException(500, f"Video generation failed: {e}")

    if not result["ok"]:
        await sse_notify(slug, f"video-failed:{scene_id}")
        raise HTTPException(500, result.get("error", "Unknown error"))

    vid_path = Path(result["video_path"])
    rel_path = f"scenes/{vid_path.name}"
    entry = {
        "type": "video",
        "path": rel_path,
        "layer": 0,
        "duration_secs": body.duration_secs,
        "aspect_ratio": body.aspect_ratio,
        "caption_phrases": [],
        "prompt": prompt,
    }
    if not isinstance(scene.get("asset_timeline"), list):
        scene["asset_timeline"] = []
    scene["asset_timeline"].append(entry)
    save_storyboard(sb_path, sb)

    await sse_notify(slug, f"video-registered:{scene_id}")
    return {"ok": True, "scene": scene_id, "path": rel_path, "status": "generated"}


class VideoEditRequest(BaseModel):
    source_video_path: str
    prompt: str


@app.post("/api/project/{slug}/storyboard/{scene_id}/video/edit")
async def request_scene_video_edit(slug: str, scene_id: int, body: VideoEditRequest):
    """
    Edit an existing video clip for this scene using xAI video edits API.
    Runs inline — progress streamed via SSE.
    """
    slug = slug.strip()
    project_dir(slug)  # validate

    sb_path, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)

    # Resolve source video path (project-relative "scenes/..." -> absolute) for bridge read
    src_rel = (body.source_video_path or "").strip().lstrip("/")
    if not src_rel or ".." in Path(src_rel).parts or Path(src_rel).is_absolute():
        raise HTTPException(400, f"Invalid source_video_path '{body.source_video_path}'")
    src_full = project_dir(slug) / src_rel
    if not src_full.exists() or not src_full.is_file():
        raise HTTPException(400, f"Source video not found: {src_rel}")

    await sse_notify(slug, f"video-editing:{scene_id}")

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            _edit_video_sync,
            str(src_full),
            body.prompt,
            str(project_dir(slug) / "scenes"),
        )
    except Exception as e:
        await sse_notify(slug, f"video-failed:{scene_id}")
        raise HTTPException(500, f"Video edit failed: {e}")

    if not result["ok"]:
        await sse_notify(slug, f"video-failed:{scene_id}")
        raise HTTPException(500, result.get("error", "Unknown error"))

    vid_path = Path(result["video_path"])
    rel_path = f"scenes/{vid_path.name}"
    entry = {
        "type": "video",
        "path": rel_path,
        "layer": 0,
        "duration_secs": 5.0,
        "aspect_ratio": "16:9",
        "caption_phrases": [],
        "prompt": body.prompt,
    }
    if not isinstance(scene.get("asset_timeline"), list):
        scene["asset_timeline"] = []
    scene["asset_timeline"].append(entry)
    save_storyboard(sb_path, sb)

    await sse_notify(slug, f"video-registered:{scene_id}")
    return {"ok": True, "scene": scene_id, "path": rel_path}


class VideoExtendRequest(BaseModel):
    source_video_path: str
    prompt: str
    duration_secs: int = 4


@app.post("/api/project/{slug}/storyboard/{scene_id}/video/extend")
async def request_scene_video_extend(slug: str, scene_id: int, body: VideoExtendRequest):
    """
    Extend an existing video clip for this scene using xAI video extensions API.
    Runs inline — progress streamed via SSE (video-extending, video-registered or video-failed).
    """
    slug = slug.strip()
    project_dir(slug)  # validate

    sb_path, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)

    # Resolve and validate source video path (relative to project dir, must exist)
    src_rel = (body.source_video_path or "").strip().lstrip("/")
    if not src_rel or ".." in Path(src_rel).parts or Path(src_rel).is_absolute():
        raise HTTPException(400, f"Invalid source_video_path '{body.source_video_path}'")
    src_full = project_dir(slug) / src_rel
    if not src_full.exists() or not src_full.is_file():
        raise HTTPException(400, f"Source video not found: {src_rel}")

    await sse_notify(slug, f"video-extending:{scene_id}")

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            _extend_video_sync,
            str(src_full),
            body.prompt,
            str(project_dir(slug) / "scenes"),
            body.duration_secs,
        )
    except Exception as e:
        await sse_notify(slug, f"video-failed:{scene_id}")
        raise HTTPException(500, f"Video extend failed: {e}")

    if not result["ok"]:
        await sse_notify(slug, f"video-failed:{scene_id}")
        raise HTTPException(500, result.get("error", "Unknown extend error"))

    vid_path = Path(result["video_path"])
    rel_path = f"scenes/{vid_path.name}"
    entry = {
        "type": "video",
        "path": rel_path,
        "layer": 0,
        "duration_secs": body.duration_secs,
        "aspect_ratio": "16:9",
        "caption_phrases": [],
        "prompt": body.prompt,
    }
    if not isinstance(scene.get("asset_timeline"), list):
        scene["asset_timeline"] = []
    scene["asset_timeline"].append(entry)
    save_storyboard(sb_path, sb)

    await sse_notify(slug, f"video-registered:{scene_id}")
    return {"ok": True, "scene": scene_id, "path": rel_path, "status": "extended"}


class ExportParams(BaseModel):
    width: int = 1920
    height: int = 1080
    crf: int = 23
    preset: str = "medium"


@app.post("/api/project/{slug}/render")
async def trigger_render(slug: str, body: Optional[ExportParams] = None):
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


@app.post("/api/project/{slug}/storyboard/{scene_id}/add-version")
async def add_scene_version(slug: str, scene_id: int, file: UploadFile = File(...), type: str = Form(...), prompt: str = Form("")):
    """
    Accept uploaded video file and append a 'video' entry to the scene's asset_timeline.
    Called by the agent after successful video gen, or by user manual upload in VIDEO CLIPS tab.
    """
    slug = slug.strip()
    project_dir(slug)  # validate

    if type not in ("still", "video"):
        raise HTTPException(400, "type form field must be 'still' or 'video'")

    if not file or not file.filename:
        raise HTTPException(400, "file is required")

    ext = Path(file.filename).suffix.lower()
    if type == "still" and ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(400, "Invalid image extension; only .jpg, .png, .webp allowed")
    if type == "video" and ext not in {".mp4", ".webm", ".mov"}:
        raise HTTPException(400, "Invalid video extension; only .mp4, .webm, .mov allowed")

    sb_path, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)

    scenes_dir = project_dir(slug) / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)

    # keep original filename; make unique if collision
    target_name = Path(file.filename).name
    target = scenes_dir / target_name
    if target.exists():
        stem = Path(target_name).stem
        suffix = ext
        i = 1
        while target.exists():
            target = scenes_dir / f"{stem}-v{i}{suffix}"
            i += 1
        target_name = target.name

    content = await file.read()
    target.write_bytes(content)

    rel_path = f"scenes/{target_name}"
    entry = {
        "type": type,
        "path": rel_path,
        "duration_secs": 5.0 if type == "video" else None,
        "layer": 0,
        "caption_phrases": [],
    }
    if type == "video":
        entry["aspect_ratio"] = "16:9"
    if prompt:
        entry["prompt"] = prompt
    if scene.get("shot_id"):
        scene.setdefault("takes", []).append(dict(entry))
    else:
        if not isinstance(scene.get("asset_timeline"), list):
            scene["asset_timeline"] = []
        scene["asset_timeline"].append(entry)
    save_storyboard(sb_path, sb)
    await sse_notify(slug, f"{type}-registered:{scene_id}")
    return {"ok": True, "path": rel_path}


# ---------------------------------------------------------------------------
# Sources (Resources tab) + script generation (v4.0)
# ---------------------------------------------------------------------------

def _generate_script_sync(sources_text: str, instructions: str) -> dict:
    """Synchronous wrapper for generate_script — called from thread pool."""
    import os as _os, sys as _sys
    _sys.path.insert(0, str(Path(_os.environ.get('HERMES_HOME', _os.path.expanduser('~/.hermes'))) / 'hermes-agent'))
    import importlib.util
    bridge = Path(__file__).resolve().parent.parent / "lib" / "gen_bridge.py"
    spec = importlib.util.spec_from_file_location("gen_bridge", bridge)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ok, result = mod.generate_script(sources_text, instructions)
    return {"ok": ok, "script" if ok else "error": result}


@app.post("/api/project/{slug}/sources")
async def upload_source(slug: str, file: UploadFile = File(...)):
    """Upload a file to projects/{slug}/sources/. Handle collisions by appending -N before ext."""
    slug = slug.strip()
    proj = project_dir(slug)
    sources_dir = proj / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    if not file or not file.filename:
        raise HTTPException(400, "file is required")

    orig_name = Path(file.filename).name
    target = sources_dir / orig_name
    saved_name = orig_name
    if target.exists():
        stem = Path(orig_name).stem
        suffix = Path(orig_name).suffix
        i = 1
        while target.exists():
            saved_name = f"{stem}-{i}{suffix}"
            target = sources_dir / saved_name
            i += 1

    content = await file.read()
    target.write_bytes(content)
    file_size = len(content)
    return {"ok": True, "filename": saved_name, "size": file_size}


@app.get("/api/project/{slug}/sources")
async def list_sources(slug: str):
    """List files in sources/ dir, newest first by mtime."""
    slug = slug.strip()
    base = project_dir(slug) / "sources"
    base.mkdir(parents=True, exist_ok=True)
    items = []
    for f in base.iterdir():
        if f.is_file():
            stat = f.stat()
            ext = f.suffix.lower()
            items.append({
                "filename": f.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "ext": ext,
            })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"sources": items}


@app.get("/api/project/{slug}/sources/{filename}")
async def read_source(slug: str, filename: str):
    """Read source file content. Text for .md/.txt, fitz+text or raw for .pdf, url for images, raw for others."""
    slug = slug.strip()
    proj = project_dir(slug)
    safe_name = Path(filename).name
    if ".." in safe_name or "/" in safe_name or "\\" in safe_name:
        raise HTTPException(400, "Invalid filename")
    path = proj / "sources" / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Source file not found")

    ext = path.suffix.lower()
    if ext in (".txt", ".md"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = path.read_text(encoding="latin-1", errors="replace")
        return {"ok": True, "filename": safe_name, "content": text}

    if ext == ".pdf":
        try:
            import fitz
            doc = fitz.open(str(path))
            text = "".join(page.get_text() for page in doc)
            doc.close()
            return {"ok": True, "filename": safe_name, "content": text}
        except Exception:
            # fallback
            raw = path.read_bytes()
            text = raw.decode("latin-1", errors="replace")
            return {"ok": True, "filename": safe_name, "content": text, "pdf_raw": True}

    if ext in (".jpg", ".jpeg", ".png", ".webp"):
        url = f"/projects/{slug}/sources/{safe_name}"
        return {"ok": True, "filename": safe_name, "type": "image", "url": url}

    # other types: try text, else latin1
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        raw = path.read_bytes()
        text = raw.decode("latin-1", errors="replace")
    return {"ok": True, "filename": safe_name, "content": text}


@app.delete("/api/project/{slug}/sources/{filename}")
async def delete_source(slug: str, filename: str):
    """Delete a source file. Reject path escape."""
    slug = slug.strip()
    proj = project_dir(slug)
    safe_name = Path(filename).name
    if ".." in safe_name or "/" in safe_name or "\\" in safe_name:
        raise HTTPException(400, "Invalid filename")
    path = proj / "sources" / safe_name
    if not path.exists():
        raise HTTPException(404, "Source file not found")
    path.unlink()
    return {"ok": True}


@app.post("/api/project/{slug}/generate-script")
async def generate_script_endpoint(slug: str, body: dict = None):
    """Generate narration script via xAI chat from selected sources + instructions.
    Concat sources, run generate_script via thread pool, notify SSE.
    """
    slug = slug.strip()
    project_dir(slug)  # validate + ensure exists

    sources_list = (body or {}).get("sources", []) or []
    instructions = (body or {}).get("instructions", "") or ""

    if not isinstance(sources_list, list) or len(sources_list) == 0:
        raise HTTPException(400, "sources must be a non-empty list of filenames")

    base = project_dir(slug) / "sources"
    parts = []
    for fn in sources_list:
        safe = Path(str(fn)).name
        if ".." in safe or "/" in safe or "\\" in safe:
            raise HTTPException(400, f"Invalid source filename: {fn}")
        p = base / safe
        if not p.exists():
            raise HTTPException(404, f"Source not found: {safe}")
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            content = p.read_bytes().decode("latin-1", errors="replace")
        parts.append(f"=== {safe} ===\n{content}\n\n")
    sources_text = "".join(parts)

    await sse_notify(slug, "script-generating")

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _generate_script_sync, sources_text, instructions)
    except Exception as e:
        await sse_notify(slug, "script-failed")
        raise HTTPException(500, f"Script generation failed: {e}")

    if not result.get("ok"):
        await sse_notify(slug, "script-failed")
        raise HTTPException(500, result.get("error", "Unknown script error"))

    script_text = result.get("script", "")
    await sse_notify(slug, "script-generated")
    return {"ok": True, "script": script_text}


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


from app.workbench import install_workbench, atomic_json

install_workbench(app, project_dir, sse_notify)

app.mount("/static", NoCacheStaticFiles(directory=str(STATIC)), name="static")
app.mount("/projects", NoCacheStaticFiles(directory=str(PROJECTS)), name="projects")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8900)
