"""Shared pytest fixtures for the video-studio test suite.

Two test layers live in this package:

* ``test_api.py`` — fast, in-process tests driven by FastAPI's ``TestClient``.
  Every API test runs against an **isolated, empty temp** ``PROJECTS`` directory
  (``app.main.PROJECTS`` is monkeypatched per-test), so the real ``projects/``
  tree is never touched and tests never collide.

* ``test_e2e.py`` — browser tests (Playwright/Chromium) that drive the real
  dashboard against a **running** server on ``http://127.0.0.1:8900``. These are
  marked ``@pytest.mark.slow`` and skip automatically if no server is reachable
  or Playwright/Chromium is unavailable.

The external generation bridge (``lib/gen_bridge.py`` → xAI / catbox.moe) is
never called: the ``mock_gen`` fixture replaces the ``app.main._*_sync`` thread
wrappers with fakes that write a small local file and return the shapes the
endpoints expect. Subprocess renders (``lib/assemble.py``) are stubbed by
``mock_subprocess`` so no ffmpeg is spawned.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Make the project importable regardless of pytest's rootdir / import mode.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.main import app  # noqa: E402  (import after sys.path tweak)
import app.main as main  # noqa: E402  (module handle for monkeypatching)

from fastapi.testclient import TestClient  # noqa: E402

BASE_URL = "http://127.0.0.1:8900"

# A real 1x1 PNG — small but valid, so a browser <img> can actually render it.
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
# Minimal ISO-BMFF-ish header — enough to look like an .mp4 on disk. The bytes
# are never decoded because the generation bridge is mocked; the endpoints only
# check the source file's existence/extension.
MP4_STUB = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isomvideo-stub"


# ===========================================================================
# API-layer fixtures (in-process, isolated temp PROJECTS)
# ===========================================================================

@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch) -> Path:
    """Point ``app.main.PROJECTS`` at a fresh temp dir for this test.

    Every helper in ``app.main`` (``project_dir``, ``list_projects``,
    ``_prompts_path`` …) reads the module global at call time, so this single
    patch fully isolates the test from the real ``projects/`` tree.
    """
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(main, "PROJECTS", root)
    return root


@pytest.fixture
def client(projects_root: Path) -> TestClient:
    """FastAPI test client bound to the isolated temp PROJECTS."""
    return TestClient(app)


# ``api_client`` is an alias kept for tests that prefer the httpx-flavoured name.
@pytest.fixture
def api_client(client: TestClient) -> TestClient:
    return client


@pytest.fixture
def tmp_project(projects_root: Path) -> SimpleNamespace:
    """Create a fully-structured project with one seeded scene.

    Layout::

        projects/<slug>/
            scenes/  audio/  renders/  exports/  sources/
            script.md   SPINE.md   storyboard.json
            scenes/seed.png   # real 1px PNG (still source)
            scenes/seed.mp4   # stub bytes (video source)

    The seeded ``still`` asset means the project passes the ``/render`` gate,
    and both seed files exist on disk so the image/video *edit* and *extend*
    endpoints (which validate the source path) work out of the box.

    Returns a namespace with ``slug``, ``dir``, ``scene_id``,
    ``missing_scene_id``, ``still_path`` and ``video_path``.
    """
    slug = "test-proj"
    proj = projects_root / slug
    for sub in ("scenes", "audio", "renders", "exports", "sources"):
        (proj / sub).mkdir(parents=True)

    still_rel = "scenes/seed.png"
    video_rel = "scenes/seed.mp4"
    (proj / still_rel).write_bytes(PNG_1PX)
    (proj / video_rel).write_bytes(MP4_STUB)

    (proj / "script.md").write_text("# Seed script\n\nNarration goes here.\n")
    (proj / "SPINE.md").write_text(f"# SPINE — {slug}\n\nNarrative spine.\n")

    storyboard = {
        "project": slug,
        "scenes": [
            {
                "id": 1,
                "text": "A seeded test scene.",
                "visual_direction": "A serene test landscape at golden hour.",
                "asset_timeline": [
                    {
                        "type": "still",
                        "path": still_rel,
                        "layer": 0,
                        "caption_phrases": [],
                        "prompt": "seed still",
                    },
                    {
                        "type": "video",
                        "path": video_rel,
                        "duration_secs": 5.0,
                        "aspect_ratio": "16:9",
                        "layer": 0,
                        "caption_phrases": [],
                    },
                ],
            }
        ],
    }
    (proj / "storyboard.json").write_text(json.dumps(storyboard, indent=2))

    return SimpleNamespace(
        slug=slug,
        dir=proj,
        scene_id=1,
        missing_scene_id=999,
        still_path=still_rel,
        video_path=video_rel,
    )


@pytest.fixture
def mock_gen(monkeypatch) -> SimpleNamespace:
    """Replace the ``app.main._*_sync`` generation wrappers with local fakes.

    Each fake writes a small real file into the ``output_dir`` it is handed (the
    scene's ``scenes/`` dir) and returns exactly the dict shape the endpoint
    consumes — so the asset_timeline registration that follows finds a real
    file and the response carries a believable ``scenes/<name>`` path. No
    xAI / catbox.moe network call is ever made.

    Returns a namespace of the underlying ``MagicMock`` objects so tests may
    additionally assert ``mock_gen.image.called`` etc.
    """

    def _write(output_dir: str, name: str, data: bytes) -> str:
        d = Path(output_dir)
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_bytes(data)
        return str(p)

    def _image(prompt, output_dir):
        return {"ok": True, "image_path": _write(output_dir, "gen-12345.jpg", PNG_1PX)}

    def _video(prompt, output_dir, image_path, duration, aspect, resolution, slug=""):
        return {"ok": True, "video_path": _write(output_dir, "genvid-12345.mp4", MP4_STUB)}

    def _edit_video(source_path, prompt, output_dir):
        return {"ok": True, "video_path": _write(output_dir, "edit-video-12345.mp4", MP4_STUB)}

    def _extend_video(source_path, prompt, output_dir, duration_secs):
        return {"ok": True, "video_path": _write(output_dir, "extend-video-12345.mp4", MP4_STUB)}

    def _edit_image(source_path, prompt, output_dir, slug):
        return {
            "ok": True,
            "path": _write(output_dir, "edit-12345.jpg", PNG_1PX),
            "url": "http://example.com/img.jpg",
            "width": None,
            "height": None,
            "revised_prompt": f"revised: {prompt}",
        }

    def _script(sources_text, instructions):
        return {"ok": True, "script": "## Scene 1 — Generated (0:00)\nNarration."}

    mocks = SimpleNamespace(
        image=MagicMock(side_effect=_image),
        video=MagicMock(side_effect=_video),
        edit_video=MagicMock(side_effect=_edit_video),
        extend_video=MagicMock(side_effect=_extend_video),
        edit_image=MagicMock(side_effect=_edit_image),
        script=MagicMock(side_effect=_script),
    )

    monkeypatch.setattr(main, "_generate_image_sync", mocks.image)
    monkeypatch.setattr(main, "_generate_video_sync", mocks.video)
    monkeypatch.setattr(main, "_edit_video_sync", mocks.edit_video)
    monkeypatch.setattr(main, "_extend_video_sync", mocks.extend_video)
    monkeypatch.setattr(main, "_edit_image_sync", mocks.edit_image)
    monkeypatch.setattr(main, "_generate_script_sync", mocks.script)
    return mocks


@pytest.fixture
def mock_subprocess(monkeypatch) -> list:
    """Stub ``app.main.subprocess.run`` so render endpoints never shell out.

    Returns a list capturing each ``cmd`` passed to ``run`` so tests can assert
    ``lib/assemble.py`` was invoked with the expected flags (and how often).
    """
    import subprocess as sp

    calls: list = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return sp.CompletedProcess(cmd, returncode=0, stdout="rendered ok", stderr="")

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    return calls


# ===========================================================================
# E2E-layer fixtures (Playwright against a live server)
# ===========================================================================

@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def live_server(base_url: str) -> str:
    """Skip the whole e2e layer unless a dashboard is reachable."""
    import httpx

    try:
        resp = httpx.get(base_url, timeout=2.0)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — any connection failure → skip
        pytest.skip(
            f"No live server at {base_url} ({exc}); "
            f"start it with `python app/main.py` to run e2e tests."
        )
    return base_url


@pytest.fixture(scope="session")
def _playwright(live_server):
    """Session-wide Playwright driver, imported lazily so the API suite has no
    hard dependency on Playwright being installed."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed (pip install playwright; playwright install chromium)")
    pw = sync_playwright().start()
    try:
        yield pw
    finally:
        pw.stop()


@pytest.fixture(scope="session")
def browser(_playwright):
    try:
        b = _playwright.chromium.launch(headless=True)
    except Exception as exc:  # noqa: BLE001 — browser binary missing → skip
        pytest.skip(f"Could not launch Chromium ({exc}); run `playwright install chromium`.")
    try:
        yield b
    finally:
        b.close()


@pytest.fixture
def context(browser, base_url: str):
    ctx = browser.new_context(base_url=base_url, viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(10_000)
    try:
        yield ctx
    finally:
        ctx.close()


@pytest.fixture
def page(context):
    pg = context.new_page()
    try:
        yield pg
    finally:
        pg.close()


@pytest.fixture(scope="session")
def e2e_project(live_server):
    """Create a deterministic project on the *live* server for e2e tests.

    Created via the real API, then its ``storyboard.json`` is written directly
    on disk with two scenes + a seed still (the server reads the filesystem on
    every request, so the change is picked up immediately). Removed on teardown.

    Yields the project slug.
    """
    import shutil

    import httpx

    slug = "e2e-autotest"
    proj = PROJECT_ROOT / "projects" / slug

    if proj.exists():
        shutil.rmtree(proj, ignore_errors=True)
    httpx.post(f"{live_server}/api/projects", json={"slug": slug}, timeout=10.0)

    # Ensure structure exists even if create was a no-op (e.g. 409 race).
    for sub in ("scenes", "audio", "renders", "exports", "sources"):
        (proj / sub).mkdir(parents=True, exist_ok=True)
    (proj / "scenes" / "seed.png").write_bytes(PNG_1PX)
    (proj / "script.md").write_text("# E2E seed script\n\nNarration.\n")
    storyboard = {
        "project": slug,
        "scenes": [
            {
                "id": 1,
                "text": "E2E seeded scene one.",
                "visual_direction": "A calm test vista.",
                "asset_timeline": [
                    {"type": "still", "path": "scenes/seed.png", "layer": 0,
                     "caption_phrases": [], "prompt": "seed"}
                ],
            },
            {
                "id": 2,
                "text": "E2E seeded scene two.",
                "visual_direction": "A second test vista.",
                "asset_timeline": [],
            },
        ],
    }
    (proj / "storyboard.json").write_text(json.dumps(storyboard, indent=2))

    try:
        yield slug
    finally:
        shutil.rmtree(proj, ignore_errors=True)


# ---------------------------------------------------------------------------
# E2E helper functions (imported by test_e2e.py)
# ---------------------------------------------------------------------------

def select_project(page, name: str) -> None:
    """Choose a project in the header <select> and wait for the load to settle.

    ``loadProject()`` either renders the v3 workspace shell (scenes present) or
    leaves the empty grid card (no scenes); wait for whichever settles.
    """
    page.select_option("#project-select", value=name)
    page.wait_for_selector(
        "#v3-scene-list .v3-scene-item, #storyboard-grid .scene-card.empty",
        timeout=10_000,
    )


def click_tab(page, tab_name: str) -> None:
    """Click a workspace tab (``data-v3-tab``) or tools tab (``data-tools-tab``)."""
    page.click(f'[data-v3-tab="{tab_name}"], [data-tools-tab="{tab_name}"]')


def navigate_to_scene(page, scene_index: int) -> None:
    """Click the Nth scene (0-based) in the v3 scene list."""
    page.click(f'.v3-scene-item[data-scene-index="{scene_index}"]')
