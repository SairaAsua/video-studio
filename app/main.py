#!/usr/bin/env python3
"""
video-studio dashboard — FastAPI server on localhost:8900.

Serves the two-pane editor (script + storyboard) and artifact CRUD endpoints.
Filesystem is the single source of truth — no database.

Endpoints:
    GET  /                                  → dashboard HTML
    GET  /api/projects                      → list project slugs
    GET  /api/project/watch                 → SSE stream (all projects)
    GET  /api/project/{slug}                → {script, storyboard, scene_images, ...}
    PUT  /api/project/{slug}/script         → write script.md
    PUT  /api/project/{slug}/storyboard/{scene_id}              → update scene fields
    POST /api/project/{slug}/storyboard/{scene_id}/image        → request generation (agent listens via SSE)
    POST /api/project/{slug}/storyboard/{scene_id}/add-version  → agent registers a saved image
    POST /api/project/{slug}/storyboard/{scene_id}/navigate     → step through image versions
    POST /api/project/{slug}/storyboard/{scene_id}/approve      → approve current version + lock scene
    POST /api/project/{slug}/render         → trigger render (gated on all approved)

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
import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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

    # Generated images, project-relative — same convention as storyboard.json
    scenes_dir = proj / "scenes"
    if scenes_dir.exists():
        result["scene_images"] = sorted(
            str(p.relative_to(proj)) for p in scenes_dir.glob("*.png")
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
    locked: bool | None = None
    approved: bool | None = None


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
# xAI Grok Imagine, saves the resulting image, and registers it via
# `/add-version`. FAL is not used — see MEMORY.md for rationale (2026-06-13).
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
    saves the image to scenes/, and registers it via /add-version.
    """
    slug = slug.strip()
    project_dir(slug)  # validate

    sb_path, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)
    if scene.get("locked"):
        raise HTTPException(400, f"Scene {scene_id} is locked")

    await sse_notify(slug, f"image-requested:{scene_id}")
    return {"ok": True, "scene": scene_id, "status": "requested", "provider": "xai-grok-imagine"}


class VersionAdd(BaseModel):
    path: str
    seed: int = 42


@app.post("/api/project/{slug}/storyboard/{scene_id}/add-version")
async def add_image_version(slug: str, scene_id: int, body: VersionAdd):
    """Agent calls this after saving a generated image to disk."""
    sb_path, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)

    versions = scene.setdefault("images", [])
    versions.append({
        "path": normalize_image_path(slug, body.path),
        "seed": body.seed,
        "approved": False,
    })
    scene["current_image"] = len(versions) - 1
    save_storyboard(sb_path, sb)
    await sse_notify(slug, f"image-added:{scene_id}")
    return {"ok": True, "version": len(versions)}


class Navigate(BaseModel):
    delta: int = 0


@app.post("/api/project/{slug}/storyboard/{scene_id}/navigate")
async def navigate_version(slug: str, scene_id: int, body: Navigate):
    """Step between image versions for a scene."""
    sb_path, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)

    total = len(scene.get("images", []))
    if total:
        scene["current_image"] = (scene.get("current_image", 0) + body.delta) % total
    save_storyboard(sb_path, sb)
    await sse_notify(slug, "storyboard-updated")
    return {"ok": True, "scene": scene_id, "current": scene.get("current_image", 0)}


@app.post("/api/project/{slug}/storyboard/{scene_id}/approve")
async def approve_current(slug: str, scene_id: int):
    """Approve the currently displayed image version and lock the scene."""
    sb_path, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)

    versions = scene.get("images", [])
    idx = scene.get("current_image", 0)
    if not versions or idx >= len(versions):
        raise HTTPException(400, f"Scene {scene_id} has no image to approve")

    for v in versions:
        v["approved"] = False
    versions[idx]["approved"] = True
    scene["locked"] = True

    save_storyboard(sb_path, sb)
    await sse_notify(slug, f"scene-approved:{scene_id}")
    return {"ok": True, "scene": scene_id}


@app.post("/api/project/{slug}/render")
async def trigger_render(slug: str):
    """Trigger assembly. Gates on all scenes having an approved image."""
    _, sb = load_storyboard(slug)
    unapproved = [
        s["id"] for s in sb.get("scenes", [])
        if not any(v.get("approved") for v in s.get("images", []))
    ]
    if unapproved:
        raise HTTPException(400, f"Scenes not approved: {unapproved}")

    # TODO: actually call assemble.py in background
    await sse_notify(slug, "render-queued")
    return {"ok": True, "message": "Render queued"}


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
