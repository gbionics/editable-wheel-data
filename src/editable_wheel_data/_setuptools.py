"""Observe setuptools' resolved data_files during its ordinary editable build."""
from __future__ import annotations

from functools import wraps
from pathlib import Path


def build_editable(backend, root, wheel_directory, config_settings, metadata_directory):
    import setuptools

    original_setup = setuptools.setup
    distributions = []

    @wraps(original_setup)
    def capture_setup(*args, **kwargs):
        distribution = original_setup(*args, **kwargs)
        distributions.append(distribution)
        return distribution

    # The delegate still configures and builds the project exactly once. Observe
    # its public setup() result rather than executing setup.py a second time or
    # reading setuptools' private global distribution state.
    setuptools.setup = capture_setup
    try:
        filename = backend.build_editable(wheel_directory, config_settings, metadata_directory)
    finally:
        setuptools.setup = original_setup
    if not distributions:
        raise RuntimeError("Cannot discover data_files: the setuptools build did not call setuptools.setup()")
    return filename, _data_mappings(root, distributions[-1].data_files or [])


def _data_mappings(root, data_files):
    mappings = []
    for entry in data_files:
        if isinstance(entry, (str, Path)):
            directory, sources = '.', [entry]
        else:
            directory, sources = entry
        directory = Path(directory).as_posix() or '.'
        for source_name in sources:
            source = Path(source_name)
            if (root / source).is_dir():
                raise ValueError(f"Setuptools data_files sources must be files: {source}")
            # setup.py collectors commonly produce native paths; normalize them
            # for the wrapper's platform-independent validation. Absolute source
            # paths are acceptable only when they resolve inside the project.
            if source.is_absolute():
                try:
                    relative = source.resolve().relative_to(root)
                except ValueError as error:
                    raise ValueError(f"Editable data source escapes the project: {source}") from error
            else:
                relative = source
            mappings.append((relative.as_posix(), (Path(directory) / source.name).as_posix()))
    return list(dict.fromkeys(mappings))
