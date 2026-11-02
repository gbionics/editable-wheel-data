"""Exercise real frontend builds, activation, replacement, and uninstall."""
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tarfile
import venv
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd=None, env=None):
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert result.returncode == 0, result.stdout
    assert 'Error processing line' not in result.stdout, result.stdout
    return result.stdout


@pytest.fixture(scope='session')
def wrapper_wheel(tmp_path_factory):
    directory = tmp_path_factory.mktemp('wrapper-wheel')
    run(sys.executable, '-m', 'build', '--wheel', '--no-isolation', '--outdir', directory, ROOT)
    return next(directory.glob('*.whl'))


def project_at(path, backend):
    path.mkdir(parents=True)
    (path / 'demo_robot').mkdir()
    (path / 'demo_robot' / '__init__.py').write_text('VALUE = 42\n')
    data = path / 'share' / 'demo_robot'
    data.mkdir(parents=True)
    (data / 'robot.urdf').write_text('<robot name="demo"/>')
    (data / 'nested').mkdir()
    (data / 'nested' / 'mesh with spaces.stl').write_text('mesh')
    (path / 'resource').mkdir()
    (path / 'resource' / 'demo_robot').write_text('')
    config = '''[build-system]
requires = ["editable-wheel-data", "BACKEND", "wheel"]
build-backend = "editable_wheel_data"
[project]
name = "ewd-demo-robot"
version = "1.0.0"
[tool.editable-wheel-data]
backend = "DELEGATE"
'''.replace('BACKEND', backend).replace('DELEGATE', {
        'hatchling': 'hatchling.build', 'setuptools': 'setuptools.build_meta'}[backend])
    if backend == 'hatchling':
        config += '''[tool.hatch.build.targets.wheel]
packages = ["demo_robot"]
[tool.hatch.build.targets.wheel.shared-data]
"share" = "share"
"resource/demo_robot" = "share/ament_index/resource_index/packages/demo_robot"
'''
    else:
        config += '''[tool.setuptools]
packages = ["demo_robot"]
[tool.setuptools.data-files]
"share/demo_robot" = ["share/demo_robot/*.urdf"]
"share/demo_robot/nested" = ["share/demo_robot/nested/*.stl"]
"share/ament_index/resource_index/packages" = ["resource/demo_robot"]
'''
    (path / 'pyproject.toml').write_text(config)
    # Keep every configured source in the setuptools sdist as well.
    (path / 'MANIFEST.in').write_text('recursive-include share *\nrecursive-include resource *\n')
    return path

@pytest.mark.parametrize('backend', ['setuptools', 'hatchling'])
@pytest.mark.parametrize('frontend', ['pip', 'uv'])
def test_lifecycle(tmp_path, wrapper_wheel, backend, frontend):
    project = project_at(tmp_path / 'source with spaces', backend)
    environment = tmp_path / 'venv'
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    # Both frontend paths build in isolation. The unpublished wrapper is supplied
    # as a local wheel, just as it will later be fetched from PyPI.
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env['PIP_FIND_LINKS'] = str(wrapper_wheel.parent)
    env['UV_FIND_LINKS'] = str(wrapper_wheel.parent)
    env.setdefault('UV_CACHE_DIR', str(wrapper_wheel.parent / 'uv-cache'))
    env.setdefault('PIP_CACHE_DIR', str(wrapper_wheel.parent / 'pip-cache'))
    if frontend == 'pip':
        install = [python, '-m', 'pip', 'install', '--no-deps']
        uninstall = [python, '-m', 'pip', 'uninstall', '-y', 'ewd-demo-robot']
    else:
        install = [shutil.which('uv'), 'pip', 'install', '--python', python, '--no-deps']
        uninstall = [shutil.which('uv'), 'pip', 'uninstall', '--python', python, 'ewd-demo-robot']
    wheels = tmp_path / 'wheels'
    run(python, '-m', 'pip', 'wheel', '--no-deps', '--wheel-dir', wheels, project, env=env)
    wheel = next(wheels.glob('*.whl'))
    with ZipFile(wheel) as archive:
        assert not any('_editable_wheel_data_' in name for name in archive.namelist())
        for destination in ['share/demo_robot/robot.urdf',
                            'share/demo_robot/nested/mesh with spaces.stl',
                            'share/ament_index/resource_index/packages/demo_robot']:
            assert any(name.endswith('.data/data/' + destination) for name in archive.namelist())
    run(*install, wheel, env=env)
    target = environment / 'share' / 'demo_robot' / 'robot.urdf'
    assert target.is_file() and not target.is_symlink()
    sources = {
        'share/demo_robot/robot.urdf': project / 'share/demo_robot/robot.urdf',
        'share/demo_robot/nested/mesh with spaces.stl': project / 'share/demo_robot/nested/mesh with spaces.stl',
        'share/ament_index/resource_index/packages/demo_robot': project / 'resource/demo_robot',
    }
    for _ in range(2):
        run(*install, '--force-reinstall' if frontend == 'pip' else '--reinstall', '-e', project, env=env)
        # Source can change between wheel build and activation.
        sources['share/demo_robot/robot.urdf'].write_text('<robot name="edited"/>')
        run(python, '-c', 'import demo_robot; assert demo_robot.VALUE == 42; '
            'import importlib.util; assert importlib.util.find_spec("editable_wheel_data") is None',
            cwd=tmp_path, env=env)
        for destination, source in sources.items():
            installed = environment / destination
            assert installed.is_symlink()
            assert installed.resolve() == source.resolve()
            source.write_text(source.read_text() + '\nedit')
            assert installed.read_bytes() == source.read_bytes()
    # Switching back to a wheel removes activation helpers and links.
    run(*install, '--force-reinstall' if frontend == 'pip' else '--reinstall', wheel, env=env)
    run(python, '-c', 'import demo_robot', env=env)
    assert target.is_file() and not target.is_symlink()
    run(*install, '--force-reinstall' if frontend == 'pip' else '--reinstall', '-e', project, env=env)
    run(python, '-c', 'pass', env=env)
    run(*uninstall, env=env)
    run(python, '-c', 'pass', env=env)
    for destination, source in sources.items():
        assert not os.path.lexists(environment / destination)
        assert source.is_file()


def test_sdist_rebuild(tmp_path):
    run(sys.executable, '-m', 'build', '--sdist', '--no-isolation', '--outdir', tmp_path, ROOT)
    archive = next(tmp_path.glob('*.tar.gz'))
    with tarfile.open(archive) as sdist:
        # The archive is generated locally by our own backend.
        sdist.extractall(tmp_path / 'source', filter='data') if sys.version_info >= (3, 12) else sdist.extractall(tmp_path / 'source')
    project = next((tmp_path / 'source').iterdir())
    run(sys.executable, '-m', 'build', '--wheel', '--no-isolation', '--outdir', tmp_path, project)
    with ZipFile(next(tmp_path.glob('*.whl'))) as wheel:
        assert 'editable_wheel_data/_runtime.py' in wheel.namelist()


@pytest.mark.parametrize('backend', ['setuptools', 'hatchling'])
@pytest.mark.parametrize('frontend', ['pip', 'uv'])
def test_project_fixtures(tmp_path, wrapper_wheel, backend, frontend):
    name = 'demo_robot'
    fixture = 'setuptools_dynamic' if backend == 'setuptools' else 'hatchling'
    project = tmp_path / 'source with spaces'
    shutil.copytree(ROOT / 'tests/fixtures' / fixture, project)
    source = project / 'share' / name / 'robot.urdf'
    extra = project / 'share' / name / 'nested/mesh with spaces.stl'
    destinations = {f'share/{name}/robot.urdf': source,
                    f'share/{name}/nested/mesh with spaces.stl': extra}
    if backend == 'setuptools':
        # Package-internal resources and external data_files are independent.
        config = (project / 'pyproject.toml').read_text()
        assert 'include-package-data = true' in config
        package_resource = project / name / 'settings with spaces.json'
        destinations[f'share/{name}/package.xml'] = project / 'package.xml'
        destinations[f'share/ament_index/resource_index/packages/{name}'] = (
            project / 'build/markers' / name)
    environment = tmp_path / 'venv'
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env['PIP_FIND_LINKS'] = str(wrapper_wheel.parent)
    env['UV_FIND_LINKS'] = str(wrapper_wheel.parent)
    env.setdefault('UV_CACHE_DIR', str(wrapper_wheel.parent / 'uv-cache'))
    env.setdefault('PIP_CACHE_DIR', str(wrapper_wheel.parent / 'pip-cache'))
    if frontend == 'pip':
        install = [python, '-m', 'pip', 'install', '--no-deps']
        uninstall = [python, '-m', 'pip', 'uninstall', '-y', name]
        reinstall = '--force-reinstall'
    else:
        install = [shutil.which('uv'), 'pip', 'install', '--python', python, '--no-deps']
        uninstall = [shutil.which('uv'), 'pip', 'uninstall', '--python', python, name]
        reinstall = '--reinstall'
    wheels = tmp_path / 'wheels'
    run(python, '-m', 'pip', 'wheel', '--no-deps', '--wheel-dir', wheels, project, env=env)
    wheel = next(wheels.glob('*.whl'))
    with ZipFile(wheel) as archive:
        for destination in destinations:
            assert any(member.endswith('.data/data/' + destination) for member in archive.namelist())
        if backend == 'setuptools':
            assert archive.read(f'{name}/settings with spaces.json') == b'{"value": 1}'
    run(*install, wheel, env=env)
    for destination in destinations:
        assert (environment / destination).is_file()
        assert not (environment / destination).is_symlink()
    resource_script = (
        'from importlib.resources import files; '
        f'assert files({name!r}).joinpath("settings with spaces.json").read_text() == '
    )
    if backend == 'setuptools':
        run(python, '-c', resource_script + repr('{"value": 1}'), cwd=tmp_path, env=env)
    for _ in range(2):
        run(*install, reinstall, '-e', project, env=env)
        run(python, '-c', f'import {name}', cwd=tmp_path, env=env)
        for destination, original in destinations.items():
            installed = environment / destination
            assert installed.is_symlink()
            assert installed.resolve() == original.resolve()
        source.write_text(source.read_text() + '\n<!-- live source edit -->')
        assert (environment / f'share/{name}/robot.urdf').read_bytes() == source.read_bytes()
        if backend == 'setuptools':
            package_resource.write_text('{"value": 2}')
            run(python, '-c', resource_script + repr('{"value": 2}'), cwd=tmp_path, env=env)
    run(*uninstall, env=env)
    run(python, '-c', 'pass', env=env)
    for destination, original in destinations.items():
        assert not os.path.lexists(environment / destination)
        assert original.is_file()
    run(*install, wheel, env=env)
    run(python, '-c', f'import {name}', env=env)
    for destination in destinations:
        assert not (environment / destination).is_symlink()
    if backend == 'setuptools':
        run(python, '-c', resource_script + repr('{"value": 1}'), cwd=tmp_path, env=env)


def test_setuptools_resolved_globs_and_one_to_many(tmp_path):
    project = project_at(tmp_path / 'project', 'setuptools')
    config = project / 'pyproject.toml'
    text = config.read_text().split('[tool.setuptools.data-files]')[0]
    text += '''[tool.setuptools.data-files]
"share/flat" = ["share/**/*.urdf", "share/demo_robot/*.urdf", "missing/*.urdf"]
"share/another" = ["share/demo_robot/robot.urdf"]
'''
    config.write_text(text)
    nested = project / 'share/demo_robot/nested/other.urdf'
    nested.write_text('nested')
    (project / 'share/demo_robot/.hidden.urdf').write_text('hidden')
    wheels = tmp_path / 'wheels'
    wheels.mkdir()
    run(sys.executable, '-c', 'import editable_wheel_data; '
        f'editable_wheel_data.build_editable({str(wheels)!r})', cwd=project)
    with ZipFile(next(wheels.glob('*.whl'))) as wheel:
        data = {name.split('.data/data/', 1)[1]: wheel.read(name)
                for name in wheel.namelist() if '.data/data/' in name}
    assert data == {'share/flat/robot.urdf': b'<robot name="demo"/>',
                    'share/flat/other.urdf': b'nested',
                    'share/another/robot.urdf': b'<robot name="demo"/>'}


def test_setuptools_setup_cfg_data_files(tmp_path):
    project = project_at(tmp_path / 'project', 'setuptools')
    config = project / 'pyproject.toml'
    config.write_text(config.read_text().split('[tool.setuptools.data-files]')[0])
    (project / 'setup.cfg').write_text('''[options.data_files]
share/config = share/demo_robot/robot.urdf
''')
    wheels = tmp_path / 'wheels'
    wheels.mkdir()
    run(sys.executable, '-c', 'import editable_wheel_data; '
        f'editable_wheel_data.build_editable({str(wheels)!r})', cwd=project)
    with ZipFile(next(wheels.glob('*.whl'))) as wheel:
        entry = next(name for name in wheel.namelist() if '.data/data/share/config/' in name)
        assert wheel.read(entry) == b'<robot name="demo"/>'
