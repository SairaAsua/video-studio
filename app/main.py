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

Usage:
    python app/main.py
    uvicorn app.main:app --host 127.0.0.1 --port 8900
"""

import asyncio
import json
import re
import time
from pathlib import Path

import requests
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
# Image generation (FAL via Nous subscription gateway)
# ---------------------------------------------------------------------------

FAL_MODEL = "fal-ai/flux-2/klein/9b"
FAL_GATEWAY = "https://fal-queue-gateway.nousresearch.com"

def _get_nous_token():
    """Read Nous access token using Hermes' internal resolver."""
    try:
        import sys
        sys.path.insert(0, str(Path.home() / ".hermes" / "hermes-agent"))
        from tools.managed_tool_gateway import read_nous_access_token
        token = read_nous_access_token()
        return token
    except Exception:
        return None

def _load_design_style(slug: str) -> tuple[str, int]:
    """Extract style suffix and seed from DESIGN.md."""
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

def _generate_fal_image(prompt: str, nous_token: str) -> str:
    """Generate an image via the FAL gateway. Returns the image URL."""
    headers = {"Authorization": f"Bearer {nous_token}", "Content-Type": "application/json"}
    payload = {"prompt": prompt, "image_size": "landscape_16_9", "num_images": 1}
    
    resp = requests.post(f"{FAL_GATEWAY}/{FAL_MODEL}", headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    status_url = data.get("status_url")
    if not status_url:
        raise RuntimeError("No status_url in FAL response")
    
    # Poll for completion
    for _ in range(30):
        time.sleep(1)
        sr = requests.get(status_url, headers=headers, timeout=10)
        sr.raise_for_status()
        sdata = sr.json()
        if sdata.get("status") == "COMPLETED":
            rr = requests.get(data["response_url"], headers=headers, timeout=10)
            rr.raise_for_status()
            result = rr.json()
            images = result.get("images", [])
            if images:
                return images[0]["url"]
            raise RuntimeError("No images in completed FAL response")
        elif sdata["status"] not in ("IN_QUEUE", "IN_PROGRESS"):
            raise RuntimeError(f"FAL generation failed: {sdata.get('status')}")
    raise RuntimeError("FAL generation timed out")


@app.post("/api/project/{slug}/storyboard/{scene_id}/image")
async def generate_scene_image(slug: str, scene_id: int):
    """Generate an image for this scene via FAL and register it immediately."""
    slug = slug.strip()
    project_dir(slug)  # validate
    
    # Load scene
    sb_path, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)
    if scene.get("locked"):
        raise HTTPException(400, f"Scene {scene_id} is locked")
    
    # Build prompt
    style, seed = _load_design_style(slug)
    prompt = f"{style} {scene.get('visual_direction', '')}".strip()
    
    # Generate
    nous_token = _get_nous_token()
    if not nous_token:
        raise HTTPException(500, "Nous token not available")
    
    try:
        image_url = _generate_fal_image(prompt, nous_token)
    except Exception as e:
        raise HTTPException(500, f"Image generation failed: {e}")
    
    # Download
    scenes_dir = project_dir(slug) / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    versions = scene.setdefault("images", [])
    vnum = len(versions) + 1
    filename = f"scene-{scene_id:02d}-v{vnum}.png"
    filepath = scenes_dir / filename
    
    img_data = requests.get(image_url, timeout=30)
    img_data.raise_for_status()
    filepath.write_bytes(img_data.content)
    
    # Register
    versions.append({"path": f"scenes/{filename}", "seed": seed, "approved": False})
    scene["current_image"] = len(versions) - 1
    save_storyboard(sb_path, sb)
    
    await sse_notify(slug, "refresh")
    return {"ok": True, "scene": scene_id, "version": vnum, "path": f"scenes/{filename}"}


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
