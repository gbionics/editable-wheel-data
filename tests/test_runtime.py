import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from editable_wheel_data import _runtime


def runtime(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.write_bytes(b'data')
    target = tmp_path / 'share' / 'demo' / 'data'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'data')
    entries = [Path('share/demo/data')]
    dist = SimpleNamespace(files=entries, locate_file=lambda entry: tmp_path / entry)
    monkeypatch.setattr(_runtime, 'PathDistribution', lambda path: dist)
    mapping = [('share/demo/data', str(source), hashlib.sha256(b'data').hexdigest())]
    return source, target, entries, mapping


def test_activation_is_idempotent_and_live(tmp_path, monkeypatch):
    source, target, _, mapping = runtime(tmp_path, monkeypatch)
    _runtime.install_links('demo.dist-info', mapping)
    assert target.is_symlink()
    source.write_bytes(b'edited')
    _runtime.install_links('demo.dist-info', mapping)
    assert target.read_bytes() == b'edited'


@pytest.mark.parametrize('failure', ['modified', 'missing', 'unrelated', 'ambiguous', 'privileges'])
def test_runtime_errors(tmp_path, monkeypatch, failure):
    source, target, entries, mapping = runtime(tmp_path, monkeypatch)
    if failure == 'modified':
        target.write_bytes(b'user edit')
    elif failure == 'missing':
        source.unlink()
    elif failure == 'unrelated':
        other = tmp_path / 'other'
        other.write_bytes(b'data')
        target.unlink()
        target.symlink_to(other)
    elif failure == 'ambiguous':
        entries.append(Path('another/share/demo/data'))
    else:
        def denied(*args, **kwargs):
            raise OSError('privileges')
        monkeypatch.setattr(Path, 'symlink_to', denied)
    with pytest.raises(RuntimeError):
        _runtime.install_links('demo.dist-info', mapping)
    assert not list(target.parent.glob('*.tmp'))
    if failure == 'modified':
        assert target.read_bytes() == b'user edit'
