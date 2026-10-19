"""Strict ``MAJOR.MINOR.PATCH`` dataset release versions (pure)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def parse_version(text: str) -> Version:
    """Parse strictly: three non-negative integers, no leading zeros, no pre-release/build suffix."""
    m = _RE.match(text) if isinstance(text, str) else None
    if m is None:
        raise ValueError(f"invalid release version {text!r}; expected MAJOR.MINOR.PATCH such as '1.0.0'")
    return Version(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def is_valid_version(text: str) -> bool:
    try:
        parse_version(text)
    except ValueError:
        return False
    return True


def sort_versions(texts: Iterable[str]) -> list[str]:
    """Ascending numeric order ('1.10.0' > '1.9.0'). Raises ValueError on any malformed entry."""
    return [str(v) for v in sorted(parse_version(t) for t in texts)]


def latest_version(texts: Iterable[str]) -> str:
    ordered = sort_versions(texts)
    if not ordered:
        raise ValueError("no versions to choose from")
    return ordered[-1]
