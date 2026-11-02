import os
from pathlib import Path

from setuptools import setup

marker = Path("build/markers/demo_robot")
marker.parent.mkdir(parents=True, exist_ok=True)
marker.write_text("")

resources = [
    (directory, [str(Path(directory) / name) for name in filenames])
    for directory, _, filenames in os.walk("share") if filenames
]
setup(data_files=[
    *resources,
    ("share/demo_robot", ["package.xml"]),
    ("share/ament_index/resource_index/packages", [str(marker)]),
])
