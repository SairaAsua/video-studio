"""Actual hot PCM and Opus timestamps must survive non-destructive assembly."""
import hashlib
import json
import subprocess
from array import array

import pytest

from lib.music import render_soundtrack, true_peak


def pcm(path, filters=None):
    args=['ffmpeg','-nostdin','-v','error','-i',str(path)]
    if filters:args+=['-af',filters]
    raw=subprocess.check_output(args+['-ac','2','-ar','48000','-f','f32le','-'])
    result=array('f');result.frombytes(raw);return result


def test_true_peak_protection_preserves_waveform_clock_sources_and_cache(tmp_path):
    source=tmp_path/'hot.wav'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=997:sample_rate=48000:duration=2',
                    '-af','volume=20','-c:a','pcm_f32le',str(source)],check=True,capture_output=True)
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    parts=[{'path':'hot.wav','source_in':.1,'source_out':.6},{'path':'hot.wav','source_in':1.2,'source_out':1.7}]
    output=render_soundtrack(tmp_path,'A',parts);data=json.loads(output.with_suffix('.wav.json').read_text())
    assert data['input_true_peak_dbtp']>0 and data['gain_db']<0
    assert data['output_true_peak_dbtp']<=-2
    original=pcm(source,'atrim=start=0.1:end=0.6,asetpts=PTS-STARTPTS')
    rendered=pcm(output)
    assert len(rendered)==96000 and len(original)==48000
    ratio=10**(data['gain_db']/20)
    assert max(abs(rendered[i]-original[i]*ratio) for i in range(100,47000))<1e-5
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    mtime=output.stat().st_mtime_ns
    assert render_soundtrack(tmp_path,'A',parts)==output and output.stat().st_mtime_ns==mtime
    # A broken cache is retained and rebuilt, never mistaken for valid audio.
    output.write_bytes(b'interrupted-cache')
    assert render_soundtrack(tmp_path,'A',parts)==output
    assert list(output.parent.glob('*-unverified-*.wav'))


def test_opus_source_zero_matches_in_multi_input_edit(tmp_path):
    source=tmp_path/'source.webm'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',
        'aevalsrc=0.2*sin(2*PI*(400*t+25*t*t)):s=48000:d=3','-c:a','libopus',str(source)],check=True,capture_output=True)
    single=render_soundtrack(tmp_path,'A',[{'path':'source.webm','source_in':.75,'source_out':1.25}])
    joined=render_soundtrack(tmp_path,'B',[{'path':'source.webm','source_in':.75,'source_out':1.25},
                                         {'path':'source.webm','source_in':2,'source_out':2.5}])
    a,b=pcm(single),pcm(joined)
    assert len(a)==48000 and len(b)==96000
    assert max(abs(a[i]-b[i]) for i in range(100,47000))<1e-6


def test_digital_silence_keeps_clock_without_nonfinite_metadata(tmp_path):
    source=tmp_path/'silent.wav'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','anullsrc=r=48000:cl=stereo',
                    '-t','0.5',str(source)],check=True,capture_output=True)
    output=render_soundtrack(tmp_path,'A',[{'path':'silent.wav','source_in':0,'source_out':.5}])
    data=json.loads(output.with_suffix('.wav.json').read_text())
    assert data['gain_db']==0 and data['input_true_peak_dbtp'] is None
    assert true_peak(output) is None and len(pcm(output))==48000
