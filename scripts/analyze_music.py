#!/usr/bin/env python3
"""Local-only, resumable source transcription for edit planning.

Output is analysis, not an accepted lyric/timecode edit. Never change sources.
Use only a cached model directory; no download and no external STT calls.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('OMP_NUM_THREADS', '2')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--model',type=Path,required=True)
    args=parser.parse_args()
    if not (args.model/'model.bin').is_file():
        raise SystemExit('A complete cached Whisper model is required')
    from faster_whisper import WhisperModel
    from app.workbench import atomic_json
    project=args.project.resolve();board=json.loads((project/'storyboard.json').read_text())
    output=project/'audio-analysis';output.mkdir(exist_ok=True)
    model=None
    for source in board['music_sources']:
        path=(project/source['path']).resolve()
        if not path.is_relative_to(project):raise SystemExit('Source outside project')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        if digest!=source['sha256']:raise SystemExit('Source changed; rescan required')
        target=output/(digest[:16]+'.json')
        if target.exists():
            previous=json.loads(target.read_text())
            if previous.get('complete') and previous.get('source_sha256')==digest:
                print(json.dumps({'source':source['path'],'status':'reused_analysis'}),flush=True)
                continue
        if model is None:
            model=WhisperModel(str(args.model),device='cpu',compute_type='int8',cpu_threads=2,num_workers=1,local_files_only=True)
        segments,info=model.transcribe(str(path),language='es',beam_size=5,word_timestamps=True,
                                      condition_on_previous_text=False,vad_filter=False)
        report={'schema':'video-studio.local-audio-analysis/v1','source_path':source['path'],
                'source_sha256':digest,'duration_secs':source['duration_secs'],'model':'faster-whisper-small-int8-cpu',
                'language':info.language,'complete':False,'review_status':'machine_transcription_needs_listening',
                'segments':[]}
        for segment in segments:
            report['segments'].append({'start':segment.start,'end':segment.end,'text':segment.text,
                 'avg_logprob':segment.avg_logprob,'no_speech_prob':segment.no_speech_prob,
                 'words':[{'start':w.start,'end':w.end,'word':w.word,'probability':w.probability} for w in segment.words or []]})
            atomic_json(target,report)
            if len(report['segments'])%10==0:
                print(json.dumps({'source':source['path'],'segments':len(report['segments']),'processed_seconds':round(segment.end,1)}),flush=True)
        report['complete']=True;atomic_json(target,report)
        print(json.dumps({'source':source['path'],'status':'analysis_saved','segments':len(report['segments'])}),flush=True)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
    main()
