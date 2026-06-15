"""Tests for assemble.py audio-first + video audio mixing (2026-06-15)."""
import tempfile, subprocess, os, shutil
from pathlib import Path
import pytest
sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if sys_path not in __import__('sys').path:
    __import__('sys').path.insert(0, sys_path)
from lib.assemble import _probe_duration, _strip_filter_for_audio_check


@pytest.fixture
def short_audio():
    tmp = tempfile.mkdtemp()
    path = Path(tmp) / 'test.mp3'
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono', '-t', '3', str(path)],
                   capture_output=True, text=True, timeout=10)
    yield path
    shutil.rmtree(tmp)


def test_audio_probe_returns_float(short_audio):
    dur = _probe_duration(short_audio)
    assert 2.5 < dur < 3.5


def test_audio_mix_includes_video_tracks(short_audio):
    filt = _strip_filter_for_audio_check(short_audio, 3.0, [2, 3, 4])
    parts = ','.join(filt)
    assert 'amix' in parts
    assert 'volume=0.2' in parts
    assert 'aresample=44100' in parts


def test_audio_tts_only_when_no_videos(short_audio):
    filt = _strip_filter_for_audio_check(short_audio, 3.0)
    parts = ','.join(filt)
    assert 'amix' not in parts


def test_audio_ambient_only_when_no_tts():
    tmp = tempfile.mkdtemp()
    path = Path(tmp) / 'missing.mp3'
    filt = _strip_filter_for_audio_check(path, 3.0, [2, 3])
    parts = ','.join(filt)
    assert 'amix' in parts
    assert 'anullsrc' not in parts
    shutil.rmtree(tmp)


def test_audio_mix_output_label_is_aud(short_audio):
    filt = _strip_filter_for_audio_check(short_audio, 3.0, [2])
    parts = ','.join(filt)
    assert '[aud]' in parts
