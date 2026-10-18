#!/usr/bin/env python3
"""Finite local video intake; no inbox watcher and no generation call.

A receiving daimon invokes this only within the human-authorized task. Matrix
must supply its own supported, authenticated receiving adapter; this command is
not an enrollment or transport bypass.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib import request, error, parse

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--body')
    parser.add_argument('--purpose', required=True)
    parser.add_argument('--request-id', type=uuid.UUID, default=None)
    parser.add_argument('--start', action='store_true', help='Start this existing local studio if it is offline')
    parser.add_argument('--open', action='store_true', help='Open the prepared project in the local desktop browser')
    args = parser.parse_args()
    local_body = os.environ.get('VIDEO_STUDIO_BODY', socket.gethostname())
    identifier = str(args.request_id or uuid.uuid4())
    if args.body is None:
        print(json.dumps({'request_id': identifier, 'status': 'needs_body',
                          'question': '¿En qué cuerpo querés abrir la app de video?',
                          'available_local_bodies': [local_body], 'generation_submitted': False}, ensure_ascii=False))
        return 2
    if args.body != local_body:
        print(json.dumps({'request_id': identifier, 'status': 'needs_routing', 'body': args.body,
                          'local_body': local_body, 'generation_submitted': False}, ensure_ascii=False))
        return 3
    # Bind to loopback rather than accepting an arbitrary remote URL or credential.
    port = int(os.environ.get('VIDEO_STUDIO_PORT', '8900'))
    base = f'http://127.0.0.1:{port}'
    def online():
        try:
            with request.urlopen(base+'/openapi.json', timeout=2) as response:
                return json.load(response).get('info', {}).get('title') == 'Video Studio'
        except (OSError, error.URLError):
            return False
    if not online():
        if not args.start:
            print(json.dumps({'request_id': identifier, 'status': 'studio_offline', 'body': local_body,
                              'next_step': 'Repeat with --start to launch the existing studio'}))
            return 4
        state = ROOT / 'state'
        state.mkdir(exist_ok=True)
        with (state / 'studio-local.log').open('ab') as log:
            process = subprocess.Popen([str(ROOT/'start.sh')], cwd=ROOT, stdout=log, stderr=log,
                                       stdin=subprocess.DEVNULL, start_new_session=True)
        for _ in range(40):
            if online():
                break
            if process.poll() is not None:
                raise RuntimeError('The existing studio did not start; inspect its local log')
            time.sleep(.25)
        else:
            raise RuntimeError('Studio readiness not confirmed; do not start a second instance without inspection')
    body = {'request_id': identifier, 'body': args.body, 'purpose': args.purpose}
    req = request.Request(base+'/api/project/'+parse.quote(args.project, safe='')+'/studio/requests',
                          data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with request.urlopen(req, timeout=15) as response:
            result = json.load(response)
    except error.HTTPError as exc:
        detail = json.load(exc)
        print(json.dumps({'request_id': identifier, 'status': 'rejected', 'detail': detail.get('detail')}, ensure_ascii=False))
        return 5
    print(json.dumps(result, ensure_ascii=False))
    if args.open and result.get('editor_path'):
        subprocess.Popen(['xdg-open', base+result['editor_path']], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({'status': 'error', 'error_type': type(exc).__name__, 'detail': str(exc)}))
        sys.exit(1)
