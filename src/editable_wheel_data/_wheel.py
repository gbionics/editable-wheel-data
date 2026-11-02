"""Transform only external data in an editable wheel, retaining RECORD ownership."""
from __future__ import annotations

import base64
import csv
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile


def rewrite_editable_wheel(wheel_path, data_files):
    with ZipFile(wheel_path) as wheel:
        infos = wheel.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate wheel members")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError(f"Invalid wheel member: {name!r}")
        contents = {name: wheel.read(name) for name in names}
    dist_infos = [name[:-len("/WHEEL")] for name in names
                  if name.endswith(".dist-info/WHEEL") and name.count("/") == 1]
    if len(dist_infos) != 1:
        raise ValueError("Expected exactly one wheel dist-info directory")
    dist_info = dist_infos[0]
    if any(name.endswith(("/RECORD.jws", "/RECORD.p7s")) for name in names):
        raise ValueError("Cannot rewrite a signed editable wheel")
    data_root = dist_info[:-len(".dist-info")] + ".data/data/"
    expected = {data_root + destination for _, destination in data_files}
    actual = {name for name in names if name.startswith(data_root) and not name.endswith("/")}
    if actual - expected:
        raise ValueError(
            f"Unmapped editable wheel data: {sorted(actual - expected)}; "
            "configure external data through setuptools data_files or Hatchling shared-data"
        )
    mapping = []
    for source, destination in data_files:
        payload = source.read_bytes()
        member = data_root + destination
        if member in contents and contents[member] != payload:
            raise ValueError(f"Wheel data differs from configured source: {destination}")
        contents[member] = payload
        mapping.append((destination, str(source), hashlib.sha256(payload).hexdigest()))

    runtime = Path(__file__).with_name("_runtime.py").read_text(encoding="utf-8")
    runtime += f"\ninstall_links({dist_info!r}, {mapping!r})\n"
    # A same-version reinstall can change hashes while retaining the helper's
    # size and timestamp. Give changed content a different module name so Python
    # cannot reuse stale timestamp-based bytecode from the previous installation.
    fingerprint = hashlib.sha256(runtime.encode("utf-8")).hexdigest()[:16]
    module = ("_editable_wheel_data_"
              + re.sub(r"\W", "_", dist_info[:-len(".dist-info")]) + "_" + fingerprint)
    if module + ".py" in contents or module + ".pth" in contents:
        raise ValueError("Editable data helper conflicts with an existing wheel member")
    contents[module + ".py"] = runtime.encode("utf-8")
    contents[module + ".pth"] = f"import {module}\n".encode("utf-8")

    record = dist_info + "/RECORD"
    contents.pop(record, None)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    for name, payload in sorted(contents.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
        writer.writerow((name, "sha256=" + digest.decode("ascii"), len(payload)))
    writer.writerow((record, "", ""))
    contents[record] = output.getvalue().encode("utf-8")
    # Preserve delegate members' modes (notably executable scripts) and timestamps.
    original_infos = {info.filename: info for info in infos}
    with TemporaryDirectory(dir=wheel_path.parent) as temporary:
        replacement = Path(temporary) / wheel_path.name
        with ZipFile(replacement, "w", ZIP_DEFLATED) as wheel:
            for name, payload in contents.items():
                wheel.writestr(original_infos.get(name, name), payload)
        replacement.replace(wheel_path)
