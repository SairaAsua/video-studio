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
