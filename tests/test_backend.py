import base64
import csv
import hashlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace
from zipfile import ZipFile, ZipInfo

import pytest

import editable_wheel_data as backend
from editable_wheel_data._wheel import rewrite_editable_wheel


def configure(tmp_path, monkeypatch, text='backend = "delegate"'):
    (tmp_path / 'pyproject.toml').write_text('[tool.editable-wheel-data]\n' + text)
    monkeypatch.chdir(tmp_path)


def test_forward_hooks_and_optional_absence(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    hook = lambda *args: args
    delegate = SimpleNamespace(build_wheel=hook, build_sdist=hook)
    monkeypatch.setitem(sys.modules, 'delegate', delegate)
    assert backend.build_wheel is hook
    assert backend.build_sdist('out', {'key': 'value'}) == ('out', {'key': 'value'})
    for name in ['prepare_metadata_for_build_wheel', 'build_editable',
                 'prepare_metadata_for_build_editable', 'unknown_hook']:
        assert not hasattr(backend, name)
    delegate.build_editable = hook
    assert callable(backend.build_editable)


def test_backend_object(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, 'backend = "delegate:nested.backend"')
    hook = lambda: None
    monkeypatch.setitem(sys.modules, 'delegate', SimpleNamespace(
        nested=SimpleNamespace(backend=SimpleNamespace(build_wheel=hook))))
    assert backend.build_wheel is hook


@pytest.mark.parametrize('config', [
    'backend = "editable_wheel_data"', 'backend = "editable_wheel_data:build_editable"',
    'backend = 42', 'backend = "delegate"\nunknown = true',
    'backend = "delegate"\nfiles = []',
])
def test_invalid_configuration(tmp_path, monkeypatch, config):
    configure(tmp_path, monkeypatch, config)
    with pytest.raises(ValueError):
        getattr(backend, 'build_wheel')


@pytest.mark.parametrize('path', ['/share/a', '../a', 'C:/share', 'C:share',
                                 'share\\a', '', '//host/share'])
def test_invalid_paths(tmp_path, path):
    source = tmp_path / 'source'
    source.write_text('data')
    with pytest.raises(ValueError):
        backend._data_files(tmp_path, {'source': path})
    with pytest.raises(ValueError):
        backend._data_files(tmp_path, {path: 'share/data'})


def test_mapping_validation(tmp_path):
    (tmp_path / 'one').write_text('one')
    (tmp_path / 'two').write_text('two')
    with pytest.raises(FileNotFoundError):
        backend._data_files(tmp_path, {'missing': 'share/data'})
    for mapping in [{'one': 'share/data', 'two': 'share/DATA'},
                    {'one': 'share/data', 'two': 'share/data/nested'}]:
        with pytest.raises(ValueError):
            backend._data_files(tmp_path, mapping)


def make_wheel(path, extra=()):
    with ZipFile(path, 'w') as wheel:
        wheel.writestr('demo-1.0.dist-info/WHEEL', 'Wheel-Version: 1.0\n')
        wheel.writestr('demo-1.0.dist-info/METADATA', 'Name: demo\nVersion: 1.0\n')
        wheel.writestr('demo-1.0.dist-info/RECORD', '')
        executable = ZipInfo('demo-1.0.data/scripts/demo')
        executable.external_attr = 0o100755 << 16
        wheel.writestr(executable, '#!/bin/sh\n')
        for name, data in extra:
            wheel.writestr(name, data)


def test_wheel_record_and_modes(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'data')
    wheel = tmp_path / 'demo-1.0-py3-none-any.whl'
    make_wheel(wheel)
    rewrite_editable_wheel(wheel, [(source, 'share/demo/data')])
    with ZipFile(wheel) as archive:
        assert archive.getinfo('demo-1.0.data/scripts/demo').external_attr >> 16 == 0o100755
        rows = list(csv.reader(io.StringIO(archive.read('demo-1.0.dist-info/RECORD').decode())))
        assert {r[0] for r in rows} == set(archive.namelist())
        for name, digest, size in rows:
            if name.endswith('/RECORD'):
                assert (digest, size) == ('', '')
            else:
                payload = archive.read(name)
                expected = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b'=')
                assert digest == 'sha256=' + expected.decode()
                assert int(size) == len(payload)


@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
@pytest.mark.parametrize('extra,match', [
    ([('demo-1.0.data/data/unmapped', b'x')], 'Unmapped'),
    ([('demo-1.0.data/data/share/data', b'other')], 'differs'),
    ([('demo-1.0.dist-info/RECORD.jws', b'signature')], 'signed'),
    ([('../escape', b'x')], 'Invalid'),
    ([('demo-1.0.dist-info/WHEEL', b'duplicate')], 'Duplicate'),
])
def test_reject_wheel_without_modifying(tmp_path, extra, match):
    source = tmp_path / 'source'
    source.write_bytes(b'data')
    wheel = tmp_path / 'demo.whl'
    make_wheel(wheel, extra)
    original = wheel.read_bytes()
    with pytest.raises(ValueError, match=match):
        rewrite_editable_wheel(wheel, [(source, 'share/data')])
    assert wheel.read_bytes() == original


def test_source_symlink_escape(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.write_bytes(b'data')
    (root / 'link').symlink_to(outside)
    with pytest.raises(ValueError, match='escapes'):
        backend._data_files(root, {'link': 'share/data'})


def test_helper_name_changes_when_same_sized_mapping_changes(tmp_path):
    source = tmp_path / 'source'
    wheel = tmp_path / 'demo.whl'
    helpers = []
    payloads = []
    for data in (b'one', b'two'):
        source.write_bytes(data)
        make_wheel(wheel)
        rewrite_editable_wheel(wheel, [(source, 'share/data')])
        with ZipFile(wheel) as archive:
            helper = next(name for name in archive.namelist() if name.endswith('.pth'))
            module = helper[:-len('.pth')]
            assert archive.read(helper) == f'import {module}\n'.encode()
            helpers.append(module)
            payloads.append(archive.read(module + '.py'))
    assert len(payloads[0]) == len(payloads[1])
    assert helpers[0] != helpers[1]


@pytest.mark.parametrize('files', ['{}', '{"data" = "share/data"}', '[]'])
def test_removed_files_option_rejected(tmp_path, monkeypatch, files):
    configure(tmp_path, monkeypatch, f'backend = "delegate"\nfiles = {files}')
    with pytest.raises(ValueError, match="Unknown editable-wheel-data settings:.*files"):
        getattr(backend, 'build_editable')
