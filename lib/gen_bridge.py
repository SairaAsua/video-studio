#!/usr/bin/env python3
"""Bridge: poll generation queue, generate images via Hermes tool, register results."""
import json, time, subprocess, sys
from pathlib import Path

QUEUE = Path("/home/nicolas/Projects/video-studio/state/gen-queue.json")
WORKSPACE = Path("/home/nicolas/Projects/video-studio")

def poll():
    if not QUEUE.exists():
        return []
    try:
        return json.loads(QUEUE.read_text())
    except (json.JSONDecodeError, FileNotFoundError):
        return []

def mark_done(job_id):
    jobs = poll()
    jobs = [j for j in jobs if j.get("id") != job_id]
    QUEUE.write_text(json.dumps(jobs, indent=2))

if __name__ == "__main__":
    jobs = poll()
    if not jobs:
        print("No pending jobs")
        sys.exit(0)
    
    for job in jobs[:1]:  # Process one at a time
        print(f"Processing: scene {job['scene_id']} in {job['slug']}")
        # The agent (CompAII) does the actual generation via image_generate tool
        # This script just reports what's pending
        print(f"JOB:{json.dumps(job)}")
