"""PEP 517/660 backend wrapper for editable external wheel data."""
from __future__ import annotations

from importlib import import_module
from pathlib import Path, PurePosixPath, PureWindowsPath

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9 and 3.10
    import tomli as tomllib

from ._wheel import rewrite_editable_wheel


_HOOKS = {
    "build_wheel", "build_sdist", "get_requires_for_build_wheel",
    "get_requires_for_build_sdist", "prepare_metadata_for_build_wheel",
    "build_editable", "get_requires_for_build_editable",
    "prepare_metadata_for_build_editable",
}


def _configuration():
    root = Path.cwd().resolve()
    with (root / "pyproject.toml").open("rb") as stream:
        config = tomllib.load(stream).get("tool", {}).get("editable-wheel-data")
    if not isinstance(config, dict) or not isinstance(config.get("backend"), str):
        raise ValueError("[tool.editable-wheel-data] requires a backend string")
    unknown = set(config) - {"backend"}
    if unknown:
        raise ValueError(f"Unknown editable-wheel-data settings: {sorted(unknown)}")
    return root, config


def _backend(config):
    module_name, separator, object_path = config["backend"].partition(":")
    components = module_name.split(".") + (object_path.split(".") if separator else [])
    if not all(component.isidentifier() for component in components):
        raise ValueError(f"Invalid delegate backend: {config['backend']!r}")
    if module_name == __name__ or module_name.startswith(__name__ + "."):
        raise ValueError("editable-wheel-data cannot delegate to itself")
    backend = import_module(module_name)
    if separator:
        for component in object_path.split("."):
            backend = getattr(backend, component)
    return backend


def _relative_path(value, label, allow_root=False):
    if (not isinstance(value, str) or not value or "\\" in value
            or "\x00" in value or PureWindowsPath(value).drive
            or PurePosixPath(value).is_absolute()
            or ".." in value.split("/")
            or (not allow_root and not PurePosixPath(value).parts)):
        raise ValueError(f"Invalid {label}: {value!r}; use a relative POSIX path")
    return PurePosixPath(value)


def _data_files(root, mappings):
    files = {}
    mapping_entries = mappings.items() if isinstance(mappings, dict) else mappings
    for source_name, destination_name in mapping_entries:
        relative = _relative_path(source_name, "data source", allow_root=True)
        destination = _relative_path(destination_name, "data destination", allow_root=True)
        source = root.joinpath(*relative.parts)
        if not source.exists():
            raise FileNotFoundError(f"Editable data source is missing: {source}")
        resolved_source = source.resolve()
        if root != resolved_source and root not in resolved_source.parents:
            raise ValueError(f"Editable data source escapes the project: {source}")
        if source.is_dir():
            entries = [(file, destination / file.relative_to(source).as_posix())
                       for file in sorted(source.rglob("*")) if file.is_file()]
        elif source.is_file():
            entries = [(source, destination)]
        else:
            raise ValueError(f"Editable data source is not a file or directory: {source}")
        for file, target in entries:
            resolved = file.resolve()
            if root != resolved and root not in resolved.parents:
                raise ValueError(f"Editable data source escapes the project: {file}")
            _relative_path(target.as_posix(), "data destination")
            key = target.as_posix().casefold()
            if key in files:
                raise ValueError(f"Duplicate editable data destination: {target}")
            files[key] = (resolved, target.as_posix())
    _validate_destinations(files)
    return list(files.values())


def _validate_destinations(destinations):
    destinations = set(destinations)
    for key in destinations:
        if any(parent.as_posix() in destinations for parent in PurePosixPath(key).parents
               if parent.parts):
            raise ValueError(f"Conflicting editable data destinations: {key}")


def _discovered_mappings(root, config):
    """Read declarative mappings for delegates without a build adapter."""
    with (root / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    tool = project.get("tool", {})
    if config["backend"] == "hatchling.build":
        mappings = (tool.get("hatch", {}).get("build", {}).get("targets", {})
                    .get("wheel", {}).get("shared-data", {}))
        if not isinstance(mappings, dict):
            raise ValueError("Hatchling shared-data must be a source-to-destination table")
        return list(mappings.items())
    return []


def __getattr__(name):
    # Preserve absence of optional delegate hooks, including build_editable.
    if name not in _HOOKS:
        raise AttributeError(name)
    _, config = _configuration()
    hook = getattr(_backend(config), name)
    if name == "build_editable":
        return _build_editable
    return hook


def _build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    root, config = _configuration()
    backend = _backend(config)
    # Backends may generate durable data sources during their build.
    if config["backend"] in {"setuptools.build_meta", "setuptools.build_meta:__legacy__"}:
        from ._setuptools import build_editable as build_setuptools_editable

        filename, mappings = build_setuptools_editable(
            backend, root, wheel_directory, config_settings, metadata_directory,
        )
    else:
        filename = backend.build_editable(wheel_directory, config_settings, metadata_directory)
        mappings = _discovered_mappings(root, config)
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError(f"Backend returned an invalid wheel filename: {filename!r}")
    rewrite_editable_wheel(
        Path(wheel_directory) / filename,
        _data_files(root, mappings),
    )
    return filename
