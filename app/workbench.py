"""Scene workbench over the existing storyboard; no separate project database."""
from __future__ import annotations

import copy
import asyncio
import hashlib
import json
import socket
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

LOCK = threading.RLock()


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def revision(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_file(project: Path, relative: str) -> Path:
    candidate = (project / relative).resolve()
    if Path(relative).is_absolute() or not candidate.is_relative_to(project.resolve()):
        raise HTTPException(400, "File must remain inside this project")
    if not candidate.is_file():
        raise HTTPException(404, "Project file not found")
    return candidate


def probe(path: Path) -> dict:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height", "-of", "json", str(path)
    ], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise HTTPException(422, "Cannot inspect this media file")
    data = json.loads(result.stdout)
    data["duration_secs"] = float(data.get("format", {}).get("duration", 0))
    return data


def music_inventory(project: Path) -> list[dict]:
    """Inspect explicitly supplied root files; never traverse unrelated sources."""
    files = []
    for path in sorted(project.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {"", ".mp4", ".webm", ".mp3", ".wav", ".m4a", ".flac", ".ogg"}:
            continue
        try:
            data = probe(local_file(project, path.name))
        except HTTPException:
            continue
        if any(s.get("codec_type") == "audio" for s in data.get("streams", [])):
            files.append({"path": path.name, "duration_secs": data["duration_secs"],
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "streams": data.get("streams", [])})
    return files


def import_cuarteto(project: Path, source: Path, source_revision: str) -> dict:
    """Import the received kit once; imported hints are not measured edit points."""
    path = project / "storyboard.json"
    with LOCK:
        if path.exists():
            raise ValueError("An existing storyboard must be preserved; import refused")
        kit = json.loads((source / "production/03_Proyecto.json").read_text())
        continuity = {}
        for kind in ("characters", "locations"):
            continuity[kind] = {
                key: {"name": value["name"], "versions": [dict(
                    copy.deepcopy(value), id="v001", status="draft", image_paths=[],
                    notes="Imported script specification; visual reference not generated."
                )]}
                for key, value in kit[kind].items()
            }
        scenes = []
        for number, shot in enumerate(kit["shots"], 1):
            scene = copy.deepcopy(shot)
            scene.update(id=number, shot_id=shot["id"], text=shot["action"],
                         visual_direction=shot["still_prompt_en"], asset_timeline=[], takes=[],
                         edit_duration_secs=None, selected_asset=None, source_trim_in=0,
                         dialogue=[], review_status="planned", production_notes="", audio_mode="music",
                         reference_versions={**{k: "v001" for k in shot["characters"]},
                                             shot["location"]: "v001"})
            scenes.append(scene)
        board = {"project": project.name, "title": kit.get("title") or project.name,
                 "workbench_schema": "video-studio.workbench/v1", "host_body": socket.gethostname(),
                 "film_titles": copy.deepcopy(kit.get("film_titles", {})),
                 "source_revision": source_revision, "source_directory": str(source.relative_to(project)),
                 "audio_mode": "music", "scenes": scenes, "continuity": continuity,
                 "music_sources": music_inventory(project), "soundtracks": {"A": [], "B": []},
                 "edit_blocks": {"A": kit["edit_A"], "B": kit["edit_B"]},
                 "age_ledger": kit["age_ledger"],
                 "montage_direction": (source / "DIRECCION_DE_MONTAJE.txt").read_text(),
                 "transition": {"type": "none", "duration_secs": 0}}
        atomic_json(path, board)
        for directory in ("scenes", "audio", "renders", "exports", "references", ".history"):
            (project / directory).mkdir(exist_ok=True)
        return board


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Dialogue(StrictModel):
    speaker: str = ""
    text: str = Field(default="", max_length=10000)
    time_in: float = Field(ge=0)
    time_out: float = Field(gt=0)

    @model_validator(mode="after")
    def valid_window(self):
        if self.time_out <= self.time_in:
            raise ValueError("Dialogue end must follow its start")
        return self


class ShotEdit(StrictModel):
    revision: str
    text: str = Field(max_length=30000)
    framing: str = Field(default="", max_length=5000)
    visual_direction: str = Field(default="", max_length=30000)
    video_prompt_en: str = Field(default="", max_length=30000)
    edit_duration_secs: float | None = Field(default=None, gt=0, le=600)
    source_trim_in: float = Field(default=0, ge=0)
    selected_asset: str | None = None
    review_status: Literal["planned", "working", "review", "approved"] = "planned"
    production_notes: str = Field(default="", max_length=30000)
    reference_versions: dict[str, str] = Field(default_factory=dict)
    dialogue: list[Dialogue] = Field(default_factory=list, max_length=100)
    included: bool = True


class OrderEdit(StrictModel):
    revision: str
    scene_ids: list[int]


class ReferenceVersion(StrictModel):
    revision: str
    appearance: str = Field(default="", max_length=15000)
    outfit: str = Field(default="", max_length=10000)
    en: str = Field(default="", max_length=15000)
    notes: str = Field(default="", max_length=15000)
    image_paths: list[str] = Field(default_factory=list, max_length=20)
    status: Literal["draft", "accepted"] = "draft"


class SoundSegment(StrictModel):
    path: str
    source_in: float = Field(ge=0)
    source_out: float = Field(gt=0)
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def valid_window(self):
        if self.source_out <= self.source_in:
            raise ValueError("Audio end must follow its start")
        return self


class SoundtrackEdit(StrictModel):
    revision: str
    segments: list[SoundSegment] = Field(max_length=200)


class VideoRequest(StrictModel):
    request_id: uuid.UUID
    body: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    purpose: str = Field(min_length=1, max_length=10000)


class RevisionEdit(StrictModel):
    revision: str


class FilmExport(StrictModel):
    revision: str
    width: int = Field(default=1280, ge=128, le=3840, multiple_of=2)
    height: int = Field(default=720, ge=128, le=2160, multiple_of=2)
    crf: int = Field(default=23, ge=0, le=51)
    preset: Literal["ultrafast", "veryfast", "medium", "slow"] = "veryfast"


class GenerationReference(StrictModel):
    kind: Literal['characters', 'locations']
    key: str
    version: str


class GenerationTarget(StrictModel):
    type: Literal['reference', 'shot']
    kind: Literal['characters', 'locations'] | None = None
    key: str | None = None
    version: str | None = None
    scene_id: int | None = None


class GenerationPlan(StrictModel):
    revision: str
    target: GenerationTarget
    media_type: Literal['image', 'video']
    model: Literal['grok-imagine-image-2.0', 'grok-imagine-video', 'grok-imagine-video-1.5']
    prompt: str = Field(min_length=1, max_length=40000)
    duration: int = Field(default=3, ge=1, le=15)
    aspect_ratio: Literal['16:9', '1:1', '3:4', '9:16'] = '16:9'
    input_references: list[GenerationReference] | None = Field(default=None, max_length=5)
    variant: str = Field(default='main', min_length=1, max_length=100)


class GenerationApproval(StrictModel):
    plan_sha256: str
    max_cost_usd: str = Field(max_length=30)
    statement: str = Field(min_length=10, max_length=2000)


def install_workbench(app, project_dir, notify):
    export_tasks = {}
    generation_tasks = {}
    def load(slug):
        project = project_dir(slug)
        path = project / "storyboard.json"
        if not path.is_file():
            raise HTTPException(404, "Import a storyboard first")
        return project, path, json.loads(path.read_text())

    def check(path, expected):
        if revision(path) != expected:
            raise HTTPException(409, "Project changed in another editor; reload before saving")

    def commit(project, path, board):
        previous = revision(path)
        history = project / ".history" / f"storyboard-{previous}.json"
        if not history.exists():
            history.parent.mkdir(exist_ok=True)
            history.write_bytes(path.read_bytes())
        atomic_json(path, board)
        return {"ok": True, "revision": revision(path)}

    @app.get("/studio")
    async def studio():
        return FileResponse(Path(__file__).parent / "static/workbench.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/project/{slug}/studio")
    async def snapshot(slug: str):
        project, path, board = load(slug)
        return {"revision": revision(path), "storyboard": board}

    @app.put("/api/project/{slug}/studio/order")
    async def reorder(slug: str, body: OrderEdit):
        with LOCK:
            project, path, board = load(slug)
            check(path, body.revision)
            current = {s["id"]: s for s in board.get("scenes", [])}
            if len(body.scene_ids) != len(current) or set(body.scene_ids) != set(current):
                raise HTTPException(400, "Order must contain each existing scene exactly once")
            ordered = [current[i] for i in body.scene_ids]
            # Keep both films independent: crossing A/B changes the narrative assignment.
            if [s.get("video") for s in ordered] != sorted(s.get("video", "") for s in ordered):
                raise HTTPException(400, "Reorder shots inside their film")
            board["scenes"] = ordered
            result = commit(project, path, board)
        await notify(slug, "storyboard-updated")
        return result

    @app.put("/api/project/{slug}/studio/shot/{scene_id}")
    async def edit_shot(slug: str, scene_id: int, body: ShotEdit):
        with LOCK:
            project, path, board = load(slug)
            check(path, body.revision)
            scene = next((s for s in board["scenes"] if s["id"] == scene_id), None)
            if scene is None:
                raise HTTPException(404, "Shot not found")
            library = {**board.get("continuity", {}).get("characters", {}),
                       **board.get("continuity", {}).get("locations", {})}
            required = set(scene.get("characters", [])) | ({scene["location"]} if scene.get("location") else set())
            if required - body.reference_versions.keys():
                raise HTTPException(400, "Keep a reference version for each character and location")
            for key, version_id in body.reference_versions.items():
                item = library.get(key)
                if not item or not any(v["id"] == version_id for v in item["versions"]):
                    raise HTTPException(400, "Unknown reference version")
            if body.edit_duration_secs is not None:
                if any(d.time_out > body.edit_duration_secs for d in body.dialogue):
                    raise HTTPException(400, "Dialogue exceeds shot duration")
            if body.selected_asset:
                media = local_file(project, body.selected_asset)
                if media.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov"}:
                    raise HTTPException(400, "Select an image or video")
                if media.suffix.lower() in {".mp4", ".webm", ".mov"}:
                    data = probe(media)
                    if not any(s.get("codec_type") == "video" for s in data.get("streams", [])):
                        raise HTTPException(400, "Selected source has no video stream")
                    if body.edit_duration_secs and body.source_trim_in + body.edit_duration_secs > data["duration_secs"] + .05:
                        raise HTTPException(400, "Selection exceeds source video; choose a shorter cut")
            takes = scene.setdefault("takes", [])
            known = {a["path"] for a in takes}
            for asset in scene.get("asset_timeline", []):
                if asset.get("path") and asset["path"] not in known:
                    takes.append(copy.deepcopy(asset))
                    known.add(asset["path"])
            if body.selected_asset and body.selected_asset not in known:
                takes.append({"path": body.selected_asset})
            scene.update(body.model_dump(exclude={"revision"}))
            # Reflect the selected take into the existing compositor's asset timeline.
            if body.selected_asset and body.edit_duration_secs:
                kind = "video" if Path(body.selected_asset).suffix.lower() in {".mp4", ".webm", ".mov"} else "still"
                scene["asset_timeline"] = [{"type": kind, "path": body.selected_asset,
                    "duration_secs": body.edit_duration_secs, "source_in": body.source_trim_in,
                    "time_in": 0, "layer": 0, "caption_phrases": []}]
            else:
                # Explicit clearing removes the previous selected take from this workbench shot.
                scene["asset_timeline"] = []
            result = commit(project, path, board)
        await notify(slug, "storyboard-updated")
        return result

    @app.post("/api/project/{slug}/studio/references/{kind}/{key}/versions")
    async def new_reference_version(slug: str, kind: str, key: str, body: ReferenceVersion):
        if kind not in {"characters", "locations"}:
            raise HTTPException(400, "Unknown reference kind")
        with LOCK:
            project, path, board = load(slug)
            check(path, body.revision)
            item = board.get("continuity", {}).get(kind, {}).get(key)
            if item is None:
                raise HTTPException(404, "Reference not found")
            for ref in body.image_paths:
                if local_file(project, ref).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    raise HTTPException(400, "References must be images")
            if body.status == "accepted" and not body.image_paths:
                raise HTTPException(400, "Accepting a visual reference requires an actual image")
            entry = body.model_dump(exclude={"revision"})
            entry["id"] = f"v{len(item['versions']) + 1:03d}"
            item["versions"].append(entry)
            result = commit(project, path, board)
            result["version_id"] = entry["id"]
            result["shots_using_previous_versions"] = [s.get("shot_id", str(s["id"])) for s in board["scenes"] if key in s.get("reference_versions", {})]
        await notify(slug, "storyboard-updated")
        return result

    @app.post("/api/project/{slug}/studio/reference-upload")
    async def upload_reference(slug: str, file: UploadFile = File(...)):
        project, _, _ = load(slug)
        data = await file.read(20 * 1024 * 1024 + 1)
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(413, "Reference image exceeds 20 MB")
        # Decode the actual image, rather than trusting an upload filename or MIME type.
        try:
            import fitz
            pix = fitz.Pixmap(data)
            if pix.width < 1 or pix.height < 1:
                raise ValueError("Empty image")
            png = pix.tobytes("png")
        except Exception:
            raise HTTPException(400, "Upload a valid raster image")
        relative = "references/" + hashlib.sha256(png).hexdigest()[:24] + ".png"
        path = project / relative
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            path.write_bytes(png)
        return {"ok": True, "path": relative}

    @app.put("/api/project/{slug}/studio/soundtrack/{film}")
    async def soundtrack(slug: str, film: str, body: SoundtrackEdit):
        if film not in {"A", "B"}:
            raise HTTPException(400, "Unknown film")
        with LOCK:
            project, path, board = load(slug)
            check(path, body.revision)
            for segment in body.segments:
                data = probe(local_file(project, segment.path))
                if not any(s.get("codec_type") == "audio" for s in data.get("streams", [])):
                    raise HTTPException(400, "Source has no audio")
                if segment.source_out > data["duration_secs"] + .05:
                    raise HTTPException(400, "Audio edit exceeds source duration")
            board.setdefault("soundtracks", {})[film] = [s.model_dump() for s in body.segments]
            result = commit(project, path, board)
        await notify(slug, "storyboard-updated")
        return result

    @app.post("/api/project/{slug}/studio/soundtrack/{film}/preview")
    async def preview_soundtrack(slug: str, film: str, body: dict):
        if film not in {"A", "B"}:
            raise HTTPException(400, "Unknown film")
        with LOCK:
            project, path, board = load(slug)
            check(path, body.get("revision", ""))
            segments = copy.deepcopy(board.get("soundtracks", {}).get(film, []))
        if not segments:
            raise HTTPException(400, "Save measured audio cuts first")
        from lib.music import render_soundtrack
        try:
            output = await asyncio.to_thread(render_soundtrack, project, film, segments)
        except (RuntimeError, ValueError):
            raise HTTPException(422, "Cannot assemble soundtrack from the saved sources and cuts")
        return {"ok": True, "path": str(output.relative_to(project)), "source_revision": body["revision"]}

    @app.post("/api/project/{slug}/studio/requests")
    async def prepare_request(slug: str, body: VideoRequest):
        """Local intake only: transport/authentication is the receiving adapter's job."""
        import os
        import socket
        from datetime import datetime, timezone
        with LOCK:
            project, _, board = load(slug)
            selected_body = body.body
            local_body = os.environ.get("VIDEO_STUDIO_BODY", socket.gethostname())
            request_path = project / ".requests" / f"{body.request_id}.json"
            existing = json.loads(request_path.read_text()) if request_path.exists() else None
            if existing:
                if existing["purpose"] != body.purpose:
                    raise HTTPException(409, "Request ID already belongs to a different purpose")
                if existing.get("body") and existing["body"] != selected_body:
                    raise HTTPException(409, "Request ID already has a selected body")
                if existing.get("body") == selected_body:
                    return {**existing, "reused": True}
            status = "needs_body" if not selected_body else ("prepared" if selected_body == local_body else "needs_routing")
            record = {"request_id": str(body.request_id), "project": slug, "purpose": body.purpose,
                      "body": selected_body, "status": status,
                      "created_at": existing["created_at"] if existing else datetime.now(timezone.utc).isoformat(),
                      "updated_at": datetime.now(timezone.utc).isoformat(),
                      "available_local_bodies": [local_body],
                      "question": "¿En qué cuerpo querés abrir la app de video?" if status == "needs_body" else None,
                      "editor_path": f"/studio?project={slug}" if status == "prepared" else None,
                      "generation_submitted": False, "transport": "local_http",
                      "matrix_delivery_verified": False}
            atomic_json(request_path, record)
            return {**record, "reused": False}

    @app.post("/api/project/{slug}/studio/shot/{scene_id}/coverage")
    async def new_coverage(slug: str, scene_id: int, body: RevisionEdit):
        with LOCK:
            project, path, board = load(slug)
            check(path, body.revision)
            index = next((i for i, s in enumerate(board["scenes"]) if s["id"] == scene_id), None)
            if index is None:
                raise HTTPException(404, "Shot not found")
            original = board["scenes"][index]
            parent = original.get("parent_shot_id") or original.get("shot_id", str(scene_id))
            existing = {s.get("shot_id") for s in board["scenes"]}
            count = 1
            while f"{parent}.C{count}" in existing:
                count += 1
            coverage = copy.deepcopy(original)
            coverage.update(id=max(s["id"] for s in board["scenes"]) + 1,
                            shot_id=f"{parent}.C{count}", parent_shot_id=parent,
                            selected_asset=None, asset_timeline=[], edit_duration_secs=None,
                            source_trim_in=0, dialogue=[], review_status="planned", included=True,
                            production_notes="New coverage; choose its framing, action and cut before production.")
            board["scenes"].insert(index + 1, coverage)
            result = commit(project, path, board)
            result["scene_id"] = coverage["id"]
        await notify(slug, "storyboard-updated")
        return result

    def begin_export(project, job_id):
        from lib.film import run_export
        key = (str(project), job_id)
        if key not in export_tasks or export_tasks[key].done():
            async def perform():
                try:
                    await asyncio.to_thread(run_export, project, job_id)
                finally:
                    export_tasks.pop(key, None)
            export_tasks[key] = asyncio.create_task(perform())

    @app.post("/api/project/{slug}/studio/film/{film}/export")
    async def export_film(slug: str, film: str, body: FilmExport):
        from lib.film import plan_export, prepare_export, export_status
        with LOCK:
            project, path, board = load(slug)
            check(path, body.revision)
            board = copy.deepcopy(board)
        try:
            plan = await asyncio.to_thread(plan_export, project, board, film,
                                           **body.model_dump(exclude={"revision"}))
            record = prepare_export(project, plan)
            record = export_status(project, plan["job_id"])
            if record["status"] != "completed":
                begin_export(project, plan["job_id"])
            return {"job_id": plan["job_id"], "status": record["status"],
                    "film": film, "total_shots": len(plan["cuts"])}
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except subprocess.SubprocessError:
            raise HTTPException(400, "Cannot inspect an export source; check the selected media")

    @app.get("/api/project/{slug}/studio/exports/{job_id}")
    async def get_export(slug: str, job_id: str):
        from lib.film import export_status
        project, _, _ = load(slug)
        try:
            record = await asyncio.to_thread(export_status, project, job_id)
        except ValueError:
            raise HTTPException(404, "Export not found")
        return {k: record.get(k) for k in ("job_id", "status", "completed_shots", "output", "error", "active_lock", "output_sha256")}

    @app.post("/api/project/{slug}/studio/exports/{job_id}/resume")
    async def resume_export(slug: str, job_id: str):
        from lib.film import export_status
        project, _, _ = load(slug)
        try:
            record = await asyncio.to_thread(export_status, project, job_id)
        except ValueError:
            raise HTTPException(404, "Export not found")
        if record["status"] != "completed":
            begin_export(project, job_id)
        return {"job_id": job_id, "status": record["status"]}

    @app.post('/api/project/{slug}/studio/generations/plan')
    async def prepare_generation(slug: str, body: GenerationPlan):
        from lib.generation import plan_generation, prepare_generation
        with LOCK:
            project, path, board = load(slug)
            check(path, body.revision)
            board = copy.deepcopy(board)
        params = body.model_dump(exclude={'revision'})
        params['target'] = body.target.model_dump(exclude_none=True)
        try:
            plan = await asyncio.to_thread(plan_generation, project, board, **params)
            return await asyncio.to_thread(prepare_generation, project, plan)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))

    @app.get('/api/project/{slug}/studio/generations')
    async def generation_list(slug: str):
        from lib.generation import status
        project, _, _ = load(slug)
        records = [status(project, p.stem) for p in sorted((project / '.generation-jobs').glob('*.json'))]
        return records

    @app.get('/api/project/{slug}/studio/generations/{job_id}')
    async def generation_status(slug: str, job_id: str):
        from lib.generation import status
        project, _, _ = load(slug)
        try:
            return await asyncio.to_thread(status, project, job_id)
        except ValueError:
            raise HTTPException(404, 'Generation not found')

    @app.post('/api/project/{slug}/studio/generations/{job_id}/approve')
    async def approve_generation(slug: str, job_id: str, body: GenerationApproval):
        from lib.generation import approve
        from decimal import InvalidOperation
        project, _, _ = load(slug)
        try:
            return await asyncio.to_thread(approve, project, job_id, **body.model_dump())
        except (ValueError, InvalidOperation) as exc:
            raise HTTPException(400, str(exc))
        except FileNotFoundError:
            raise HTTPException(404, 'Generation not found')

    @app.post('/api/project/{slug}/studio/generations/{job_id}/run')
    async def run_generation(slug: str, job_id: str):
        from lib.generation import status, run_generation, path_for
        project, _, _ = load(slug)
        try:
            record = status(project, job_id)
        except ValueError:
            raise HTTPException(404, 'Generation not found')
        if record['status'] == 'awaiting_approval':
            raise HTTPException(403, 'Human authorization for the displayed cost is required')
        key = (str(project), job_id)
        if key not in generation_tasks or generation_tasks[key].done():
            async def perform():
                try:
                    await asyncio.to_thread(run_generation, project, job_id)
                except Exception as exc:
                    # Do not persist credential-bearing exception strings.
                    def save_error():
                        from lib.generation import lock_job
                        path = path_for(project, job_id)
                        with lock_job(path):
                            saved = json.loads(path.read_text())
                            saved.update(error='Cannot continue this job; check authorization, reference files and the issuing Hermes profile.',
                                         error_type=type(exc).__name__)
                            atomic_json(path, saved)
                    await asyncio.to_thread(save_error)
                finally:
                    generation_tasks.pop(key, None)
            generation_tasks[key] = asyncio.create_task(perform())
        return {'job_id': job_id, 'status': record['status'], 'local_action_started': True}

    @app.post('/api/project/{slug}/studio/generations/{job_id}/withdraw')
    async def withdraw_generation(slug: str, job_id: str):
        from lib.generation import withdraw
        project, _, _ = load(slug)
        try:
            return await asyncio.to_thread(withdraw, project, job_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except FileNotFoundError:
            raise HTTPException(404, 'Generation not found')

    @app.post('/api/project/{slug}/studio/generations/{job_id}/register')
    async def register_generation(slug: str, job_id: str, body: RevisionEdit):
        from lib.generation import status
        with LOCK:
            project, path, board = load(slug)
            check(path, body.revision)
            try:
                record = status(project, job_id)
            except ValueError:
                raise HTTPException(404, 'Generation not found')
            if record['status'] != 'completed':
                raise HTTPException(400, 'A verified downloaded candidate is required')
            output = local_file(project, record['output'])
            if revision(output) != record['output_sha256']:
                raise HTTPException(409, 'Generated candidate changed; recover its original first')
            target = record['plan']['target']
            if target['type'] == 'reference':
                item = board['continuity'][target['kind']].get(target['key'])
                if not item:
                    raise HTTPException(409, 'Reference target was removed')
                if not any(v.get('generation_job_id') == job_id for v in item['versions']):
                    entry = copy.deepcopy(record['plan']['target_specification'])
                    entry.update(id=f"v{len(item['versions'])+1:03d}", status='draft',
                                 image_paths=[record['output']], generation_job_id=job_id,
                                 en=record['plan']['prompt'],
                                 notes='Generated candidate; inspect before accepting. Prompt and input hashes are retained in the generation job.')
                    item['versions'].append(entry)
            else:
                scene = next((s for s in board['scenes'] if s['id'] == target['scene_id']), None)
                if scene is None:
                    raise HTTPException(409, 'Shot target was removed')
                if not any(t.get('generation_job_id') == job_id for t in scene.setdefault('takes', [])):
                    scene['takes'].append({'path': record['output'], 'generation_job_id': job_id,
                                           'type': 'video' if record['plan']['media_type'] == 'video' else 'still'})
            result = commit(project, path, board)
        await notify(slug, 'storyboard-updated')
        return {**result, 'job_id': job_id, 'selected_automatically': False}
