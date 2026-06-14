"""API tests for the video-studio FastAPI dashboard.

Uses the isolated PROJECTS fixture (``projects_root``) so the real
``projects/`` tree is never touched.

Run with: .venv/bin/python -m pytest tests/test_api.py -q --tb=short
"""
import json
from pathlib import Path

import pytest


# ===========================================================================
# Project listing + creation
# ===========================================================================

def test_list_projects(client, projects_root):
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_project(client, projects_root):
    slug = "test-create-abc"
    resp = client.post("/api/projects", json={"slug": slug})
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"ok": True, "slug": slug}

    proj = projects_root / slug
    assert proj.exists()
    assert (proj / "storyboard.json").exists()
    assert (proj / "SPINE.md").exists()
    for sub in ("scenes", "audio", "renders", "exports", "sources"):
        assert (proj / sub).is_dir()

    resp = client.get("/api/projects")
    assert slug in resp.json()


def test_create_project_duplicate(client, projects_root):
    slug = "dup-proj"
    r1 = client.post("/api/projects", json={"slug": slug})
    assert r1.status_code == 200
    r2 = client.post("/api/projects", json={"slug": slug})
    assert r2.status_code == 409


def test_create_project_invalid_slug(client, projects_root):
    bad = ["UPPER", "has space", "bad!", "", "-start"]
    for s in bad:
        r = client.post("/api/projects", json={"slug": s})
        assert r.status_code == 400, s


# ===========================================================================
# Get project
# ===========================================================================

def test_get_project(client, tmp_project):
    p = tmp_project
    resp = client.get(f"/api/project/{p.slug}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == p.slug
    assert "storyboard" in data
    assert isinstance(data["storyboard"]["scenes"], list)


def test_get_project_404(client):
    resp = client.get("/api/project/does-not-exist-xyz")
    assert resp.status_code == 404


# ===========================================================================
# Script + scene updates (uses the seeded tmp_project)
# ===========================================================================

def test_update_script(client, tmp_project):
    p = tmp_project
    content = "# Test script\n\n## Scene 1\nHello."
    r = client.put(f"/api/project/{p.slug}/script", json={"content": content})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    data = client.get(f"/api/project/{p.slug}").json()
    assert data["script"] == content


def test_update_scene(client, tmp_project):
    p = tmp_project
    r = client.put(
        f"/api/project/{p.slug}/storyboard/{p.scene_id}",
        json={"text": "Updated narration.", "visual_direction": "Wide establishing shot."},
    )
    assert r.status_code == 200

    data = client.get(f"/api/project/{p.slug}").json()
    scene = data["storyboard"]["scenes"][0]
    assert scene["text"] == "Updated narration."
    assert scene["visual_direction"] == "Wide establishing shot."


def test_update_scene_404(client, tmp_project):
    p = tmp_project
    r = client.put(f"/api/project/{p.slug}/storyboard/{p.missing_scene_id}", json={"text": "x"})
    assert r.status_code == 404


# ===========================================================================
# Image / Video generation + edits (mocked gen_bridge via mock_gen fixture)
# ===========================================================================

def test_generate_image(client, tmp_project, mock_gen):
    p = tmp_project
    r = client.post(
        f"/api/project/{p.slug}/storyboard/{p.scene_id}/image",
        json={"prompt": "golden hour coast"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["scene"] == p.scene_id
    assert "scenes/" in body["path"]

    data = client.get(f"/api/project/{p.slug}").json()
    tl = data["storyboard"]["scenes"][0]["asset_timeline"]
    assert len(tl) >= 3  # 2 seeds + 1 newly generated
    assert any(e.get("prompt") == "golden hour coast" for e in tl)
    assert mock_gen.image.called


def test_generate_video(client, tmp_project, mock_gen):
    p = tmp_project
    r = client.post(
        f"/api/project/{p.slug}/storyboard/{p.scene_id}/video",
        json={"prompt": "slow pan", "duration_secs": 6, "aspect_ratio": "16:9", "resolution": "720p"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert mock_gen.video.called


def test_edit_image(client, tmp_project, mock_gen):
    p = tmp_project
    r = client.post(
        f"/api/project/{p.slug}/storyboard/{p.scene_id}/image/edit",
        json={"source_image_path": p.still_path, "prompt": "make it warmer"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # Storyboard re-read: the new entry should carry edited_from
    sb = json.loads((p.dir / "storyboard.json").read_text())
    assert any("edited_from" in e for e in sb["scenes"][0]["asset_timeline"])
    assert mock_gen.edit_image.called


def test_edit_video(client, tmp_project, mock_gen):
    p = tmp_project
    r = client.post(
        f"/api/project/{p.slug}/storyboard/{p.scene_id}/video/edit",
        json={"source_video_path": p.video_path, "prompt": "slow motion"},
    )
    assert r.status_code == 200
    assert mock_gen.edit_video.called


def test_extend_video(client, tmp_project, mock_gen):
    p = tmp_project
    r = client.post(
        f"/api/project/{p.slug}/storyboard/{p.scene_id}/video/extend",
        json={"source_video_path": p.video_path, "prompt": "continue walking", "duration_secs": 4},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "extended"
    assert mock_gen.extend_video.called


# ===========================================================================
# Add version (multipart upload) + timeline
# ===========================================================================

def test_add_version_upload(client, tmp_project):
    p = tmp_project
    files = {"file": ("myclip.mp4", b"\x00mp4data" * 100, "video/mp4")}
    data = {"type": "video", "prompt": "test upload"}
    r = client.post(f"/api/project/{p.slug}/storyboard/{p.scene_id}/add-version", files=files, data=data)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "scenes/" in body["path"]
    assert (p.dir / body["path"]).exists()


def test_add_version_invalid_type(client, tmp_project):
    p = tmp_project
    files = {"file": ("x.jpg", b"img", "image/jpeg")}
    r = client.post(f"/api/project/{p.slug}/storyboard/{p.scene_id}/add-version", files=files, data={"type": "badtype"})
    assert r.status_code == 400


def test_timeline_get_put(client, tmp_project):
    p = tmp_project
    r = client.get(f"/api/project/{p.slug}/storyboard/{p.scene_id}/timeline")
    assert r.status_code == 200
    assert len(r.json()["asset_timeline"]) >= 1

    new_tl = [{"type": "still", "path": "scenes/b.jpg", "layer": 0}]
    r2 = client.put(f"/api/project/{p.slug}/storyboard/{p.scene_id}/timeline", json={"asset_timeline": new_tl})
    assert r2.status_code == 200

    fetched = client.get(f"/api/project/{p.slug}/storyboard/{p.scene_id}/timeline").json()
    assert fetched["asset_timeline"] == new_tl


# ===========================================================================
# Prompts CRUD
# ===========================================================================

def test_prompts_default(client, tmp_project):
    p = tmp_project
    r = client.get(f"/api/project/{p.slug}/prompts")
    assert r.status_code == 200
    data = r.json()
    assert data["is_default"] is True
    assert "image" in data["prompts"]
    assert "video" in data["prompts"]


def test_prompts_crud(client, tmp_project):
    p = tmp_project
    custom = {"image": {"beacon_world": "test"}, "video": {"scene_template": "x"}}
    r = client.put(f"/api/project/{p.slug}/prompts", json={"prompts": custom})
    assert r.status_code == 200

    r2 = client.get(f"/api/project/{p.slug}/prompts")
    assert r2.json()["is_default"] is False
    assert r2.json()["prompts"]["image"]["beacon_world"] == "test"

    r3 = client.delete(f"/api/project/{p.slug}/prompts")
    assert r3.status_code == 200

    r4 = client.get(f"/api/project/{p.slug}/prompts")
    assert r4.json()["is_default"] is True


# ===========================================================================
# Media serving + exports + presets
# ===========================================================================

def test_media_serving(client, tmp_project):
    p = tmp_project
    # seed.png already exists in tmp_project
    r = client.get(f"/api/project/{p.slug}/media/scenes/seed.png")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")

    r404 = client.get(f"/api/project/{p.slug}/media/scenes/nope.jpg")
    assert r404.status_code == 404


def test_exports_list(client, tmp_project):
    p = tmp_project
    exp = p.dir / "exports"
    exp.mkdir(exist_ok=True)
    (exp / "demo.mp4").write_bytes(b"fakevid")

    r = client.get(f"/api/project/{p.slug}/exports")
    assert r.status_code == 200
    data = r.json()
    assert "exports" in data
    assert any(e["name"] == "demo.mp4" for e in data["exports"])


def test_export_presets(client, tmp_project):
    p = tmp_project
    r = client.get(f"/api/project/{p.slug}/export-presets")
    assert r.status_code == 200
    presets = r.json()["presets"]
    assert len(presets) > 0
    assert any("1080p" in pr.get("label", "") for pr in presets)

    custom = {"my_720": {"width": 1280, "height": 720, "crf": 28, "preset": "fast"}}
    r2 = client.put(f"/api/project/{p.slug}/export-presets", json=custom)
    assert r2.status_code == 200
    # returned merged contains our key
    merged = r2.json()
    assert "my_720" in merged
    assert merged["my_720"]["width"] == 1280


# ===========================================================================
# Render endpoints (uses mock_subprocess to avoid shelling out)
# ===========================================================================

def test_render_scene(client, tmp_project, mock_subprocess):
    p = tmp_project
    r = client.post(f"/api/project/{p.slug}/storyboard/{p.scene_id}/render-scene", json={"quality": "draft"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "url" in body
    assert mock_subprocess  # sanity: fixture was used


def test_render_scene_preview(client, tmp_project, mock_subprocess):
    p = tmp_project
    r = client.post(
        f"/api/project/{p.slug}/render-scene-preview",
        json={"scene_id": p.scene_id, "width": 1280, "height": 720, "crf": 28, "preset": "fast"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_render_full(client, tmp_project, mock_subprocess):
    p = tmp_project
    r = client.post(f"/api/project/{p.slug}/render", json={"width": 1920, "height": 1080, "crf": 23, "preset": "medium"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_scene_render(client, tmp_project):
    p = tmp_project
    rdir = p.dir / "renders"
    rdir.mkdir(exist_ok=True)
    (rdir / "scene-01.mp4").write_bytes(b"x")
    (rdir / "scene-01-cache.json").write_text("{}")

    r = client.post(f"/api/project/{p.slug}/storyboard/1/render-scene/delete")
    assert r.status_code == 200
    assert not (rdir / "scene-01.mp4").exists()


# ===========================================================================
# Sources upload / list / read / delete
# ===========================================================================

def test_sources_upload_list_read_delete(client, tmp_project):
    p = tmp_project
    f = {"file": ("notes.md", b"# source notes\nTest content.", "text/markdown")}
    r = client.post(f"/api/project/{p.slug}/sources", files=f)
    assert r.status_code == 200
    assert r.json()["filename"] == "notes.md"

    ls = client.get(f"/api/project/{p.slug}/sources").json()
    assert any(s["filename"] == "notes.md" for s in ls["sources"])

    rd = client.get(f"/api/project/{p.slug}/sources/notes.md").json()
    assert rd["content"].startswith("# source notes")

    d = client.delete(f"/api/project/{p.slug}/sources/notes.md")
    assert d.status_code == 200
    assert client.get(f"/api/project/{p.slug}/sources/notes.md").status_code == 404


# ===========================================================================
# Script generation
# ===========================================================================

def test_generate_script_endpoint(client, tmp_project, mock_gen):
    p = tmp_project
    src_dir = p.dir / "sources"
    src_dir.mkdir(exist_ok=True)
    (src_dir / "a.txt").write_text("Fact one.")
    (src_dir / "b.txt").write_text("Fact two.")

    r = client.post(
        f"/api/project/{p.slug}/generate-script",
        json={"sources": ["a.txt", "b.txt"], "instructions": "Make it exciting."},
    )
    assert r.status_code == 200
    assert "script" in r.json()
    assert mock_gen.script.called


def test_generate_script_requires_sources(client, tmp_project):
    p = tmp_project
    r = client.post(f"/api/project/{p.slug}/generate-script", json={"sources": [], "instructions": ""})
    assert r.status_code == 400


# ===========================================================================
# SSE stream (light smoke test)
# ===========================================================================

@pytest.mark.skip(reason="SSE stream blocks TestClient — verify manually or via integration test")
def test_sse_stream(client):
    with client.stream("GET", "/api/project/watch") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
