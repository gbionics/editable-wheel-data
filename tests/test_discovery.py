"""Mapping discovery and validation across backend formats."""
import pytest

import editable_wheel_data as backend


def discover(root, backend_name, table=''):
    text = f'[tool.editable-wheel-data]\nbackend = "{backend_name}"\n'
    (root / 'pyproject.toml').write_text(text + table)
    config = {'backend': backend_name}
    return {destination: source for source, destination in backend._data_files(root, backend._discovered_mappings(root, config))}


def source(root, name, data='data'):
    file = root / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(data)
    return file.resolve()


def test_hatchling_recursive_directories_and_renamed_file(tmp_path):
    robot = source(tmp_path, 'resources/nested/robot with spaces.urdf')
    marker = source(tmp_path, 'marker', '')
    result = discover(tmp_path, 'hatchling.build', '''
[tool.hatch.build.targets.wheel.shared-data]
"resources" = "share/demo"
"marker" = "share/ament_index/resource_index/packages/demo"
''')
    assert result == {'share/demo/nested/robot with spaces.urdf': robot,
                      'share/ament_index/resource_index/packages/demo': marker}


@pytest.mark.parametrize('delegate', ['setuptools.build_meta', 'hatchling.build', 'other.backend'])
def test_missing_backend_table_has_no_data(tmp_path, delegate):
    assert discover(tmp_path, delegate) == {}


@pytest.mark.parametrize('delegate,table,exception', [
    ('hatchling.build', '[tool.hatch.build.targets.wheel]\nshared-data = []', ValueError),
    ('hatchling.build', '[tool.hatch.build.targets.wheel.shared-data]\n"missing" = "share/data"', FileNotFoundError),
    ('hatchling.build', '[tool.hatch.build.targets.wheel.shared-data]\n"data" = "../escape"', ValueError),
    ('hatchling.build', '[tool.hatch.build.targets.wheel.shared-data]\n"data" = 1', ValueError),
])
def test_invalid_discovered_mappings(tmp_path, delegate, table, exception):
    source(tmp_path, 'data')
    (tmp_path / 'directory').mkdir()
    with pytest.raises(exception):
        discover(tmp_path, delegate, table)


def test_discovered_parent_collision(tmp_path):
    source(tmp_path, 'data')
    source(tmp_path, 'extra')
    with pytest.raises(ValueError, match='Conflicting'):
        discover(tmp_path, 'hatchling.build', '''
[tool.hatch.build.targets.wheel.shared-data]
"data" = "share/data"
"extra" = "share/data/child"
''')


def test_generated_sources_discovered_after_delegate_build(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import sys
    from zipfile import ZipFile

    # Give the fake delegate the standard name so real discovery runs, but defer
    # generation until its hook is invoked, as an actual build hook would do.
    (tmp_path / 'pyproject.toml').write_text('''
[tool.editable-wheel-data]
backend = "hatchling.build"
[tool.hatch.build.targets.wheel.shared-data]
"generated" = "share/generated"
''')
    output = tmp_path / 'wheels'
    output.mkdir()
    calls = []

    def build(wheel_directory, config_settings, metadata_directory):
        calls.append((wheel_directory, config_settings, metadata_directory))
        source(tmp_path, 'generated/robot.urdf')
        name = 'demo-1-py3-none-any.whl'
        with ZipFile(output / name, 'w') as wheel:
            wheel.writestr('demo-1.dist-info/WHEEL', 'Wheel-Version: 1.0\n')
            wheel.writestr('demo-1.dist-info/RECORD', '')
        return name

    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, 'hatchling.build', SimpleNamespace(build_editable=build))
    filename = backend.build_editable(str(output), {'option': 'value'}, 'metadata')
    assert calls == [(str(output), {'option': 'value'}, 'metadata')]
    with ZipFile(output / filename) as wheel:
        assert wheel.read('demo-1.data/data/share/generated/robot.urdf') == b'data'

