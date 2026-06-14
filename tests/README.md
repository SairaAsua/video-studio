# video-studio — Test Suite

Pytest test suite for the FastAPI + single-file SPA dashboard (app/main.py + app/static/index.html).

## Prerequisites (in this repo)

Use the project virtualenv:

```bash
/home/nicolas/Projects/video-studio/.venv/bin/python -m pip install -U pip
/home/nicolas/Projects/video-studio/.venv/bin/pip install -r requirements-dev.txt   # or: pytest pytest-cov httpx playwright
/home/nicolas/Projects/video-studio/.venv/bin/playwright install chromium
```

If `requirements-dev.txt` does not exist, the minimal set is already stated to be pre-installed in the environment:
`pytest`, `pytest-cov`, `httpx`, `playwright` + chromium browser.

Python interpreter for all commands below:

```bash
PY=/home/nicolas/Projects/video-studio/.venv/bin/python
```

## Running the tests

### API / unit-style tests (no server required, fully mocked)

```bash
$PY -m pytest tests/test_api.py -q --tb=line -m "not slow"
```

These tests:
- Use `fastapi.testclient.TestClient`
- Patch `app.main._generate_*_sync` (and `_edit_*`, `_generate_script_sync`) at the module level
- Create real temp asset files so that the server-side asset registration code sees valid paths
- Use an isolated `projects/` directory via monkey-patching `app.main.PROJECTS` for the duration of each test
- Clean up after themselves

### All tests (including E2E)

```bash
$PY -m pytest
```

### E2E (Playwright) tests — marked `@pytest.mark.slow`

These drive the real browser against a **running** dashboard:

```bash
# Terminal 1
$PY app/main.py
# or
$PY -m uvicorn app.main:app --host 127.0.0.1 --port 8900
```

```bash
# Terminal 2
$PY -m pytest -m slow -q --tb=short
# For visual debugging:
$PY -m pytest -m slow --headed
```

The E2E tests:
- Navigate to `http://127.0.0.1:8900`
- Exercise the SPA (project selector, + new project via `prompt()`, tab switching, script editor, Resources, Images tab toggles, scene list/strip navigation, etc.)
- Create throwaway projects with `e2e-*` prefixes when needed
- Are intentionally tolerant of environment differences (they `skip` rather than hard-fail when no suitable project data is present)

### With coverage

```bash
$PY -m pytest --cov=app --cov-report=term-missing tests/test_api.py -q
```

## Project layout (test side)

```
tests/
  __init__.py
  conftest.py          # fixtures: client, tmp_project, mock_gen_bridge, temp_projects_dir, browser, page
  test_api.py          # ~20 FastAPI endpoint + integration tests (mocked gen)
  test_e2e.py          # ~10 Playwright SPA interaction tests (marked slow)
  README.md
pytest.ini             # markers, testpaths, strict config
```

## What is covered (API tests)

- Project CRUD (list, create, duplicate, validation)
- GET project, 404 handling
- Script PUT + round-trip
- Scene PUT (text / visual_direction / 404)
- Image generate, video generate, edit image, edit video, extend video (all exercise mocked `_sync` wrappers + asset_timeline registration)
- Multipart add-version (still/video) + invalid type
- Timeline GET/PUT round-trip + SSE headers
- Prompts: defaults, PUT, GET, DELETE reset
- Media serving (200/404)
- Exports list
- Export presets GET + PUT custom
- SSE `/api/project/watch` stream smoke test
- Bonus: sources (upload/list/read/delete), generate-script (mocked), render-scene, render-scene-preview, delete render cache

## Mock strategy (important)

The production code dynamically loads `lib/gen_bridge` via `importlib` inside the `_generate_image_sync`, `_generate_video_sync`, `_edit_*`, `_extend_*`, and `_generate_script_sync` functions and then calls `mod.generate_image(...)` etc.

Therefore we patch the **wrappers that live in `app.main`**:

```python
with patch("app.main._generate_image_sync", ...) as ...
```

Each mock creates an actual file on disk (via `tempfile`) at the path it returns. This satisfies `Path(...).exists()` / size logic that runs after the bridge call when the endpoint registers the asset into `storyboard.json`.

Real network calls to xAI or catbox.moe are **never** made.

## E2E notes & flakiness

- The dashboard is a large single-file SPA with heavy delegated event handlers and async reloads on SSE.
- Tests prefer stable IDs (`#project-select`, `#new-project-btn`, `#v3-tab-bar [data-v3-tab="..."]`, `#images-prompt`, `#script-editor`, etc.).
- Native `window.prompt()` is used for "New project"; Playwright intercepts it with `page.once("dialog", ...)`.
- Tests that require multiple scenes prefer the existing `costa-rica` project when present.
- All E2E tests are marked `slow` so that `pytest -m "not slow"` stays fast and server-free.

## Adding more tests

- API tests go in `test_api.py`. Add a `mock_gen_bridge` usage when you touch generation endpoints.
- New E2E interactions go in `test_e2e.py`. Prefer `expect(...).to_be_visible()`, `page.locator("#id")`, and explicit `switch_v3_tab` / `switch_tools_tab` helpers.
- Keep using the `.venv` Python for everything.
- Never edit files outside `tests/` + `pytest.ini`.

## Troubleshooting

- `ModuleNotFoundError: No module named 'app'`: run pytest from the repo root (`/home/nicolas/Projects/video-studio`).
- E2E tests all skipped: the server is not running on :8900, or all projects were filtered out.
- "Invalid slug" in create tests: the temp dir patch or re-match in `app.main` changed — check `SLUG_RE`.
- Playwright browser missing: `playwright install chromium` inside the venv.
- SSE test hangs: the streaming test only reads the first chunk; it is intentionally lightweight.

Happy testing!
