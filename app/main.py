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
    POST /api/project/{slug}/storyboard/{scene_id}/unapprove    → unapprove any variant + unlock scene
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
import subprocess
import sys
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
    caption_phrases: list[dict] | None = None


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


class RenderSceneRequest(BaseModel):
    quality: str = "draft"  # "draft" or "high"
    motion_override: str | None = None


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


@app.post("/api/project/{slug}/storyboard/{scene_id}/unapprove")
async def unapprove_scene(slug: str, scene_id: int):
    """Clear any approved variant for the scene and unlock it.

    Returns the path of the previously-approved image (if any) so callers
    can record/restore context if needed.
    """
    sb_path, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)

    versions = scene.get("images", [])
    prev_approved_path = None
    for v in versions:
        if v.get("approved"):
            prev_approved_path = v.get("path")
            break

    for v in versions:
        v["approved"] = False
    scene["locked"] = False

    save_storyboard(sb_path, sb)
    await sse_notify(slug, f"scene-unapproved:{scene_id}")
    return {"ok": True, "scene": scene_id, "prev_approved_path": prev_approved_path}


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
# Per-scene render preview
# ---------------------------------------------------------------------------


@app.post("/api/project/{slug}/storyboard/{scene_id}/render-scene")
async def render_single_scene(slug: str, scene_id: int, body: RenderSceneRequest):
    """Render a single scene for preview. Supports draft/high quality and
    optional motion override. Caches results to avoid re-rendering when
    nothing changed."""
    # Validate scene exists
    _, sb = load_storyboard(slug)
    scene = find_scene(sb, scene_id)

    renders_dir = project_dir(slug) / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = renders_dir / f"scene-{scene_id:02d}.mp4"
    cache_path = renders_dir / f"scene-{scene_id:02d}-cache.json"

    # Cache check
    if mp4_path.exists() and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if (cached.get("quality") == body.quality
                    and cached.get("motion_override") == body.motion_override):
                return {
                    "ok": True,
                    "path": str(mp4_path),
                    "cached": True,
                    "url": f"/projects/{slug}/renders/scene-{scene_id:02d}.mp4",
                }
        except (json.JSONDecodeError, KeyError):
            pass  # Corrupt cache — re-render

    # Build the CLI command
    cmd = [
        sys.executable,
        "lib/assemble.py",
        slug,
        "--scene", str(scene_id),
        "--quality", body.quality,
    ]
    if body.motion_override:
        cmd += ["--motion", body.motion_override]

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

    # Update cache
    cache_path.write_text(json.dumps({
        "quality": body.quality,
        "motion_override": body.motion_override,
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
