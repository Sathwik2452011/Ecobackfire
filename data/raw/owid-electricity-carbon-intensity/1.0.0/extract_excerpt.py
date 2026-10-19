"""Reproduce the raw excerpts in this directory from a pinned checkout of the upstream repository.

Usage:
    git clone https://github.com/owid/energy-data owid && git -C owid checkout f8de88a4f6f9cab44f1cf606047cbd6c4414042c
    python extract_excerpt.py owid        # writes the two excerpt files next to this script

The excerpts are VERBATIM LINES of the upstream files (header line + the selected data lines, byte-identical,
upstream order). Nothing is parsed, rounded, reordered or converted. The selection below is fixed here, before
any model is run, and is documented in data/imports/owid-electricity-carbon-intensity-1.0.0.toml.
Stdlib only.
"""

from __future__ import annotations

import csv
import hashlib
import io
import sys
from pathlib import Path

UPSTREAM_COMMIT = "f8de88a4f6f9cab44f1cf606047cbd6c4414042c"
UPSTREAM_DATA_SHA256 = "266f2e2baad7975351bc9bb4aa061d22b1da9fe4c47d51d2ac6071e01e171f76"
UPSTREAM_CODEBOOK_SHA256 = "3cc9b7db0d921496e2988568ce3aee5ed41f50431dd234a0663b5f0a4b2e32bb"
SELECTION = [("World", "2024"), ("India", "2024"), ("United States", "2024"), ("Germany", "2024"), ("France", "2024")]
CODEBOOK_KEY = "carbon_intensity_elec"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main(checkout: str) -> None:
    here = Path(__file__).resolve().parent
    src = Path(checkout)
    data = (src / "owid-energy-data.csv").read_bytes()
    book = (src / "owid-energy-codebook.csv").read_bytes()
    if _sha(data) != UPSTREAM_DATA_SHA256 or _sha(book) != UPSTREAM_CODEBOOK_SHA256:
        raise SystemExit("upstream files do not match the pinned hashes; check out the pinned commit")
    lines = data.split(b"\n")
    header = lines[0]
    cols = next(csv.reader(io.StringIO(header.decode("utf-8"))))
    ci, yi = cols.index("country"), cols.index("year")
    picked: list[bytes] = []
    for country, year in SELECTION:
        hits = [
            ln
            for ln in lines[1:]
            if ln and (r := next(csv.reader(io.StringIO(ln.decode("utf-8")))))[ci] == country and r[yi] == year
        ]
        if len(hits) != 1:
            raise SystemExit(f"selection {country!r}/{year} matched {len(hits)} lines, expected exactly 1")
        picked.append(hits[0])
    (here / "owid-energy-data.excerpt.csv").write_bytes(b"\n".join([header, *picked]) + b"\n")
    blines = book.split(b"\n")
    brow = [ln for ln in blines[1:] if ln.startswith(CODEBOOK_KEY.encode() + b",")]
    if len(brow) != 1:
        raise SystemExit("codebook row not found exactly once")
    (here / "owid-energy-codebook.excerpt.csv").write_bytes(b"\n".join([blines[0], brow[0]]) + b"\n")
    print("wrote excerpts; upstream commit", UPSTREAM_COMMIT)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "owid")
