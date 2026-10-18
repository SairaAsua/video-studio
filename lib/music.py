"""Non-destructive soundtrack assembly from measured source cuts."""
from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path


def render_soundtrack(project: Path, film: str, segments: list[dict]) -> Path:
    if film not in {'A', 'B'} or not segments:
        raise ValueError('Choose a film and at least one measured audio segment')
    files = []
    for segment in segments:
        path = (project / segment['path']).resolve()
        if not path.is_relative_to(project.resolve()) or not path.is_file():
            raise ValueError('Audio source must remain in the project')
        if not 0 <= segment['source_in'] < segment['source_out']:
            raise ValueError('Invalid audio source interval')
        files.append(path)
    identity = json.dumps({'segments': segments, 'sources': [
        {'path': str(p.relative_to(project.resolve())), 'size': p.stat().st_size,
         'mtime_ns': p.stat().st_mtime_ns} for p in files
    ]}, sort_keys=True)
    digest = hashlib.sha256(identity.encode()).hexdigest()[:20]
    target = project / 'audio' / f'{film}-edit-{digest}.wav'
    if target.exists():
        return target
    target.parent.mkdir(exist_ok=True)
    temporary = target.with_name(f'.{target.stem}-{uuid.uuid4().hex}.wav')
    args = ['ffmpeg', '-nostdin', '-v', 'error']
    for p in files:
        args += ['-i', str(p)]
    filters = []
    for i, s in enumerate(segments):
        filters.append(f"[{i}:a]atrim=start={float(s['source_in'])}:end={float(s['source_out'])},"
                       f'asetpts=PTS-STARTPTS,aresample=48000,'
                       f'aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]')
    labels = ''.join(f'[a{i}]' for i in range(len(segments)))
    filters.append(f'{labels}concat=n={len(segments)}:v=0:a=1[out]')
    args += ['-filter_complex', ';'.join(filters), '-map', '[out]', '-c:a', 'pcm_s16le', str(temporary)]
    try:
        result = subprocess.run(args, capture_output=True, timeout=300)
        if result.returncode:
            raise RuntimeError('Audio assembly failed; source format and cuts need inspection')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
