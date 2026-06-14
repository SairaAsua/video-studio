#!/usr/bin/env python3
"""Direct xAI image generation — called by dashboard, no agent loop needed."""
import argparse, json, os, sys, time, requests
from pathlib import Path

def generate_image(prompt, output_dir):
    """Call xAI Grok Imagine API and save the image. Returns (ok, path|error)."""
    hermes_home = Path(os.environ.get('HERMES_HOME', os.path.expanduser('~/.hermes')))
    sys.path.insert(0, str(hermes_home / 'hermes-agent'))

    from tools.xai_http import resolve_xai_http_credentials, hermes_xai_user_agent

    creds = resolve_xai_http_credentials()
    api_key = creds.get('api_key') or creds.get('access_token')
    if not api_key:
        return False, "No xAI credentials available — re-authenticate with: hermes auth add xai-oauth --type oauth"

    # xAI Grok Imagine endpoint
    url = "https://api.x.ai/v1/images/generations"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": hermes_xai_user_agent(),
    }
    body = {
        "model": "grok-imagine-image",
        "prompt": prompt,
        "n": 1,
        "response_format": "url",
    }
    
    r = requests.post(url, json=body, headers=headers, timeout=120)
    if r.status_code != 200:
        return False, f"xAI API error {r.status_code}: {r.text[:200]}"
    
    data = r.json()
    image_url = None
    if 'data' in data and len(data['data']) > 0:
        image_url = data['data'][0].get('url')
    
    if not image_url:
        return False, f"No image URL in response: {json.dumps(data)[:200]}"
    
    # Download image
    img_r = requests.get(image_url, timeout=60)
    if img_r.status_code != 200:
        return False, f"Failed to download image: {img_r.status_code}"
    
    # Save
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    img_path = output_dir / f"gen-{ts}.jpg"
    img_path.write_bytes(img_r.content)
    
    return True, str(img_path)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prompt', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    
    ok, result = generate_image(args.prompt, args.output_dir)
    print(json.dumps({"ok": ok, "image_path" if ok else "error": result}))
