"""Recoverable per-film music-led exports over the existing scene compositor."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from lib.assemble import render_scene
from lib.music import render_soundtrack

FPS = 30


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def atomic(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}-{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def media_file(project: Path, relative: str) -> Path:
    path = (project / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(project.resolve()) or not path.is_file():
        raise ValueError('A selected media source is missing or outside this project')
    return path


def inspect(path: Path) -> dict:
    result = subprocess.run(['ffprobe','-v','error','-show_entries',
        'format=duration:stream=codec_type,width,height,r_frame_rate,nb_frames,duration',
        '-of','json',str(path)],capture_output=True,text=True,timeout=20,check=True)
    return json.loads(result.stdout)


def plan_export(project: Path, board: dict, film: str, *, width=1280, height=720, crf=23, preset='veryfast') -> dict:
    if film not in {'A','B'}:
        raise ValueError('Choose film A or B')
    if width < 128 or height < 128 or width % 2 or height % 2:
        raise ValueError('Export dimensions must be positive even pixel values')
    if not 0 <= crf <= 51 or preset not in {'ultrafast','veryfast','medium','slow'}:
        raise ValueError('Unsupported export quality')
    shots = [s for s in board.get('scenes',[]) if s.get('video') == film and s.get('included',True)]
    segments = board.get('soundtracks',{}).get(film,[])
    if not shots or not segments:
        raise ValueError('Choose shots and save measured soundtrack edits first')
    sources = {}; cuts = []; elapsed = 0.; previous_frame = 0
    def source(relative):
        path = media_file(project,relative)
        if relative not in sources:
            sources[relative] = {'sha256':digest_file(path)}
        return path
    for s in shots:
        duration = s.get('edit_duration_secs')
        if not duration or not math.isfinite(duration) or duration <= 0 or not s.get('selected_asset'):
            raise ValueError(f"Shot {s.get('shot_id',s['id'])} needs a selected take and measured duration")
        path = source(s['selected_asset']); start = float(s.get('source_trim_in',0))
        if start < 0 or not math.isfinite(start):
            raise ValueError('Invalid selected clip source interval')
        kind = 'video' if path.suffix.lower() in {'.mp4','.webm','.mov'} else 'still'
        if kind == 'video':
            info = inspect(path)
            if not any(v.get('codec_type')=='video' for v in info.get('streams',[])):
                raise ValueError('A selected take has no picture stream')
            if start+duration > float(info['format']['duration'])+1/FPS:
                raise ValueError('A selected cut exceeds its source video')
        elapsed += duration
        end_frame = round(elapsed * FPS)
        frames = end_frame-previous_frame
        if frames < 1:
            raise ValueError('Each picture cut must contain at least one output frame')
        cuts.append({'scene_id':s['id'],'shot_id':s.get('shot_id',str(s['id'])),
            'path':s['selected_asset'],'type':kind,'source_in':start,
            'frame_in':previous_frame,'frames':frames,'duration_secs':frames/FPS,
            'reference_versions':copy.deepcopy(s.get('reference_versions',{}))})
        previous_frame = end_frame
    music_duration = 0.
    for segment in segments:
        path = source(segment['path']); info = inspect(path)
        a,b = float(segment['source_in']),float(segment['source_out'])
        if not math.isfinite(a+b) or not 0 <= a < b <= float(info['format']['duration'])+.001:
            raise ValueError('A soundtrack cut exceeds its source or has invalid times')
        if not any(v.get('codec_type')=='audio' for v in info.get('streams',[])):
            raise ValueError('A soundtrack source has no audio stream')
        music_duration += b-a
    if abs(previous_frame/FPS-music_duration) > 1/FPS+.001:
        raise ValueError(f'Picture covers {previous_frame/FPS:.3f}s but music covers {music_duration:.3f}s; align the edit before exporting')
    payload = {'schema':'video-studio.film-export/v1','film':film,'width':width,'height':height,
        'crf':crf,'preset':preset,'fps':FPS,'cuts':cuts,'soundtrack':copy.deepcopy(segments),
        'sources':sources,'music_duration_secs':music_duration,
        'storyboard_sha256':hashlib.sha256(json.dumps(board,sort_keys=True).encode()).hexdigest(),
        'renderer_sha256':digest_file(Path(__file__).with_name('assemble.py')),
        'review_status':'technical_preview_needs_human_playback'}
    identifier = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:24]
    return {**payload,'job_id':identifier}


def job_path(project: Path, identifier: str) -> Path:
    if len(identifier)!=24 or any(c not in '0123456789abcdef' for c in identifier):
        raise ValueError('Invalid export job ID')
    return project/'exports'/'.jobs'/(identifier+'.json')


def prepare_export(project: Path, plan: dict) -> dict:
    path = job_path(project,plan['job_id'])
    if path.exists():
        saved=json.loads(path.read_text())
        if saved['plan'] != plan:
            raise ValueError('Export job content mismatch')
        return saved
    record={'job_id':plan['job_id'],'status':'prepared','plan':plan,'completed_shots':[],
            'created_at':datetime.now(timezone.utc).isoformat()}
    atomic(path,record)
    return record


def export_status(project: Path, identifier: str) -> dict:
    path=job_path(project,identifier)
    if not path.exists():raise ValueError('Export job not found')
    record=json.loads(path.read_text())
    lock_path=path.with_suffix('.lock')
    active=False
    with lock_path.open('a+b') as handle:
        try:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            active=True
        else:
            fcntl.flock(handle,fcntl.LOCK_UN)
    if record['status']=='running' and not active:
        record['status']='interrupted'
    if record['status']=='completed' and not (project/record.get('output','')).is_file():
        record['status']='interrupted'
    return {**record,'active_lock':active}


def run_export(project: Path, identifier: str) -> dict:
    path=job_path(project,identifier)
    with path.with_suffix('.lock').open('a+b') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return export_status(project,identifier)
        record=json.loads(path.read_text());plan=record['plan']
        if record['status']=='completed' and record.get('output') and (project/record['output']).is_file():
            if digest_file(project/record['output'])==record.get('output_sha256'):
                return record
        work=project/'renders'/('film-'+plan['film'])/identifier;work.mkdir(parents=True,exist_ok=True)
        record.update(status='running',pid=os.getpid(),error=None)
        atomic(path,record)
        try:
            for relative,stamp in plan['sources'].items():
                if digest_file(media_file(project,relative))!=stamp['sha256']:
                    raise ValueError('An export source changed; create a new export plan')
            paths={'scenes':project/'scenes','audio':project/'audio','renders':work,'exports':project/'exports'}
            rendered=[]
            for cut in plan['cuts']:
                output=work/f"scene-{cut['scene_id']:02d}.mp4"
                valid_cache = False
                if output.exists():
                    try:
                        valid_cache = abs(float(inspect(output)['format']['duration'])-cut['duration_secs'])<=1/FPS+.01
                    except (subprocess.SubprocessError, ValueError, KeyError):
                        pass
                if not valid_cache:
                    if output.exists():
                        output.rename(output.with_name(output.stem+'-interrupted-'+uuid.uuid4().hex+'.mp4'))
                    scene={'id':cut['scene_id'],'audio_mode':'music','edit_duration_secs':cut['duration_secs'],
                        'asset_timeline':[{'path':cut['path'],'type':cut['type'],'duration_secs':cut['duration_secs'],
                                          'source_in':cut['source_in'],'time_in':0,'layer':0}]}
                    render_scene(scene,paths,width=plan['width'],height=plan['height'],crf=plan['crf'],x264_preset=plan['preset'])
                rendered.append(output)
                record['completed_shots']=[c['shot_id'] for c in plan['cuts'][:len(rendered)]]
                atomic(path,record)
            for relative, stamp in plan['sources'].items():
                if digest_file(media_file(project, relative)) != stamp['sha256']:
                    raise ValueError('An export source changed while rendering')
            soundtrack=render_soundtrack(project,plan['film'],plan['soundtrack'])
            # Relative, numeric filenames avoid concat-demuxer quoting ambiguities.
            listing=work/'cuts.txt';listing.write_text(''.join(f"file '{p.name}'\n" for p in rendered))
            picture=work/'picture.mp4'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-f','concat','-safe','0','-i',str(listing),
                '-map','0:v:0','-an','-c:v','copy',str(picture)],capture_output=True,timeout=300,check=True)
            target=project/'exports'/f"{plan['film']}-review-{identifier}.mp4"
            temporary=target.with_name('.'+target.stem+'-'+uuid.uuid4().hex+'.mp4')
            try:
                subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(picture),'-i',str(soundtrack),
                    '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k',
                    '-t',str(plan['music_duration_secs']),'-movflags','+faststart',str(temporary)],
                    capture_output=True,timeout=300,check=True)
                output_info=inspect(temporary)
                if abs(float(output_info['format']['duration'])-plan['music_duration_secs'])>1/FPS+.025:
                    raise RuntimeError('Export duration did not preserve its musical clock')
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            record.update(status='completed',output=str(target.relative_to(project)),
                          output_sha256=digest_file(target),probe=output_info,finished_at=datetime.now(timezone.utc).isoformat())
            atomic(path,record)
            atomic(target.with_suffix('.json'),record)
        except Exception as exc:
            record.update(status='failed',error='Export interrupted or failed; inspect the source cuts and resume this job.',error_type=type(exc).__name__)
            atomic(path,record)
        return record
