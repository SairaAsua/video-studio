"""Non-destructive soundtrack assembly from measured source cuts."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import re
import subprocess
import uuid
from pathlib import Path

TRUE_PEAK_TARGET_DBTP = -2.0


def file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def true_peak(path: Path) -> float | None:
    """Measure decoded floating-point true peak; None denotes digital silence."""
    result = subprocess.run(['ffmpeg','-nostdin','-hide_banner','-v','info','-i',str(path),
        '-vn','-af','ebur128=peak=true:framelog=verbose','-f','null','-'],
        capture_output=True,text=True,timeout=300,check=True)
    matches = re.findall(r'True peak:\s*Peak:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*dBFS',result.stderr)
    if not matches:
        raise RuntimeError('Audio peak measurement unavailable')
    value = float(matches[-1])
    if value == -math.inf:
        return None
    if not math.isfinite(value):
        raise RuntimeError('Invalid audio peak measurement')
    return value


def render_soundtrack(project: Path, film: str, segments: list[dict]) -> Path:
    if film not in {'A', 'B'} or not segments:
        raise ValueError('Choose a film and at least one measured audio segment')
    files = []; sources = {}
    for segment in segments:
        path = (project / segment['path']).resolve()
        if not path.is_relative_to(project.resolve()) or not path.is_file():
            raise ValueError('Audio source must remain in the project')
        a,b = segment['source_in'],segment['source_out']
        if not math.isfinite(a+b) or not 0 <= a < b:
            raise ValueError('Invalid audio source interval')
        files.append(path)
        relative = str(path.relative_to(project.resolve()))
        if relative not in sources:
            sources[relative] = file_digest(path)
    identity = {'segments':segments,'sources':sources,'renderer_sha256':file_digest(Path(__file__)),
                'target_true_peak_dbtp':TRUE_PEAK_TARGET_DBTP,'sample_rate':48000,
                'clock':'decoded-audio-zero','sample_format':'pcm_s24le'}
    digest = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:20]
    target = project/'audio'/f'{film}-edit-{digest}.wav'
    target.parent.mkdir(exist_ok=True)
    metadata_path = target.with_suffix('.wav.json')
    with target.with_suffix('.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if target.exists() and metadata_path.exists():
            try:
                metadata=json.loads(metadata_path.read_text())
                if metadata['output_sha256']==file_digest(target):
                    return target
            except (ValueError,KeyError):
                pass
        if target.exists():
            retained=target.with_name(target.stem+'-unverified-'+uuid.uuid4().hex+'.wav')
            target.rename(retained)
            if metadata_path.exists():
                metadata_path.rename(retained.with_suffix('.wav.json'))
        temporary=target.with_name(f'.{target.stem}-{uuid.uuid4().hex}.wav')
        floating=target.with_name(f'.{target.stem}-float-{uuid.uuid4().hex}.wav')
        temp_metadata=metadata_path.with_name('.'+metadata_path.name+'-'+uuid.uuid4().hex)
        args=['ffmpeg','-nostdin','-v','error']
        for p in files:
            args+=['-i',str(p)]
        filters=[]
        for i,s in enumerate(segments):
            # Normalize each decoded clock before trimming. In particular, Opus
            # pre-skip/start timestamps must not shift cuts in a multi-input job.
            filters.append(f'[{i}:a]asetpts=PTS-STARTPTS,'
                f"atrim=start={float(s['source_in'])}:end={float(s['source_out'])},"
                'asetpts=PTS-STARTPTS,aresample=48000,'
                f'aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]')
        labels=''.join(f'[a{i}]' for i in range(len(segments)))
        filters.append(f'{labels}concat=n={len(segments)}:v=0:a=1[out]')
        args+=['-filter_complex',';'.join(filters),'-map','[out]','-c:a','pcm_f32le',str(floating)]
        try:
            subprocess.run(args,capture_output=True,timeout=300,check=True)
            peak=true_peak(floating)
            # Apply one constant attenuation across the complete edit. Preserve
            # musical dynamics, speed and relative track levels; never boost.
            gain=0.0 if peak is None else min(0.0,TRUE_PEAK_TARGET_DBTP-peak-.1)
            subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(floating),
                '-af',f'volume={gain:.6f}dB','-c:a','pcm_s24le',str(temporary)],
                capture_output=True,timeout=300,check=True)
            output_peak=true_peak(temporary)
            if output_peak is not None and output_peak>TRUE_PEAK_TARGET_DBTP+.05:
                raise RuntimeError('Audio output did not retain the requested headroom')
            for relative,stamp in sources.items():
                if file_digest(project/relative)!=stamp:
                    raise RuntimeError('Audio source changed during assembly')
            metadata={'schema':'video-studio.soundtrack-render/v1','identity':identity,
                      'input_true_peak_dbtp':peak,'gain_db':gain,'output_true_peak_dbtp':output_peak,
                      'output_sha256':file_digest(temporary),'clock_preserved':True,
                      'review_status':'technical_preview_needs_listening'}
            temp_metadata.write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
            temporary.replace(target);temp_metadata.replace(metadata_path)
        finally:
            temporary.unlink(missing_ok=True);floating.unlink(missing_ok=True);temp_metadata.unlink(missing_ok=True)
    return target
