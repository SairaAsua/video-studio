#!/usr/bin/env python3
"""Open this existing studio using an explicit or per-project saved body choice."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project')
    parser.add_argument('--body')
    parser.add_argument('--purpose', default='Open the video production workbench')
    parser.add_argument('--request-id')
    parser.add_argument('--no-open', action='store_true', help='Prepare without opening the desktop browser')
    args = parser.parse_args(argv)
    config_base = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    config_file = config_base / 'video-studio' / 'launcher.json'
    config = json.loads(config_file.read_text()) if config_file.exists() else {}
    project = args.project or config.get('default_project')
    if not project:
        print(json.dumps({'status': 'needs_project', 'question': 'Which existing project should be opened?'}))
        return 2
    # Saved body choices belong to one project, never to every future request.
    body = args.body
    if body is None and project == config.get('default_project'):
        body = config.get('body')
    command = [sys.executable, str(ROOT / 'scripts' / 'request_video.py'),
               '--project', project, '--purpose', args.purpose, '--start']
    if body:
        command += ['--body', body]
    if args.request_id:
        command += ['--request-id', args.request_id]
    if not args.no_open:
        command.append('--open')
    # Serialize finite launches. This is not a daemon, updater or inbox watcher.
    state = ROOT / 'state'
    state.mkdir(exist_ok=True)
    with (state / '.manual-launch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == '__main__':
    sys.exit(main())
