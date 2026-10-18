"""Real audio concatenation preserves chosen intervals and source files."""
import hashlib
import json
import subprocess

import pytest

from lib.music import render_soundtrack


def test_edited_audio_uses_measured_cuts_without_replacing_sources(tmp_path):
    source = tmp_path / 'song.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3',
                    str(source)], check=True, capture_output=True, timeout=15)
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    cuts = [{'path': 'song.wav', 'source_in': .5, 'source_out': 1.25},
            {'path': 'song.wav', 'source_in': 2, 'source_out': 2.5}]
    out = render_soundtrack(tmp_path, 'A', cuts)
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                                'format=duration', '-of', 'json', str(out)]))
    assert float(probe['format']['duration']) == pytest.approx(1.25, abs=.01)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    assert render_soundtrack(tmp_path, 'A', cuts) == out
    assert not list(out.parent.glob('.*.wav'))
