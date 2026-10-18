"""Actual silent-source cut: music mode must ignore legacy narration length."""
import json
import subprocess

import pytest

from lib.assemble import render_scene


def test_music_cut_keeps_duration_and_source_in_without_source_audio(tmp_path):
    paths = {k: tmp_path / k for k in ('scenes', 'audio', 'renders', 'exports')}
    for path in paths.values():
        path.mkdir()
    source = paths['scenes'] / 'colors.mp4'
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=red:s=160x90:r=30:d=1',
                    '-f', 'lavfi', '-i', 'color=blue:s=160x90:r=30:d=1',
                    '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)],
                   check=True, capture_output=True, timeout=20)
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'sine=frequency=500:duration=4',
                    str(paths['audio'] / 'scene-01.mp3')], check=True, capture_output=True, timeout=15)
    scene = {'id': 1, 'audio_mode': 'music', 'edit_duration_secs': .75,
             'asset_timeline': [{'type': 'video', 'path': 'scenes/colors.mp4',
                                 'duration_secs': .75, 'source_in': 1, 'time_in': 0}]}
    rendered = render_scene(scene, paths, width=160, height=90, x264_preset='ultrafast')
    data = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                               'format=duration', '-of', 'json', str(rendered)]))
    assert float(data['format']['duration']) == pytest.approx(.75, abs=.06)
    frame = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(rendered), '-frames:v', '1',
                                     '-vf', 'scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    assert frame[2] > frame[0] + 100  # the blue half, not the red source beginning
