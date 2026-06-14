#!/usr/bin/env python3
"""SSE listener for Video Studio dashboard — picks up image/video generation requests."""
import json, subprocess, sys, time, os
from pathlib import Path
from urllib.request import urlopen

DASHBOARD = "http://localhost:8900"

def notify_done(slug, scene_id, rel_path, asset_type="still"):
    """Register a generated file via the dashboard API."""
    import requests
    proj = Path(os.path.expanduser(f"~/Projects/video-studio/projects/{slug}"))
    fpath = proj / rel_path
    if not fpath.exists():
        print(f"  ✗ File not found: {fpath}")
        return False
    url = f"{DASHBOARD}/api/project/{slug}/storyboard/{scene_id}/add-version"
    with open(fpath, 'rb') as f:
        r = requests.post(url, files={'file': f}, data={'type': asset_type, 'prompt': ''})
    print(f"  Registered: {r.status_code} {r.text[:100]}")
    return r.ok

def handle_image_request(slug, scene_id):
    """Call Hermes image_generate for the given scene."""
    proj = Path(os.path.expanduser(f"~/Projects/video-studio/projects/{slug}"))
    sb_path = proj / "storyboard.json"
    if not sb_path.exists():
        print(f"  ✗ No storyboard for {slug}")
        return

    sb = json.loads(sb_path.read_text())
    scene = next((s for s in sb['scenes'] if s['id'] == scene_id), None)
    if not scene:
        print(f"  ✗ Scene {scene_id} not found")
        return

    prompt = scene.get('visual_direction', '')
    print(f"  Generating image for scene {scene_id}: {prompt[:80]}...")

    # Call hermes image_generate via CLI
    result = subprocess.run(
        ['hermes', 'image-generate', '--prompt', prompt, '--aspect-ratio', 'landscape'],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, 'HOME': os.path.expanduser('~')}
    )
    if result.returncode != 0:
        print(f"  ✗ image_generate failed: {result.stderr[:200]}")
        return

    # Parse output for image path
    for line in result.stdout.split('\n') + result.stderr.split('\n'):
        if line.startswith('MEDIA:') or '.jpg' in line or '.png' in line:
            img_path = line.strip().split('MEDIA:')[-1].strip() if 'MEDIA:' in line else line.strip()
            if os.path.exists(img_path):
                # Copy to project scenes dir
                scenes_dir = proj / "scenes"
                scenes_dir.mkdir(exist_ok=True)
                dest = scenes_dir / f"scene-{scene_id:02d}-gen.jpg"
                import shutil
                shutil.copy(img_path, dest)
                rel = f"scenes/{dest.name}"
                print(f"  ✓ Generated: {rel}")
                notify_done(slug, scene_id, rel, "still")
                return
    print(f"  ✗ Could not find image in output")

def main():
    print(f"Video Studio SSE listener — watching {DASHBOARD}/api/project/watch")
    while True:
        try:
            resp = urlopen(f"{DASHBOARD}/api/project/watch")
            print("Connected to SSE stream")
            buffer = ""
            for line in resp:
                line = line.decode('utf-8').strip()
                if not line:
                    if buffer.startswith('data: '):
                        data = json.loads(buffer[6:])
                        event = data.get('event', '')
                        if ':' in event:
                            kind, sid = event.split(':', 1)
                            if kind == 'image-requested':
                                slug = data.get('slug', 'costa-rica')
                                print(f"\n🎨 image-requested:{sid}")
                                handle_image_request(slug, int(sid))
                    buffer = ""
                else:
                    buffer += line
        except Exception as e:
            print(f"SSE connection lost: {e}. Reconnecting in 5s...")
            time.sleep(5)

if __name__ == '__main__':
    main()
