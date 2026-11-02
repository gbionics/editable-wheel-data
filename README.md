# editable-wheel-data

For Python projects that create wheels with `data` folders, most PEP 517 build backends copy the files in `data`  folder, even when the project itself in editable mode. Indeed, [PEP 660](https://peps.python.org/pep-0660/#limitations) covers editable Python imports but does not specify the same behavior for external wheel data. So

`editable-wheel-data` is a build backend wrapper that extends existing build backends by
adding support for installing the data files in editable mode, installing them via symlinks,
so that data installed via `pip install -e .` and `uv pip install -e .` is automatically updated when modified in the source directory, providing for the wheel data files the same user experiments of actual Python files.

The supported build backends that can be wrapped by `editable-wheel-data` are:
* [`setuptools`](#setuptools)
* [`hatchling`](#hatchling)

If you need support for additional build backends, please [open an issue](https://github.com/gbionics/editable-wheel-data/issues/new).

## setuptools

Add the wrapper and keep your existing data-file configuration. Editable mappings
are discovered from setuptools' resolved `data_files`, whether configured in
`pyproject.toml`, `setup.cfg`, or computed in `setup.py`:

```toml
[build-system]
requires = ["editable-wheel-data>=0.1", "setuptools>=64", "wheel"]
build-backend = "editable_wheel_data"

[tool.editable-wheel-data]
backend = "setuptools.build_meta"

[tool.setuptools]
# Include resources inside Python packages, for example via MANIFEST.in.
include-package-data = true

[tool.setuptools.data-files]
# Key = destination directory relative to the installation prefix;
# value = source files or glob patterns relative to the project root.
"share/my_robot" = ["share/my_robot/*.urdf", "package.xml"]
# Each file keeps its basename, so resource/my_robot is installed as my_robot.
"share/ament_index/resource_index/packages" = ["resource/my_robot"]
```

Add destination entries for resource subdirectories. Glob matches are installed
into the specified directory, without preserving their source directory structure.

## hatchling

Add the wrapper and keep your existing shared-data table. Editable mappings are
discovered automatically from `[tool.hatch.build.targets.wheel.shared-data]`:

```toml
[build-system]
requires = ["editable-wheel-data>=0.1", "hatchling>=1.26"]
build-backend = "editable_wheel_data"

[tool.editable-wheel-data]
backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/my_robot"]

[tool.hatch.build.targets.wheel.shared-data]
# Folder: key = source directory relative to the project root;
# value = destination directory relative to the installation prefix.
# All files inside the source directory are included recursively.
"share" = "share"
# File: key = source file relative to the project root;
# value = full destination filename relative to the installation prefix.
"package.xml" = "share/my_robot/package.xml"
# File: install the source marker at this exact destination filename.
"resource/my_robot" = "share/ament_index/resource_index/packages/my_robot"
```

Set `packages` to your Python package directory.

Hatchling discovery reads its `shared-data` table in `pyproject.toml`; mappings
configured only in `hatch.toml` or added dynamically by Hatchling hooks are not
supported.
Use `/` in paths on every platform and keep sources inside your checkout.

# Using a project with editable-wheel-data

To actually create the files that a project installs in editable mode, you need to run python at least once:

```sh
python -m pip install -e .
python -c "pass"  # Activate the resource symlinks.
```

Installation first creates data copies; the next Python startup replaces them
with links to your source files. Python packaging has no standard post-install
hook, so the wrapper uses a Python startup helper to create the links after
installation. The second command triggers this helper without running your app.
