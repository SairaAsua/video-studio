#!/usr/bin/env python3
"""Direct xAI image/video generation — per official docs at docs.x.ai."""
import argparse, hashlib, json, os, sys, time, requests
from pathlib import Path

BASE = "https://api.x.ai/v1"

def _credentials(credential_identity=None, oauth_only=False):
    """Keep OAuth selection/refresh in Hermes, without copying its auth store."""
    explicit_key = os.environ.get("XAI_API_KEY", "").strip()
    if explicit_key and not oauth_only:
        identity = 'explicit-' + hashlib.sha256(explicit_key.encode()).hexdigest()
        if credential_identity and credential_identity != identity:
            raise RuntimeError('The generation belongs to a different xAI credential')
        return {"api_key": explicit_key,
                "base_url": os.environ.get("XAI_BASE_URL", BASE).rstrip("/"),
                "user_agent": "Video-Studio", "credential_identity": identity,
                "provider": "xai", "auth_type": "api_key"}
    agent_root = Path(os.environ.get("HERMES_AGENT_ROOT", Path.home() / ".hermes/hermes-agent"))
    if str(agent_root) not in sys.path:
        sys.path.insert(0, str(agent_root))
    try:
        from tools.xai_http import resolve_xai_http_credentials, hermes_xai_user_agent
    except ImportError:
        raise RuntimeError("Hermes credential resolver unavailable; configure HERMES_AGENT_ROOT or XAI_API_KEY") from None
    credentials = resolve_xai_http_credentials()
    # Public pool metadata lets an asynchronous job retain its issuing account
    # even after a refresh changes the access token or selection rotates.
    from agent.credential_pool import load_pool
    from hermes_constants import get_hermes_home
    pool = load_pool('xai-oauth')
    for entry in pool.entries():
        identity = hashlib.sha256((str(get_hermes_home().resolve())+':'+entry.id).encode()).hexdigest()
        matches = identity == credential_identity if credential_identity else entry.runtime_api_key == credentials.get('api_key')
        if matches:
            credentials = {**credentials, 'api_key': entry.runtime_api_key,
                           'base_url': entry.runtime_base_url or BASE,
                           'auth_type': entry.auth_type,
                           'credential_identity': identity}
            break
    if oauth_only and (credentials.get('provider') != 'xai-oauth' or credentials.get('auth_type') != 'oauth'):
        raise RuntimeError('Subscription mode requires an OAuth grant; paid API-key fallback is forbidden')
    if not credentials.get('credential_identity'):
        credentials['credential_identity'] = 'explicit-' + hashlib.sha256(credentials.get('api_key', '').encode()).hexdigest()
    if credential_identity and credentials.get('credential_identity') != credential_identity:
        raise RuntimeError('The issuing Hermes credential is not available in this profile')
    if not credentials.get("api_key"):
        raise RuntimeError("No xAI credential available in the selected Hermes profile")
    return {**credentials, "user_agent": hermes_xai_user_agent()}


def _api_request(method, path, **kwargs):
    # Resolve per request so token rotation and pool selection are respected.
    # Do not automatically retry a paid POST after an ambiguous response.
    credentials = _credentials()
    return requests.request(method, credentials["base_url"].rstrip("/") + path,
                            headers={"Authorization": "Bearer " + credentials["api_key"],
                                     "Content-Type": "application/json",
                                     "User-Agent": credentials["user_agent"]}, **kwargs)

# ── Image ──

def generate_image(prompt, output_dir):
    """POST /v1/images/generations → download → save."""
    r = _api_request("POST", "/images/generations", json={
        "model": "grok-imagine-image", "prompt": prompt, "n": 1, "response_format": "url"
    }, timeout=120)
    if r.status_code != 200:
        return False, f"xAI API {r.status_code}: {r.text[:200]}"
    url = r.json().get('data', [{}])[0].get('url')
    if not url:
        return False, f"No URL: {json.dumps(r.json())[:200]}"
    img = requests.get(url, timeout=60)
    if img.status_code != 200:
        return False, f"Download failed: {img.status_code}"
    d = Path(output_dir); d.mkdir(parents=True, exist_ok=True)
    p = d / f"gen-{int(time.time())}.jpg"
    p.write_bytes(img.content)
    return True, str(p)

# ── Video ──

def generate_video(prompt, output_dir, image_path=None, duration=8, aspect_ratio="16:9", resolution="720p", slug=""):
    """
    POST /v1/videos/generations → poll GET /v1/videos/{id} until status="done".
    Official docs: status="done", video.url for download.
    Optional: image_path for image-to-video (first frame = source image).
    """
    body = {
        "model": "grok-imagine-video",
        "prompt": prompt,
        "duration": int(duration),
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }
    if image_path:
        # Upload to catbox.moe for a temporary public URL (direct raw file access)
        import subprocess
        try:
            r = subprocess.run(
                ["curl", "-sS", "-F", "reqtype=fileupload", "-F", f"fileToUpload=@{image_path}",
                 "https://catbox.moe/user/api.php"],
                capture_output=True, text=True, timeout=30
            )
            public_url = r.stdout.strip()
            if r.returncode != 0 or not public_url.startswith("http"):
                return False, f"Upload to catbox.moe failed: {r.stderr or public_url}"
            body["image"] = {"url": public_url, "type": "image_url"}
        except Exception as e:
            return False, f"Upload to catbox.moe error: {e}"

    r = _api_request("POST", "/videos/generations", json=body, timeout=30)
    if r.status_code != 200:
        return False, f"xAI video API {r.status_code}: {r.text[:300]}"

    rid = r.json().get("request_id")
    if not rid:
        return False, f"No request_id: {json.dumps(r.json())[:200]}"

    # Poll GET /v1/videos/{request_id}
    for attempt in range(45):  # up to ~7.5 min
        time.sleep(10)
        pr = _api_request("GET", f"/videos/{rid}", timeout=30)
        if pr.status_code != 200:
            continue
        data = pr.json()
        status = data.get("status", "")
        progress = data.get("progress", 0)
        print(f"  Video poll {attempt+1}: status={status} progress={progress}%", file=sys.stderr)
        if status == "done":
            video_url = data.get("video", {}).get("url")
            if not video_url:
                return False, f"Status 'done' but no video.url: {json.dumps(data)[:200]}"
            break
        elif status == "failed":
            return False, f"Video failed: {data.get('error', 'unknown')}"
    else:
        return False, f"Video polling timed out ({45*10}s)"

    # Download
    vr = requests.get(video_url, timeout=120)
    if vr.status_code != 200:
        return False, f"Video download failed: {vr.status_code}"
    d = Path(output_dir); d.mkdir(parents=True, exist_ok=True)
    p = d / f"genvid-{int(time.time())}.mp4"
    p.write_bytes(vr.content)
    return True, str(p)


def extend_video(source_path, prompt, output_dir, duration_secs=4):
    """Extend an existing video via POST /v1/videos/extensions + poll.
    Source video is sent as base64 data URL (video/mp4). Follows generate_video patterns.
    Duration clamped to 1-15s. Saves as extend-video-{ts}.mp4. Returns (ok, path_or_err).
    """
    dur = int(duration_secs)
    if dur < 1 or dur > 15:
        print(f"Warning: duration {dur} out of range [1,15], clamping", file=sys.stderr)
        dur = max(1, min(15, dur))

    # Read video as base64 data URL (mp4)
    import base64
    vid_data = Path(source_path).read_bytes()
    b64 = base64.b64encode(vid_data).decode()
    video_b64 = f"data:video/mp4;base64,{b64}"

    body = {
        "model": "grok-imagine-video",
        "prompt": prompt,
        "video": {"base64": video_b64},
        "duration": dur,
    }

    r = _api_request("POST", "/videos/extensions", json=body, timeout=30)
    if r.status_code != 200:
        return False, f"xAI video extend API {r.status_code}: {r.text[:300]}"

    rid = r.json().get("request_id")
    if not rid:
        return False, f"No request_id: {json.dumps(r.json())[:200]}"

    # Poll GET /v1/videos/{request_id} — exact log format required
    for attempt in range(45):  # up to ~7.5 min
        time.sleep(10)
        pr = _api_request("GET", f"/videos/{rid}", timeout=30)
        if pr.status_code != 200:
            continue
        data = pr.json()
        status = data.get("status", "")
        progress = data.get("progress", 0)
        print(f"  Video extend poll {attempt+1}: status={status} progress={progress}%", file=sys.stderr)
        if status == "done":
            video_url = data.get("video", {}).get("url")
            if not video_url:
                return False, f"Status 'done' but no video.url: {json.dumps(data)[:200]}"
            break
        elif status == "failed":
            return False, f"Video failed: {data.get('error', 'unknown')}"
    else:
        return False, f"Video polling timed out ({45*10}s)"

    # Download
    vr = requests.get(video_url, timeout=120)
    if vr.status_code != 200:
        return False, f"Video download failed: {vr.status_code}"
    d = Path(output_dir); d.mkdir(parents=True, exist_ok=True)
    p = d / f"extend-video-{int(time.time())}.mp4"
    p.write_bytes(vr.content)
    return True, str(p)


def edit_video(source_path, prompt, output_dir):
    """POST /v1/videos/edits → poll GET /v1/videos/{id} until status="done".
    Source video read + base64 data URL (flat string under "video" per task).
    Output filename prefix "edit-video-" . Returns (True, path) or (False, err).
    """
    import base64
    vid_data = Path(source_path).read_bytes()
    b64 = base64.b64encode(vid_data).decode()
    body = {
        "model": "grok-imagine-video",
        "prompt": prompt,
        "video": f"data:video/mp4;base64,{b64}"
    }

    r = _api_request("POST", "/videos/edits", json=body, timeout=30)
    if r.status_code != 200:
        return False, f"xAI video API {r.status_code}: {r.text[:300]}"

    rid = r.json().get("request_id")
    if not rid:
        return False, f"No request_id: {json.dumps(r.json())[:200]}"

    # Poll GET /v1/videos/{request_id}
    for attempt in range(45):  # up to ~7.5 min
        time.sleep(10)
        pr = _api_request("GET", f"/videos/{rid}", timeout=30)
        if pr.status_code != 200:
            continue
        data = pr.json()
        status = data.get("status", "")
        progress = data.get("progress", 0)
        print(f"  Video edit poll {attempt+1}: status={status} progress={progress}%", file=sys.stderr)
        if status == "done":
            video_url = data.get("video", {}).get("url")
            if not video_url:
                return False, f"Status 'done' but no video.url: {json.dumps(data)[:200]}"
            break
        elif status == "failed":
            return False, f"Video failed: {data.get('error', 'unknown')}"
    else:
        return False, "Video edit timed out after 7.5 minutes"

    # Download
    vr = requests.get(video_url, timeout=120)
    if vr.status_code != 200:
        return False, f"Video download failed: {vr.status_code}"
    d = Path(output_dir); d.mkdir(parents=True, exist_ok=True)
    p = d / f"edit-video-{int(time.time())}.mp4"
    p.write_bytes(vr.content)
    return True, str(p)


# ── Image Edit ──

def edit_image(source_path, prompt, output_dir, slug):
    """POST /v1/images/edits → public URL reference for source → download edited → save.
    Returns rich dict on success per spec. Uses /api/project/{slug}/media/{rel} public URL.
    """
    # Derive relative_path from source_path relative to projects/{slug}/
    src = str(source_path).replace("\\", "/").lstrip("/")
    prefix = f"projects/{slug}/"
    if src.startswith(prefix):
        relative_path = src[len(prefix):]
    else:
        pth = Path(source_path)
        try:
            proj_dir = Path("projects") / slug
            relative_path = str(pth.relative_to(proj_dir)).replace("\\", "/")
        except ValueError:
            parts = pth.parts
            if slug in parts:
                i = parts.index(slug)
                relative_path = "/".join(parts[i+1:]) if (i + 1) < len(parts) else pth.name
            else:
                # Fallback: if source_path already looks like a project-relative path (e.g. "scenes/foo.jpg")
                # or has subdirs, keep the structure; bare name falls back to filename only.
                if len(pth.parts) > 1:
                    relative_path = str(pth).replace("\\", "/").lstrip("./")
                else:
                    relative_path = pth.name
    if not relative_path:
        relative_path = Path(source_path).name

    public_url = f"http://127.0.0.1:8900/api/project/{slug}/media/{relative_path}"

    r = _api_request("POST", "/images/edits", json={
        "model": "grok-imagine-image-quality",
        "prompt": prompt,
        "image": {"url": public_url, "type": "image_url"}
    }, timeout=60)
    if r.status_code != 200:
        return {"ok": False, "error": f"xAI edit API {r.status_code}: {r.text[:200]}"}

    j = r.json()
    data0 = j.get("data", [{}])[0]
    url = data0.get("url")
    if not url:
        return {"ok": False, "error": f"No URL in edit response: {json.dumps(j)[:200]}"}

    img = requests.get(url, timeout=60)
    if img.status_code != 200:
        return {"ok": False, "error": f"Edit download failed: {img.status_code}"}

    d = Path(output_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"edit-{int(time.time())}.jpg"
    p.write_bytes(img.content)
    return {
        "ok": True,
        "path": str(p),
        "url": data0.get("url", url),
        "width": None,
        "height": None,
        "revised_prompt": data0.get("revised_prompt", "")
    }


def generate_script(sources_text, instructions):
    """Call xAI chat completions to generate a video narration script.
    Uses the managed xAI credential. Returns (ok, script_text) tuple.
    """
    import json, requests, sys, os
    from pathlib import Path
    
    
    system_prompt = (
        "You are a video script writer. Given these sources and instructions, "
        "produce a narration script in markdown format with ## Scene N — Title (time) headers."
    )
    
    user_message = f"SOURCES:\n{sources_text}\n\nINSTRUCTIONS:\n{instructions}"
    
    body = {
        "model": "grok-3",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.7,
    }
    
    r = _api_request("POST", "/chat/completions", json=body, timeout=120)
    if r.status_code != 200:
        return False, f"xAI chat API {r.status_code}: {r.text[:300]}"
    
    result = r.json()
    script = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not script:
        return False, f"No content in response: {json.dumps(result)[:200]}"
    
    return True, script


# ── CLI ──

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--type', choices=['image','video'], default='image')
    p.add_argument('--prompt', required=True)
    p.add_argument('--output-dir', required=True)
    p.add_argument('--image', default=None)  # for image-to-video
    p.add_argument('--duration', type=int, default=8)
    p.add_argument('--aspect-ratio', default='16:9')
    p.add_argument('--resolution', default='720p')
    a = p.parse_args()
    if a.type == 'video':
        ok, r = generate_video(a.prompt, a.output_dir, a.image, a.duration, a.aspect_ratio, a.resolution)
        key = "video_path"
    else:
        ok, r = generate_image(a.prompt, a.output_dir)
        key = "image_path"
    print(json.dumps({"ok": ok, key if ok else "error": r}))
