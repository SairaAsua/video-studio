#!/usr/bin/env python3
"""
video-studio dashboard — FastAPI server on localhost:8900.

Serves the two-pane editor (script + storyboard) and artifact CRUD endpoints.
Filesystem is the single source of truth — no database.

Endpoints:
    GET  /                         → dashboard HTML
    GET  /api/project/<slug>       → {script, storyboard, design, scene_images}
    PUT  /api/project/<slug>/script    → write script.md
    PUT  /api/project/<slug>/storyboard/<int:scene_id> → update scene
    POST /api/project/<slug>/render     → trigger render (gated on all approved)
    GET  /api/project/<slug>/ws         → SSE for live refresh

Usage:
    python app/main.py
    uvicorn app.main:app --host 127.0.0.1 --port 8900
"""

import json
import asyncio
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

WORKSPACE = Path(__file__).resolve().parent.parent
PROJECTS = WORKSPACE / "projects"
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Video Studio")


# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------

sse_clients: list[asyncio.Queue] = []

async def sse_notify(slug: str, event: str = "refresh"):
    """Push an SSE event to all connected clients."""
    data = json.dumps({"slug": slug, "event": event})
    for q in sse_clients:
        await q.put(data)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/projects")
async def list_projects():
    """List all project slugs."""
    if not PROJECTS.exists():
        return []
    slugs = sorted([
        d.name for d in PROJECTS.iterdir()
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith(".")
    ])
    return slugs


@app.get("/api/project/watch")
async def sse_watch(request: Request):
    """SSE endpoint for all projects — no slug required. MUST be before /api/project/{slug}."""
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

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/project/{slug}")
async def get_project(slug: str):
    """Return all artifacts for a project."""
    proj = PROJECTS / slug
    if not proj.exists():
        raise HTTPException(404, f"Project '{slug}' not found")

    result = {"slug": slug}

    for artifact in ["brief.md", "DESIGN.md", "SPINE.md", "script.md"]:
        path = proj / artifact
        if path.exists():
            result[artifact.replace(".md", "")] = path.read_text()

    sb_path = proj / "storyboard.json"
    if sb_path.exists():
        result["storyboard"] = json.loads(sb_path.read_text())

    # List generated images
    scenes_dir = proj / "scenes"
    if scenes_dir.exists():
        result["scene_images"] = sorted(
            str(p.relative_to(WORKSPACE)) for p in scenes_dir.glob("*.png")
        )

    return result


class ScriptUpdate(BaseModel):
    content: str


@app.put("/api/project/{slug}/script")
async def update_script(slug: str, body: ScriptUpdate):
    """Write script.md."""
    proj = PROJECTS / slug
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "script.md").write_text(body.content)
    await sse_notify(slug, "script-updated")
    return {"ok": True}


class SceneUpdate(BaseModel):
    text: str | None = None
    visual_direction: str | None = None
    locked: bool | None = None
    approved: bool | None = None


@app.put("/api/project/{slug}/storyboard/{scene_id}")
async def update_scene(slug: str, scene_id: int, body: SceneUpdate):
    """Update a single scene in storyboard.json."""
    sb_path = PROJECTS / slug / "storyboard.json"
    if not sb_path.exists():
        raise HTTPException(404, "storyboard.json not found")

    sb = json.loads(sb_path.read_text())
    for scene in sb.get("scenes", []):
        if scene["id"] == scene_id:
            if body.text is not None:
                scene["text"] = body.text
            if body.visual_direction is not None:
                scene["visual_direction"] = body.visual_direction
            if body.locked is not None:
                scene["locked"] = body.locked
            if body.approved is not None:
                scene["approved"] = body.approved
            break
    else:
        raise HTTPException(404, f"Scene {scene_id} not found")

    sb_path.write_text(json.dumps(sb, indent=2))
    await sse_notify(slug, "storyboard-updated")
    return {"ok": True, "scene": scene_id}


@app.post("/api/project/{slug}/render")
async def trigger_render(slug: str):
    """Trigger assembly. Gates on all scenes approved."""
    sb_path = PROJECTS / slug / "storyboard.json"
    if not sb_path.exists():
        raise HTTPException(400, "No storyboard yet")

    sb = json.loads(sb_path.read_text())
    unapproved = [s["id"] for s in sb.get("scenes", []) if not s.get("approved")]
    if unapproved:
        raise HTTPException(400, f"Scenes not approved: {unapproved}")

    # TODO: actually call assemble.py in background
    await sse_notify(slug, "render-queued")
    return {"ok": True, "message": "Render queued"}


@app.post("/api/project/{slug}/storyboard/{scene_id}/image")
async def request_scene_image(slug: str, scene_id: int):
    """Request image generation from agent. Agent generates, saves, and updates storyboard.json."""
    # Validate scene exists
    sb_path = PROJECTS / slug / "storyboard.json"
    if not sb_path.exists():
        raise HTTPException(404, "storyboard.json not found")
    sb = json.loads(sb_path.read_text())
    scene = next((s for s in sb.get("scenes", []) if s["id"] == scene_id), None)
    if not scene:
        raise HTTPException(404, f"Scene {scene_id} not found")
    if scene.get("locked"):
        raise HTTPException(400, f"Scene {scene_id} is locked")

    # Signal agent — agent does the actual generation, file save, and storyboard update
    await sse_notify(slug, f"gen-requested:{scene_id}")
    return {"ok": True, "message": f"Generation requested for scene {scene_id}"}


@app.post("/api/project/{slug}/storyboard/{scene_id}/add-version")
async def add_image_version(slug: str, scene_id: int, body: dict):
    """Agent calls this after saving a generated image to disk."""
    sb_path = PROJECTS / slug / "storyboard.json"
    sb = json.loads(sb_path.read_text())
    scene = next((s for s in sb.get("scenes", []) if s["id"] == scene_id), None)
    if not scene:
        raise HTTPException(404, f"Scene {scene_id} not found")

    versions = scene.setdefault("images", [])
    versions.append({
        "path": body.get("path", ""),
        "seed": body.get("seed", 42),
        "approved": False
    })
    scene["current_image"] = len(versions) - 1
    sb_path.write_text(json.dumps(sb, indent=2))
    await sse_notify(slug, "refresh")
    return {"ok": True, "version": len(versions)}


@app.post("/api/project/{slug}/storyboard/{scene_id}/navigate")
async def navigate_version(slug: str, scene_id: int, body: dict = None):
    """Navigate between image versions for a scene."""
    sb_path = PROJECTS / slug / "storyboard.json"
    sb = json.loads(sb_path.read_text())
    for scene in sb.get("scenes", []):
        if scene["id"] == scene_id:
            delta = body.get("delta", 0) if body else 0
            total = len(scene.get("images", []))
            if total:
                scene["current_image"] = (scene.get("current_image", 0) + delta) % total
            break
    sb_path.write_text(json.dumps(sb, indent=2))
    return {"ok": True, "scene": scene_id, "current": scene.get("current_image", 0)}


@app.post("/api/project/{slug}/storyboard/{scene_id}/approve")
async def approve_current(slug: str, scene_id: int):
    """Approve the currently displayed image version."""
    sb_path = PROJECTS / slug / "storyboard.json"
    sb = json.loads(sb_path.read_text())
    for scene in sb.get("scenes", []):
        if scene["id"] == scene_id:
            versions = scene.get("images", [])
            idx = scene.get("current_image", 0)
            if versions and idx < len(versions):
                # Un-approve all, approve current
                for v in versions:
                    v["approved"] = False
                versions[idx]["approved"] = True
                scene["locked"] = True  # lock on approve
            break
    sb_path.write_text(json.dumps(sb, indent=2))
    await sse_notify(slug, f"scene-approved:{scene_id}")
    return {"ok": True, "scene": scene_id}


@app.get("/api/project/{slug}/ws")
async def sse_stream(slug: str, request: Request):
    """SSE endpoint for live refresh."""
    q: asyncio.Queue = asyncio.Queue()
    sse_clients.append(q)

    async def event_generator():
        try:
            # Send initial event
            yield f"data: {json.dumps({'slug': slug, 'event': 'connected'})}\n\n"
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

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# Static files (CSS, JS)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

# Project files (scenes, renders, exports) — served directly from workspace
from fastapi.staticfiles import StaticFiles as SF
app.mount("/projects", SF(directory=str(PROJECTS)), name="projects")
