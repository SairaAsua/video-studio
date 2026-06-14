#!/usr/bin/env python3
"""Direct xAI image generation — reads auth.json directly, no Hermes imports needed."""
import argparse, json, os, sys, time, requests
from pathlib import Path

def _get_xai_token():
    """Read xAI OAuth token from Hermes auth.json."""
    auth_path = Path(os.environ.get('HERMES_HOME', os.path.expanduser('~/.hermes'))) / 'auth.json'
    if not auth_path.exists():
        return None
    store = json.loads(auth_path.read_text())
    providers = store.get('providers', {})
    xai = providers.get('xai-oauth', {})
    tokens = xai.get('tokens', {})
    return tokens.get('access_token')

def generate_image(prompt, output_dir):
    """Call xAI Grok Imagine API and save the image."""
    token = _get_xai_token()
    if not token:
        return False, "No xAI credentials — re-auth: hermes auth add xai-oauth --type oauth"

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

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--prompt', required=True)
    p.add_argument('--output-dir', required=True)
    a = p.parse_args()
    ok, r = generate_image(a.prompt, a.output_dir)
    print(json.dumps({"ok": ok, "image_path" if ok else "error": r}))
