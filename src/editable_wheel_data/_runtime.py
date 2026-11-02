"""Standard-library activation helper embedded into each editable wheel."""
from __future__ import annotations

import hashlib
from importlib.metadata import PathDistribution
import os
from pathlib import Path, PurePosixPath
from uuid import uuid4


def install_links(dist_info, mapping):
    # Bind to this helper's own installation, even if another version is on sys.path.
    dist = PathDistribution(Path(__file__).with_name(dist_info))
    installed = list(dist.files or ())
    for destination, source_name, digest in mapping:
        suffix = PurePosixPath(destination).parts
        matches = [entry for entry in installed if entry.parts[-len(suffix):] == suffix]
        if len(matches) != 1:
            raise RuntimeError(f"Cannot locate editable data {destination!r} in {dist_info}")
        target = Path(os.path.abspath(dist.locate_file(matches[0])))
        source = Path(source_name)
        if not source.is_file():
            raise RuntimeError(f"Editable source data is missing: {source}; reinstall the project")
        if target.is_symlink():
            if target.resolve() == source.resolve():
                continue
            raise RuntimeError(f"Refusing to replace unrelated symlink: {target}")
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Editable data was modified: {target}; reinstall the project")
        temporary = target.with_name(target.name + "." + uuid4().hex + ".tmp")
        try:
            temporary.symlink_to(source)
            os.replace(temporary, target)
        except OSError as error:
            raise RuntimeError(
                f"Cannot symlink {target} to {source}. On Windows, enable Developer "
                "Mode or run with symlink privileges."
            ) from error
        finally:
            if temporary.is_symlink():
                temporary.unlink()
