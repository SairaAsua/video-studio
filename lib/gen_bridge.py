#!/usr/bin/env python3
"""Direct xAI image/video generation — per official docs at docs.x.ai."""
import argparse, json, os, sys, time, requests
from pathlib import Path

BASE = "https://api.x.ai/v1"

def _token():
    auth_path = Path(os.environ.get('HERMES_HOME', os.path.expanduser('~/.hermes'))) / 'auth.json'
    auth = auth_path.read_text()
    store = json.loads(auth)
    return store['providers']['xai-oauth']['tokens']['access_token']

def _headers():
    return {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}

# ── Image ──

def generate_image(prompt, output_dir):
    """POST /v1/images/generations → download → save."""
    r = requests.post(f"{BASE}/images/generations", json={
        "model": "grok-imagine-image", "prompt": prompt, "n": 1, "response_format": "url"
    }, headers=_headers(), timeout=120)
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

def generate_video(prompt, output_dir, image_path=None, duration=8, aspect_ratio="16:9", resolution="720p"):
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
        # Read image as base64 data URL
        import base64
        img_data = Path(image_path).read_bytes()
        b64 = base64.b64encode(img_data).decode()
        ext = Path(image_path).suffix.lower().lstrip('.')
        mime = {'jpg': 'jpeg', 'jpeg': 'jpeg', 'png': 'png', 'webp': 'webp'}.get(ext, 'jpeg')
        body["image"] = f"data:image/{mime};base64,{b64}"

    r = requests.post(f"{BASE}/videos/generations", json=body, headers=_headers(), timeout=30)
    if r.status_code != 200:
        return False, f"xAI video API {r.status_code}: {r.text[:300]}"

    rid = r.json().get("request_id")
    if not rid:
        return False, f"No request_id: {json.dumps(r.json())[:200]}"

    # Poll GET /v1/videos/{request_id}
    for attempt in range(45):  # up to ~7.5 min
        time.sleep(10)
        pr = requests.get(f"{BASE}/videos/{rid}", headers=_headers(), timeout=30)
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

    r = requests.post(f"{BASE}/videos/extensions", json=body, headers=_headers(), timeout=30)
    if r.status_code != 200:
        return False, f"xAI video extend API {r.status_code}: {r.text[:300]}"

    rid = r.json().get("request_id")
    if not rid:
        return False, f"No request_id: {json.dumps(r.json())[:200]}"

    # Poll GET /v1/videos/{request_id} — exact log format required
    for attempt in range(45):  # up to ~7.5 min
        time.sleep(10)
        pr = requests.get(f"{BASE}/videos/{rid}", headers=_headers(), timeout=30)
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

    r = requests.post(f"{BASE}/videos/edits", json=body, headers=_headers(), timeout=30)
    if r.status_code != 200:
        return False, f"xAI video API {r.status_code}: {r.text[:300]}"

    rid = r.json().get("request_id")
    if not rid:
        return False, f"No request_id: {json.dumps(r.json())[:200]}"

    # Poll GET /v1/videos/{request_id}
    for attempt in range(45):  # up to ~7.5 min
        time.sleep(10)
        pr = requests.get(f"{BASE}/videos/{rid}", headers=_headers(), timeout=30)
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

    r = requests.post(f"{BASE}/images/edits", json={
        "model": "grok-imagine-image-quality",
        "prompt": prompt,
        "image": [{"url": public_url, "type": "image_url"}]
    }, headers=_headers(), timeout=60)
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
