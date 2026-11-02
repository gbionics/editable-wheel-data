"""Capture the effective setuptools distribution without rerunning setup.py."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from editable_wheel_data._setuptools import build_editable, _data_mappings


def test_capture_resolved_distribution_and_restore_setup(tmp_path, monkeypatch):
    dist = SimpleNamespace(data_files=[('share/demo', ['generated/data'])])
    calls = []

    def setup(**kwargs):
        calls.append(kwargs)
        return dist

    module = SimpleNamespace(setup=setup)
    monkeypatch.setitem(sys.modules, 'setuptools', module)

    def delegate(*args):
        assert args == ('output', {'setting': 'value'}, 'metadata')
        assert module.setup(data_files='computed') is dist
        return 'demo.whl'

    result = build_editable(SimpleNamespace(build_editable=delegate), tmp_path,
                            'output', {'setting': 'value'}, 'metadata')
    assert result == ('demo.whl', [('generated/data', 'share/demo/data')])
    assert calls == [{'data_files': 'computed'}]
    assert module.setup is setup


def test_restore_setup_when_delegate_raises(tmp_path, monkeypatch):
    setup = lambda **kwargs: None
    module = SimpleNamespace(setup=setup)
    monkeypatch.setitem(sys.modules, 'setuptools', module)

    def delegate(*args):
        raise RuntimeError('build failed')

    with pytest.raises(RuntimeError, match='build failed'):
        build_editable(SimpleNamespace(build_editable=delegate), tmp_path, 'out', None, None)
    assert module.setup is setup


def test_uncaptured_distribution_fails_clearly(tmp_path, monkeypatch):
    setup = lambda **kwargs: None
    module = SimpleNamespace(setup=setup)
    monkeypatch.setitem(sys.modules, 'setuptools', module)
    with pytest.raises(RuntimeError, match='did not call setuptools.setup'):
        build_editable(SimpleNamespace(build_editable=lambda *args: 'demo.whl'),
                       tmp_path, 'out', None, None)
    assert module.setup is setup


def test_normalize_resolved_sources_and_retain_one_to_many(tmp_path):
    file = tmp_path / 'resource' / 'marker'
    file.parent.mkdir()
    file.write_text('')
    mappings = _data_mappings(tmp_path.resolve(), [
        ('share/first', [str(file), str(file)]),
        ('share/second', [Path('resource/marker')]),
        'resource/marker',
    ])
    assert mappings == [('resource/marker', 'share/first/marker'),
                        ('resource/marker', 'share/second/marker'),
                        ('resource/marker', 'marker')]


def test_absolute_source_outside_project_rejected(tmp_path):
    with pytest.raises(ValueError, match='escapes the project'):
        _data_mappings(tmp_path.resolve(), [('share', [str(tmp_path.parent / 'outside')])])
