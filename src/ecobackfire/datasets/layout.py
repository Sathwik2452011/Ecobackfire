"""On-disk layout of the local dataset registry (pure: stdlib only).

Default root: ``data/releases/``. One immutable release per directory::

    <root>/<dataset_id>/<release_version>/dataset.json

``dataset_id``: lower-case letters, digits, ``.`` ``_`` ``-``; starts with a letter or digit; 1-64 characters.
``release_version``: strict MAJOR.MINOR.PATCH. Hidden entries (leading ``.``) and plain files are ignored.
Anything else that looks like a dataset or release directory but breaks the rules is reported as a problem and
never silently skipped.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ecobackfire.datasets.versioning import is_valid_version, parse_version

DATASET_FILENAME = "dataset.json"
DEFAULT_ROOT = Path("data") / "releases"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class DatasetLocation:
    dataset_id: str
    version: str
    path: Path  # the dataset.json file


def valid_dataset_id(text: str) -> bool:
    return isinstance(text, str) and bool(_ID_RE.match(text))


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def discover(root: Path) -> tuple[list[DatasetLocation], list[str]]:
    """Scan ``root``; return (locations sorted by id then version, problems). Raises FileNotFoundError if absent."""
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"dataset registry root not found or not a directory: {root}")
    found: list[DatasetLocation] = []
    problems: list[str] = []
    for ds_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if not valid_dataset_id(ds_dir.name):
            problems.append(f"{ds_dir}: directory name {ds_dir.name!r} is not a valid dataset id")
            continue
        for v_dir in sorted(p for p in ds_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            if not is_valid_version(v_dir.name):
                problems.append(f"{v_dir}: directory name {v_dir.name!r} is not a MAJOR.MINOR.PATCH version")
                continue
            f = v_dir / DATASET_FILENAME
            if not f.is_file():
                problems.append(f"{v_dir}: missing {DATASET_FILENAME}")
                continue
            found.append(DatasetLocation(ds_dir.name, v_dir.name, f))
    found.sort(key=lambda loc: (loc.dataset_id, parse_version(loc.version)))
    return found, problems
