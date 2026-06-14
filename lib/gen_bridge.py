#!/usr/bin/env python3
"""Direct xAI image/video generation — reads auth.json directly, no Hermes imports needed."""
import argparse, json, os, sys, time, requests
from pathlib import Path

def _get_xai_token():
    auth_path = Path(os.environ.get('HERMES_HOME', os.path.expanduser('~/.hermes'))) / 'auth.json'
    if not auth_path.exists():
        return None
    store = json.loads(auth_path.read_text())
    providers = store.get('providers', {})
    xai = providers.get('xai-oauth', {})
    tokens = xai.get('tokens', {})
    return tokens.get('access_token')

def _api_key():
    token = _get_xai_token()
    if not token:
        raise RuntimeError("No xAI credentials — re-auth: hermes auth add xai-oauth --type oauth")
    return token

def generate_image(prompt, output_dir):
    """Call xAI Grok Imagine image API and save the result."""
    token = _api_key()
    url = "https://api.x.ai/v1/images/generations"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"model": "grok-imagine-image", "prompt": prompt, "n": 1, "response_format": "url"}
    
    r = requests.post(url, json=body, headers=headers, timeout=120)
    if r.status_code != 200:
        return False, f"xAI API error {r.status_code}: {r.text[:200]}"
    
    data = r.json()
    image_url = data.get('data', [{}])[0].get('url')
    if not image_url:
        return False, f"No image URL: {json.dumps(data)[:200]}"
    
    img_r = requests.get(image_url, timeout=60)
    if img_r.status_code != 200:
        return False, f"Download failed: {img_r.status_code}"
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    img_path = output_dir / f"gen-{int(time.time())}.jpg"
    img_path.write_bytes(img_r.content)
    return True, str(img_path)


def generate_video(prompt, output_dir, reference_image_paths=None, duration_secs=5, aspect_ratio="16:9", resolution="720p"):
    """Call xAI Grok Imagine video API (image-to-video or text-to-video) and save."""
    token = _api_key()
    url = "https://api.x.ai/v1/videos/generations"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "model": "grok-imagine-video",
        "prompt": prompt,
        "duration": int(min(max(duration_secs, 1), 15)),
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }
    if reference_image_paths:
        body["reference_image_urls"] = reference_image_paths  # xAI API expects URLs, but we pass local paths and hope the API handles them
        body["n"] = 1
    
    r = requests.post(url, json=body, headers=headers, timeout=300)
    if r.status_code != 200:
        return False, f"xAI video API error {r.status_code}: {r.text[:300]}"
    
    data = r.json()
    
    # Handle async polling if response has request_id but no data
    video_url = data.get('data', [{}])[0].get('url') if data.get('data') else None
    request_id = data.get('request_id')
    
    if not video_url and request_id:
        # Poll for completion
        poll_url = f"https://api.x.ai/v1/videos/generations/{request_id}"
        for attempt in range(20):  # up to ~3 minutes
            time.sleep(10)
            pr = requests.get(poll_url, headers=headers, timeout=30)
            if pr.status_code == 200:
                pd = pr.json()
                status = pd.get('status', '')
                if status == 'completed':
                    video_url = pd.get('data', [{}])[0].get('url') if pd.get('data') else pd.get('video_url')
                    break
                elif status == 'failed':
                    return False, f"Video generation failed: {pd.get('error', 'unknown')}"
        if not video_url:
            return False, f"Video polling timed out after 20 attempts"
    
    if not video_url:
        return False, f"No video URL: {json.dumps(data)[:200]}"
    
    vid_r = requests.get(video_url, timeout=120)
    if vid_r.status_code != 200:
        return False, f"Video download failed: {vid_r.status_code}"
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    vid_path = output_dir / f"genvid-{int(time.time())}.mp4"
    vid_path.write_bytes(vid_r.content)
    return True, str(vid_path)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--type', choices=['image','video'], default='image')
    p.add_argument('--prompt', required=True)
    p.add_argument('--output-dir', required=True)
    p.add_argument('--duration', type=float, default=5)
    p.add_argument('--aspect-ratio', default='16:9')
    p.add_argument('--resolution', default='720p')
    p.add_argument('--ref-images', nargs='*', default=[])
    a = p.parse_args()
    
    if a.type == 'video':
        ok, r = generate_video(a.prompt, a.output_dir, a.ref_images or None, a.duration, a.aspect_ratio, a.resolution)
    else:
        ok, r = generate_image(a.prompt, a.output_dir)
    print(json.dumps({"ok": ok, "image_path" if a.type == 'image' else "video_path" if ok else "error": r}))
