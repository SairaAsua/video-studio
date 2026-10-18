"""Saved body choices must not leak into another project request."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('workbench_launcher', ROOT / 'scripts/open_workbench.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_saved_choice_only_applies_to_selected_project(tmp_path, monkeypatch):
    config = tmp_path / 'config' / 'video-studio'
    config.mkdir(parents=True)
    (config / 'launcher.json').write_text(json.dumps({'default_project': 'one', 'body': 'local-body'}))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(config.parent))
    monkeypatch.setattr(launcher, 'ROOT', tmp_path)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(launcher.subprocess, 'run', run)
    assert launcher.main([]) == 0
    assert calls[-1][calls[-1].index('--project')+1] == 'one'
    assert calls[-1][calls[-1].index('--body')+1] == 'local-body'
    assert '--open' in calls[-1]
    assert launcher.main(['--project', 'two', '--no-open']) == 0
    assert '--body' not in calls[-1]
    assert '--open' not in calls[-1]
    assert launcher.main(['--project', 'two', '--body', 'chosen']) == 0
    assert calls[-1][calls[-1].index('--body')+1] == 'chosen'


def test_unconfigured_launcher_asks_for_project(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    def unexpected(*args, **kwargs):
        raise AssertionError('must not start a server without a selected project')
    monkeypatch.setattr(launcher.subprocess, 'run', unexpected)
    assert launcher.main([]) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'needs_project'
