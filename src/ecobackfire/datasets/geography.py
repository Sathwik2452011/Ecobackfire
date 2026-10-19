"""Structured geographic codes (pure; syntax and containment only).

Accepted forms (case-insensitive on input, normalized to upper case)::

    GLOBAL          the whole world
    M49:150         UN M49 region/country numeric code (3 digits)
    IN              ISO 3166-1 alpha-2 country (two letters)
    IN-AP           ISO 3166-2 subdivision (country, hyphen, 1-3 letters/digits)

Limitations, stated rather than hidden:
* Codes are checked for SYNTAX only; whether ``ZZ`` is an assigned country is not checked (no code list ships).
* M49 membership (which countries lie in region 150) is NOT resolved: an M49 code covers only itself (and
  ``GLOBAL`` covers everything). Requests must therefore use the same level as the dataset, or a dataset must
  list the country codes it covers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

GLOBAL = "GLOBAL"
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_SUBDIV = re.compile(r"^([A-Z]{2})-[A-Z0-9]{1,3}$")
_M49 = re.compile(r"^M49:[0-9]{3}$")


def normalize_code(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError(f"geography code must be a string, got {text!r}")
    t = text.strip().upper()
    if t == GLOBAL or _COUNTRY.match(t) or _SUBDIV.match(t) or _M49.match(t):
        return t
    raise ValueError(
        f"invalid geography code {text!r}; expected GLOBAL, M49:NNN, ISO 3166-1 alpha-2 (e.g. 'IN') "
        "or ISO 3166-2 (e.g. 'IN-AP')"
    )


def code_level(code: str) -> str:
    c = normalize_code(code)
    if c == GLOBAL:
        return "global"
    if _M49.match(c):
        return "m49"
    return "country" if _COUNTRY.match(c) else "subdivision"


def covers(dataset_code: str, requested_code: str) -> bool:
    """True if a record scoped to ``dataset_code`` is applicable to ``requested_code``.

    GLOBAL covers everything; a country covers its own subdivisions; identical codes cover each other.
    A subdivision does not cover its country, and unrelated codes never cover each other.
    """
    d, r = normalize_code(dataset_code), normalize_code(requested_code)
    if d in (r, GLOBAL):
        return True
    sub = _SUBDIV.match(r)
    return bool(sub and _COUNTRY.match(d) and sub.group(1) == d)


def covers_any(dataset_codes: Iterable[str], requested_code: str) -> bool:
    return any(covers(c, requested_code) for c in dataset_codes)
