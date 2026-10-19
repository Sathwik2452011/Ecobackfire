"""Web-research layer of the EcoBackfire demo: question -> required parameters -> candidate values + sources.

HONESTY RULES (enforced here, tested in tests/web/test_research.py)
  * A number is only ever proposed if it appears VERBATIM in a quoted sentence of a search result. The quote is
    kept, so a reader can check the number against the text it came from. Nothing is generated, interpolated,
    averaged or "typical-valued".
  * No result for a parameter => status ``missing`` => "Evidence insufficient — no reliable source found."
    A missing parameter never receives a number from this module.
  * Illustrative defaults (the engine's own placeholders from examples/missions/example.toml) are carried in a
    SEPARATE field (``illustrative_default``). They are never turned into candidates and never called evidence.
  * Search unavailable (no API key / provider error) is reported as such: status ``not_researched`` /
    ``search_failed``, never ``missing`` and never a value.
  * Automatic extraction is a keyword + unit heuristic over search-result text. It can be wrong (wrong device,
    wrong country, wrong year, wrong definition). Every web candidate is therefore labelled
    ``sourced_unreviewed`` (the engine's own term: a citation exists, nobody has checked it).

This module performs web I/O only through ``SearchProvider``; it never fetches the pages it links to and never turns
a URL into a filesystem path. It never imports the calculation code beyond reading model metadata.
"""

from __future__ import annotations

import datetime as _dt
import ipaddress
import os
import re
import secrets
import tomllib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from ecobackfire.errors import UnitError
from ecobackfire.models.registry import get_model
from ecobackfire.units import to_canonical

MODEL_REF = "efficiency_rebound@1.0.0"
MODEL_OPTIONS = {"boundary_variant": "B1", "demand_form": "constant_elasticity"}  # same as the canonical mission
SEARCH_KEY_ENV = "ECOBACKFIRE_SEARCH_API_KEY"
SEARCH_PROVIDER_ENV = "ECOBACKFIRE_SEARCH_PROVIDER"  # "tavily" (default) or "brave"
MAX_CANDIDATES_PER_PARAMETER = 4
EVIDENCE_INSUFFICIENT = "Evidence insufficient — no reliable source found."
EXTRACTION_CAVEAT = (
    "Extracted automatically from a search-result text by a keyword + unit rule; the number appears verbatim in "
    "the quoted sentence. Not reviewed: check the source for device, country, year and definition."
)

# ------------------------------------------------------------------------------------------------ URL safety


def validate_source_url(url: object) -> str | None:
    """Return a normalised http(s) URL or None. Rejects other schemes, credentials, control characters, localhost,
    internal/private hosts and IP literals. The result is only ever displayed as a link or stored as text: it is never
    fetched here and never used as a path."""
    if not isinstance(url, str):
        return None
    u = url.strip()
    if not u or len(u) > 2000 or any(ord(c) < 33 or ord(c) == 127 for c in u):
        return None
    try:
        parts = urlsplit(u)
        host = (parts.hostname or "").lower()
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        return None
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost", ".lan", ".home", ".corp")):
        return None
    try:
        ipaddress.ip_address(host)
        return None  # IP literals are not acceptable sources
    except ValueError:
        pass
    if "." not in host:
        return None
    return u


_AUTH_SUFFIXES = (
    ".gov", ".gov.in", ".nic.in", ".gov.uk", ".gov.au", ".europa.eu", ".edu", ".ac.uk", ".ac.in", ".edu.au", ".int",
)  # fmt: skip
_AUTH_DOMAINS = frozenset(
    {
        "iea.org", "ember-energy.org", "ourworldindata.org", "worldbank.org", "un.org", "unep.org", "ipcc.ch",
        "irena.org", "oecd.org", "doi.org", "nature.com", "sciencedirect.com", "springer.com", "wiley.com",
        "ieee.org", "mdpi.com", "arxiv.org", "pnas.org", "science.org", "tandfonline.com", "iop.org", "lbl.gov",
        "nrel.gov", "eia.gov", "bee.gov.in", "cea.nic.in", "teriin.org", "ceew.in", "nber.org", "ssrn.com",
        "copernicus.org", "cell.com", "annualreviews.org", "jstor.org", "eceee.org", "iccsd.org", "wri.org",
        "rff.org", "brookings.edu", "rmi.org", "unfccc.int",
    }
)  # fmt: skip


def source_tier(url: str) -> str:
    """'authoritative' (government / intergovernmental / academic / recognised research body or publisher) or
    'other'. A coarse domain heuristic, NOT a quality judgement of the particular document."""
    host = (urlsplit(url).hostname or "").lower()
    if host.endswith(_AUTH_SUFFIXES):
        return "authoritative"
    if any(host == d or host.endswith("." + d) for d in _AUTH_DOMAINS):
        return "authoritative"
    return "other"


# ------------------------------------------------------------------------------------------------ search providers


class SearchUnavailable(RuntimeError):
    """The web-search provider cannot be used (no key, unknown provider, network failure)."""


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    published: str | None = None


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, max_results: int = 6) -> list[SearchHit]: ...


class TavilyProvider:
    """https://docs.tavily.com — POST /search with a bearer key. Returns page-content snippets (good for numbers)."""

    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def __init__(self, api_key: str, client: Any | None = None) -> None:
        self._key = api_key
        self._client = client

    def search(self, query: str, max_results: int = 6) -> list[SearchHit]:
        import httpx

        body = {"query": query, "search_depth": "basic", "max_results": max_results, "include_answer": False}
        try:
            if self._client is not None:
                r = self._client.post(self.endpoint, json=body, headers={"Authorization": f"Bearer {self._key}"})
            else:
                r = httpx.post(self.endpoint, json=body, headers={"Authorization": f"Bearer {self._key}"}, timeout=12.0)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:  # network / HTTP / JSON: never echo the request (it carries the key)
            raise SearchUnavailable(f"tavily search failed: {type(exc).__name__}") from None
        hits = []
        for it in data.get("results", []) if isinstance(data, dict) else []:
            if isinstance(it, dict):
                hits.append(
                    SearchHit(
                        title=str(it.get("title") or ""),
                        url=str(it.get("url") or ""),
                        snippet=str(it.get("content") or ""),
                        published=it.get("published_date") if isinstance(it.get("published_date"), str) else None,
                    )
                )
        return hits


class BraveProvider:
    """https://api.search.brave.com — GET /res/v1/web/search with X-Subscription-Token."""

    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, client: Any | None = None) -> None:
        self._key = api_key
        self._client = client

    def search(self, query: str, max_results: int = 6) -> list[SearchHit]:
        import httpx

        headers = {"X-Subscription-Token": self._key, "Accept": "application/json"}
        params = {"q": query, "count": max_results}
        try:
            if self._client is not None:
                r = self._client.get(self.endpoint, params=params, headers=headers)
            else:
                r = httpx.get(self.endpoint, params=params, headers=headers, timeout=12.0)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            raise SearchUnavailable(f"brave search failed: {type(exc).__name__}") from None
        web = data.get("web", {}) if isinstance(data, dict) else {}
        hits = []
        for it in web.get("results", []) if isinstance(web, dict) else []:
            if isinstance(it, dict):
                snippet = " ".join(
                    [str(it.get("description") or "")] + [str(x) for x in (it.get("extra_snippets") or [])]
                )
                hits.append(
                    SearchHit(
                        title=str(it.get("title") or ""),
                        url=str(it.get("url") or ""),
                        snippet=snippet.strip(),
                        published=str(it.get("page_age") or it.get("age") or "") or None,
                    )
                )
        return hits


def get_provider() -> SearchProvider:
    """Provider from the environment. Raises ``SearchUnavailable`` with a user-readable reason; never returns a fake."""
    key = os.environ.get(SEARCH_KEY_ENV, "").strip()
    if not key:
        raise SearchUnavailable(
            f"Web research is unavailable: the environment variable {SEARCH_KEY_ENV} is not set, so no web search "
            "was performed. Registry evidence (if any) is still shown."
        )
    name = os.environ.get(SEARCH_PROVIDER_ENV, "tavily").strip().lower()
    if name == "tavily":
        return TavilyProvider(key)
    if name == "brave":
        return BraveProvider(key)
    raise SearchUnavailable(f"Unknown search provider {name!r} in {SEARCH_PROVIDER_ENV}; use 'tavily' or 'brave'.")


# ------------------------------------------------------------------------------------------------ parameter specs

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_RANGE_SEP = r"(?:\s*(?:-|–|—|to|and)\s*)"


@dataclass(frozen=True)
class ParamSpec:
    """How to look for ONE model input. Search phrasing and extraction rules only: no scientific values."""

    label: str
    queries: tuple[str, ...]  # templates with {subject} and {geo}
    keywords: tuple[str, ...]  # one must occur in the sentence (lower-case)
    units: tuple[tuple[str, str], ...] = ()  # (regex for the unit text, unit string understood by pint)
    needs_geo: bool = False
    ambiguity_note: str | None = None
    kind: str = "quantity"  # "quantity" | "elasticity"


PARAM_SPECS: dict[str, ParamSpec] = {
    "epsilon": ParamSpec(
        label="Own-price elasticity of service demand (absolute value)",
        queries=("price elasticity of demand {subject} {geo}", "{subject} rebound effect elasticity study"),
        keywords=("elasticit",),
        kind="elasticity",
        ambiguity_note="Short- vs long-run and direct-only definitions differ between studies; the model needs the "
        "supplier to state which one applies.",
    ),
    "f": ParamSpec(
        label="Emissions factor of delivered electricity",
        queries=("grid emission factor electricity {geo} kgCO2/kWh", "electricity carbon intensity {geo} gCO2/kWh"),
        keywords=(
            "emission factor",
            "emissions factor",
            "carbon intensity",
            "emission intensity",
            "grid intensity",
            "co2/kwh",
            "co2e/kwh",
            "co2 per kwh",
            "g/kwh",
            "kg/kwh",
        ),  # fmt: skip
        units=(
            (r"g\s?CO[2₂]e?\s*(?:/|per)\s*kWh", "gCO2e/kWh"),
            (r"kg\s?CO[2₂]e?\s*(?:/|per)\s*kWh", "kgCO2e/kWh"),
            (r"t(?:onnes?|ons)?\s?CO[2₂]e?\s*(?:/|per)\s*MWh", "tCO2e/MWh"),
        ),
        needs_geo=True,
        ambiguity_note="Average vs marginal, generated vs delivered, and the year differ between sources.",
    ),
    "EM1": ParamSpec(
        label="Embodied emissions of the efficient (new) device",
        queries=(
            "embodied carbon energy efficient {subject} manufacturing kgCO2e",
            "{subject} life cycle assessment embodied emissions",
        ),
        keywords=("embodied", "manufactur", "production", "cradle", "life cycle", "lifecycle", "footprint"),
        units=(
            (
                r"kg\s?CO[2₂]e?+(?!\s*(?:/|per\b)\s*(?:kwh|mwh|gwh|mj|gj|kg|g\b|tonnes?|tons?|years?|yrs?|annum|hours?|hrs?|km|miles?|m2|m²|m3|litres?|liters?|kw\b|btu))",
                "kgCO2e",
            ),
            (
                r"t(?:onnes?|ons)?\s?CO[2₂]e?+(?!\s*(?:/|per\b)\s*(?:kwh|mwh|gwh|mj|gj|kg|g\b|tonnes?|tons?|years?|yrs?|annum|hours?|hrs?|km|miles?|m2|m²|m3|litres?|liters?|kw\b|btu))",
                "tCO2e",
            ),
        ),
        ambiguity_note="A search result rarely says whether the figure is for the baseline or the efficient device; "
        "check which one it describes.",
    ),
    "EM0": ParamSpec(
        label="Embodied emissions of the baseline (old) device",
        queries=(
            "embodied carbon {subject} manufacturing kgCO2e",
            "{subject} life cycle assessment embodied emissions",
        ),
        keywords=("embodied", "manufactur", "production", "cradle", "life cycle", "lifecycle", "footprint"),
        units=(
            (
                r"kg\s?CO[2₂]e?+(?!\s*(?:/|per\b)\s*(?:kwh|mwh|gwh|mj|gj|kg|g\b|tonnes?|tons?|years?|yrs?|annum|hours?|hrs?|km|miles?|m2|m²|m3|litres?|liters?|kw\b|btu))",
                "kgCO2e",
            ),
            (
                r"t(?:onnes?|ons)?\s?CO[2₂]e?+(?!\s*(?:/|per\b)\s*(?:kwh|mwh|gwh|mj|gj|kg|g\b|tonnes?|tons?|years?|yrs?|annum|hours?|hrs?|km|miles?|m2|m²|m3|litres?|liters?|kw\b|btu))",
                "tCO2e",
            ),
        ),
        ambiguity_note="A search result rarely says whether the figure is for the baseline or the efficient device; "
        "check which one it describes.",
    ),
    "L": ParamSpec(
        label="Device lifetime (amortization period)",
        queries=("{subject} average lifetime years {geo}", "{subject} useful life lifespan"),
        keywords=("lifetime", "lifespan", "life span", "service life", "useful life", "operating life", "last"),
        units=((r"(?:years?|yrs?)\b", "yr"),),
    ),
    "E0": ParamSpec(
        label="Baseline annual energy use per user",
        queries=("{subject} annual electricity consumption kWh per year {geo}", "{subject} energy use kWh/year"),
        keywords=("annual", "per year", "a year", "yearly", "consumption", "electricity use", "energy use"),
        units=(
            (r"kWh\s*(?:/|per)\s*(?:yr|year|annum|a)\b", "kWh/yr"),
            (r"MWh\s*(?:/|per)\s*(?:yr|year|annum|a)\b", "MWh/yr"),
        ),
        needs_geo=True,
    ),
    "eta": ParamSpec(
        label="Fractional energy-intensity reduction of the efficient device",
        queries=(
            "{subject} energy savings percent more efficient model",
            "{subject} efficiency improvement energy reduction",
        ),
        keywords=(
            "energy saving",
            "energy savings",
            "save energy",
            "saves energy",
            "less energy",
            "lower energy",
            "reduce energy",
            "reduces energy",
            "reduction in energy",
            "energy reduction",
            "energy consumption by",
        ),  # fmt: skip
        units=((r"%|percent|per\s?cent", "percent"),),
        ambiguity_note="'X% more efficient' is not the same as 'X% less energy'; only energy-reduction phrasing is "
        "accepted, but check the baseline it is measured against.",
    ),
    "phi": ParamSpec(
        label="Energy share of the marginal cost of the service",
        queries=(
            "{subject} electricity cost share of total cost of ownership",
            "{subject} running cost share energy cost",
        ),
        keywords=(
            "share of",
            "of the total cost",
            "of total cost",
            "cost of ownership",
            "running cost",
            "operating cost",
        ),
        units=((r"%|percent|per\s?cent", "percent"),),
        ambiguity_note="The model's phi is the energy-related share of the per-unit-service marginal cost.",
    ),
}

_GEO_CODES: dict[str, tuple[str, str]] = {  # lower-case name -> (ISO code, label); registry geographies + common ones
    "india": ("IN", "India"), "united states": ("US", "United States"), "usa": ("US", "United States"),
    "u.s.": ("US", "United States"), "america": ("US", "United States"), "germany": ("DE", "Germany"),
    "france": ("FR", "France"), "china": ("CN", "China"), "japan": ("JP", "Japan"), "brazil": ("BR", "Brazil"),
    "indonesia": ("ID", "Indonesia"), "mexico": ("MX", "Mexico"), "south africa": ("ZA", "South Africa"),
    "united kingdom": ("GB", "United Kingdom"), "uk": ("GB", "United Kingdom"), "britain": ("GB", "United Kingdom"),
    "australia": ("AU", "Australia"), "canada": ("CA", "Canada"), "italy": ("IT", "Italy"), "spain": ("ES", "Spain"),
    "vietnam": ("VN", "Vietnam"), "thailand": ("TH", "Thailand"), "pakistan": ("PK", "Pakistan"),
    "bangladesh": ("BD", "Bangladesh"), "nigeria": ("NG", "Nigeria"), "egypt": ("EG", "Egypt"),
    "turkey": ("TR", "Türkiye"), "saudi arabia": ("SA", "Saudi Arabia"), "south korea": ("KR", "South Korea"),
    "philippines": ("PH", "Philippines"), "russia": ("RU", "Russia"), "poland": ("PL", "Poland"),
    "world": ("GLOBAL", "World"), "global": ("GLOBAL", "World"), "worldwide": ("GLOBAL", "World"),
}  # fmt: skip

_GENERIC_WORDS = frozenset(
    ["a", "an", "the", "of", "in", "on", "for", "to", "by", "with", "and", "or", "is", "are", "was", "were", "be", "been", "can", "could", "does", "do", "did", "will", "would", "should", "may", "might", "it", "its", "this", "that", "these", "those", "what", "which", "who", "how", "why", "when", "where", "whether", "if", "than", "then", "there", "their", "from", "at", "as", "improving", "improve", "improved", "improvement", "improvements", "increase", "increases", "increasing", "decrease", "decreases", "reduce", "reducing", "reduction", "lead", "leads", "cause", "causes", "make", "makes", "efficiency", "efficient", "energy", "electricity", "related", "total", "emissions", "emission", "carbon", "greenhouse", "gas", "gases", "overall", "net", "absolute", "really", "actually", "ever", "more", "less", "higher", "lower", "cut", "cuts", "into", "out", "up", "down", "about", "over", "under", "across", "between", "per", "using", "use", "used"]
)

_RELEVANCE_HINTS = (
    "efficien", "rebound", "backfire", "appliance", "device", "replace", "retrofit", "air condition", "refrigerator",
    "heater", "heat pump", "lighting", "led", "vehicle", "boiler", "cooling", "heating", "consumption", "electricity",
)  # fmt: skip


def parse_question(question: str) -> dict[str, Any]:
    """Heuristic scope extraction: geography code (+label) and a short subject phrase for search queries."""
    q = re.sub(r"\s+", " ", question.strip())
    low = q.lower()
    geo_code, geo_label, geo_found = "GLOBAL", "World", False
    for name in sorted(_GEO_CODES, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", low):
            geo_code, geo_label = _GEO_CODES[name]
            geo_found = True
            break
    geo_words = {w for n in _GEO_CODES for w in n.split()}
    tokens = [
        t
        for t in re.findall(r"[a-z][a-z\-]+", low)
        if not all(part in _GENERIC_WORDS or part in geo_words for part in t.split("-") if part)
    ]
    seen: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.append(t)
    subject = " ".join(seen[:6])
    fit_ok = any(h in low for h in _RELEVANCE_HINTS)
    return {
        "geography_code": geo_code,
        "geography_label": geo_label,
        "geography_recognised": geo_found,
        "subject": subject,
        "model_fit_ok": fit_ok,
    }


# ------------------------------------------------------------------------------------------------ extraction


def _to_float(text: str) -> float:
    return float(text.replace(",", ""))


def _split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\[(])", text) if s.strip()]


def _in_bounds(model_spec: Any, vid: str, canonical_value: float) -> bool:
    b = model_spec.input_by_id(vid).physical_bounds
    if b is None:
        return True
    if b.min is not None and (canonical_value < b.min or (canonical_value == b.min and not b.inclusive_min)):
        return False
    return not (b.max is not None and (canonical_value > b.max or (canonical_value == b.max and not b.inclusive_max)))


def _quantity_matches(sentence: str, spec: ParamSpec) -> list[tuple[float, float | None, str, str]]:
    """(low, high|None, unit, matched_text) for 'NUM [- NUM] UNIT' occurrences."""
    out = []
    for unit_re, unit in spec.units:
        for m in re.finditer(rf"(?P<a>{_NUM})(?:{_RANGE_SEP}(?P<b>{_NUM}))?\s*(?:{unit_re})", sentence, re.IGNORECASE):
            a = _to_float(m.group("a"))
            b = _to_float(m.group("b")) if m.group("b") else None
            out.append((a, b, unit, m.group(0)))
    return out


def _elasticity_matches(sentence: str) -> list[tuple[float, float | None, str, str]]:
    out = []
    pat = (
        r"elasticit\w*[^0-9\-−–]{0,90}?(?P<a>[-−–]?\s?\d+(?:\.\d+)?)"
        r"(?:\s*(?:to|and|–|—|-)\s*(?P<b>[-−–]?\s?\d+(?:\.\d+)?))?"
    )
    for m in re.finditer(pat, sentence, re.IGNORECASE):
        a_txt, b_txt = m.group("a"), m.group("b")
        tail = sentence[m.end() : m.end() + 2]
        if tail.lstrip().startswith("%"):
            continue
        a = abs(_to_float(re.sub(r"[-−–\s]", "", a_txt)))
        b = abs(_to_float(re.sub(r"[-−–\s]", "", b_txt))) if b_txt else None
        if a >= 1900 or (b is not None and b >= 1900):  # a year, not an elasticity
            continue
        out.append((a, b, "dimensionless", m.group(0)))
    return out


def extract_from_hit(
    vid: str, spec: ParamSpec, hit: SearchHit, model_spec: Any, canon_unit: str
) -> list[dict[str, Any]]:
    """Candidates found in one search hit. The matched number text is always inside the returned ``quote``."""
    url = validate_source_url(hit.url)
    if url is None:
        return []
    out: list[dict[str, Any]] = []
    for sent in _split_sentences(f"{hit.title}. {hit.snippet}"):
        low = sent.lower()
        kw = next((k for k in spec.keywords if k in low), None)
        if kw is None:
            continue
        matches = _elasticity_matches(sent) if spec.kind == "elasticity" else _quantity_matches(sent, spec)
        for a, b, unit, matched in matches[:2]:
            try:
                ca = to_canonical(a, unit, canon_unit, what=vid)
                cb = None if b is None else to_canonical(b, unit, canon_unit, what=vid)
            except UnitError:
                continue
            lo, hi = (ca, cb) if cb is None or ca <= cb else (cb, ca)
            if not _in_bounds(model_spec, vid, lo) or not _in_bounds(model_spec, vid, hi if hi is not None else lo):
                continue
            is_range = b is not None and cb is not None and cb != ca
            rng = [min(a, b), max(a, b)] if is_range and b is not None else None
            out.append(
                {
                    # the number(s) exactly as written in the quote; for a range the base value is DERIVED (midpoint)
                    # and labelled as such: the source states only the range
                    "value": None if is_range else a,
                    "unit": unit,
                    "range": rng,
                    "base_value": (rng[0] + rng[1]) / 2 if rng else a,
                    "base_value_basis": "midpoint of the sourced range (computed here; the source states only the range)"
                    if rng
                    else "value stated in the source",
                    "canonical_low": lo,
                    "canonical_high": hi if is_range else lo,
                    "kind": "range" if is_range else "value",
                    "source": {
                        "title": hit.title.strip()[:300] or url,
                        "url": url,
                        "publisher": (urlsplit(url).hostname or "").removeprefix("www."),
                        "published": (hit.published or None),
                        "tier": source_tier(url),
                    },
                    "quote": sent[:400],
                    "matched_text": matched.strip(),
                    "why_relevant": (
                        f"The sentence mentions '{kw}' and states “{matched.strip()}”. "
                        f"Source type: {'government / academic / recognised research body (by domain)' if source_tier(url) == 'authoritative' else 'general web source (not recognised as authoritative by domain)'}."
                        + (f" {spec.ambiguity_note}" if spec.ambiguity_note else "")
                    ),
                }
            )
    return out


def _dedupe_and_rank(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, float, float]] = set()
    uniq = []
    for c in sorted(cands, key=lambda c: (c["source"]["tier"] != "authoritative", c["kind"] != "range")):
        key = (c["source"]["url"], round(c["canonical_low"], 9), round(c["canonical_high"], 9))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq[:MAX_CANDIDATES_PER_PARAMETER]


# ------------------------------------------------------------------------------------------------ registry candidates


def registry_candidates(
    registry: Any, vid: str, geography_code: str, canon_unit: str, model_spec: Any
) -> list[dict[str, Any]]:
    """Candidates that come from the LOCAL dataset registry (real, hashed, pinned releases): same status vocabulary as
    the engine uses (e.g. ``sourced_unreviewed``). Only exact-geography records match (no silent fallback)."""
    out = []
    if registry is None:
        return out
    for e in registry.entries(variable_id=vid, usable_only=True):
        if e.dataset_id.startswith("web-review-"):
            continue
        codes = {c.upper() for c in e.record.geography_codes}
        if geography_code.upper() not in codes:
            continue
        ev = e.record.evidence
        try:
            canon = to_canonical(ev.value, ev.unit, canon_unit, what=vid)
        except UnitError:
            continue
        rng = None
        if getattr(ev.uncertainty, "kind", None) == "interval":
            rng = [ev.uncertainty.low, ev.uncertainty.high]  # type: ignore[union-attr]
        src = ev.source
        url = validate_source_url(src.url) if src and src.url else None
        out.append(
            {
                "value": ev.value,
                "unit": ev.unit,
                "range": rng,
                "base_value": ev.value,
                "base_canonical": canon,
                "base_value_basis": "value stated in the dataset record",
                "canonical_low": canon if rng is None else to_canonical(rng[0], ev.unit, canon_unit, what=vid),
                "canonical_high": canon if rng is None else to_canonical(rng[1], ev.unit, canon_unit, what=vid),
                "kind": "range" if rng else "value",
                "source": {
                    "title": (src.citation if src and src.citation else e.evidence_id)[:300],
                    "url": url,
                    "publisher": "local dataset registry",
                    "published": None,
                    "tier": "authoritative",
                },
                "quote": (ev.note or ev.description or "")[:400],
                "matched_text": f"{ev.value} {ev.unit}",
                "why_relevant": f"Exact-geography record in the pinned local dataset release {e.evidence_id} "
                f"(status {e.status.value}). {ev.note or ''}".strip(),
                "origin": "registry",
                "evidence_status": e.status.value,
                "evidence_id": e.evidence_id,
                "dataset_id": e.dataset_id,
                "dataset_version": e.version,
                "record_id": e.record.record_id,
                "extraction": "pinned dataset record (not extracted from the web)",
            }
        )
    return out


# ------------------------------------------------------------------------------------------------ report


def _midpoint(c: dict[str, Any]) -> float:
    return (c["canonical_low"] + c["canonical_high"]) / 2


def classify_candidates(cands: list[dict[str, Any]]) -> tuple[str, str]:
    """(status, message). Conflict = intervals do not overlap AND are more than 10% apart (relative to the larger
    midpoint). The candidates are never merged, averaged or reduced to one."""
    if not cands:
        return "missing", EVIDENCE_INSUFFICIENT
    if len(cands) == 1:
        return ("sourced_range" if cands[0]["kind"] == "range" else "sourced"), (
            "One source found." + (" It gives a range." if cands[0]["kind"] == "range" else "")
        )
    for i, a in enumerate(cands):
        for b in cands[i + 1 :]:
            gap = max(a["canonical_low"], b["canonical_low"]) - min(a["canonical_high"], b["canonical_high"])
            scale = max(abs(_midpoint(a)), abs(_midpoint(b)), 1e-12)
            if gap > 0 and gap / scale > 0.10:
                return "conflicting", "CONFLICTING EVIDENCE: sources disagree. All are shown; choose one or override."
    return "multiple_sources", "Several sources found and their values are consistent (overlap or within 10%)."


@dataclass
class ResearchContext:
    provider: SearchProvider | None
    unavailable_reason: str | None = None
    registry: Any = None
    mission_template: dict[str, Any] = field(default_factory=dict)


def required_parameters() -> list[dict[str, Any]]:
    """The inputs the existing engine actually uses under the canonical options (read from the model, not hard-coded)."""
    model = get_model(MODEL_REF)
    opts = model.resolve_options(dict(MODEL_OPTIONS))
    out = []
    for vid in sorted(model.required_inputs(opts)):
        iv = model.spec.input_by_id(vid)
        spec = PARAM_SPECS.get(vid)
        out.append(
            {
                "id": vid,
                "label": spec.label if spec else iv.name,
                "unit": iv.canonical_unit,
                "description": iv.description,
            }
        )
    return out


def _load_illustrative_defaults(mission_file: Path) -> dict[str, dict[str, Any]]:
    """The engine's own placeholders (single source of truth: the canonical mission file)."""
    data = tomllib.loads(mission_file.read_text(encoding="utf-8"))
    out = {}
    for vid, b in data.get("bindings", {}).items():
        unc = b.get("uncertainty", {})
        out[vid] = {
            "value": b["value"],
            "unit": b["unit"],
            "interval": [unc["low"], unc["high"]] if unc.get("kind") == "interval" else None,
            "note": "ILLUSTRATIVE — not evidence (placeholder from the canonical demo mission)",
        }
    return out


def research_question(
    question: str,
    *,
    provider: SearchProvider | None,
    unavailable_reason: str | None,
    registry: Any,
    mission_file: Path,
    now: Callable[[], _dt.datetime] | None = None,
) -> dict[str, Any]:
    """question -> required model parameters -> candidate values / ranges / sources / evidence status.

    ``provider=None`` means web research is unavailable: the report says so, searches nothing, and only local
    registry evidence (if any) is shown."""
    now = now or (lambda: _dt.datetime.now(_dt.UTC))
    retrieved_on = now().date().isoformat()
    model = get_model(MODEL_REF)
    opts = model.resolve_options(dict(MODEL_OPTIONS))
    required = sorted(model.required_inputs(opts))
    scope = parse_question(question)
    defaults = _load_illustrative_defaults(mission_file)

    unique_queries: list[str] = []
    queries: dict[str, list[str]] = {}
    for vid in required:
        spec = PARAM_SPECS.get(vid)
        qs: list[str] = []
        if spec is not None:
            for t in spec.queries:
                geo = scope["geography_label"] if scope["geography_recognised"] else ""
                q = re.sub(r"\s+", " ", t.format(subject=scope["subject"], geo=geo)).strip()
                qs.append(q)
        queries[vid] = qs
        unique_queries += [q for q in qs if q not in unique_queries]

    hits_by_query: dict[str, list[SearchHit]] = {}
    errors_by_query: dict[str, str] = {}
    if provider is not None and unique_queries:

        def run(q: str) -> tuple[str, list[SearchHit] | str]:
            try:
                return q, provider.search(q)
            except SearchUnavailable as exc:
                return q, str(exc)

        with ThreadPoolExecutor(max_workers=8) as pool:
            for q, res in pool.map(run, unique_queries):
                if isinstance(res, str):
                    errors_by_query[q] = res
                else:
                    hits_by_query[q] = res
    hits_by_param: dict[str, list[SearchHit]] = {
        v: [h for q in queries[v] for h in hits_by_query.get(q, [])] for v in required
    }
    errors_by_param: dict[str, str] = {
        v: next(errors_by_query[q] for q in queries[v] if q in errors_by_query)
        for v in required
        if any(q in errors_by_query for q in queries[v])
    }

    params = []
    for vid in required:
        iv = model.spec.input_by_id(vid)
        spec = PARAM_SPECS.get(vid)
        cands: list[dict[str, Any]] = []
        if provider is not None and spec is not None:
            raw: list[dict[str, Any]] = []
            for hit in hits_by_param[vid]:
                raw += extract_from_hit(vid, spec, hit, model.spec, iv.canonical_unit)
            for c in _dedupe_and_rank(raw):
                c["base_canonical"] = to_canonical(c["base_value"], c["unit"], iv.canonical_unit, what=vid)
                c.update(
                    origin="web",
                    evidence_status="sourced_unreviewed",
                    extraction=EXTRACTION_CAVEAT,
                )
                c["source"]["retrieved_on"] = retrieved_on
                cands.append(c)
        reg = registry_candidates(registry, vid, scope["geography_code"], iv.canonical_unit, model.spec)
        cands = reg + cands
        for i, c in enumerate(cands, start=1):
            c["candidate_id"] = f"{vid}.{c['origin']}{i}"
        if provider is None and not cands:
            status, msg = "not_researched", "Web research was not performed (unavailable). No value is proposed."
        elif vid in errors_by_param and not cands:
            status, msg = (
                "search_failed",
                f"The web search for this parameter failed ({errors_by_param[vid]}). No value is proposed.",
            )
        else:
            status, msg = classify_candidates(cands)
            if provider is None and cands:
                msg += " (Web research unavailable: only local registry evidence is shown.)"
        d = defaults.get(vid)
        params.append(
            {
                "id": vid,
                "label": spec.label if spec else iv.name,
                "description": iv.description,
                "unit": iv.canonical_unit,
                "queries": queries[vid],
                "candidates": cands,
                "status": status,
                "message": msg,
                "illustrative_default": d,
            }
        )

    if provider is None:
        web = {"available": False, "provider": None, "message": unavailable_reason or "Web research unavailable."}
    else:
        web = {"available": True, "provider": provider.name, "message": f"Searched the web via {provider.name}."}
    return {
        "research_id": secrets.token_hex(8),
        "question": question,
        "created_utc": now().isoformat(timespec="seconds"),
        "model_ref": MODEL_REF,
        "model_options": dict(MODEL_OPTIONS),
        "scope": {
            "geography_code": scope["geography_code"],
            "geography_label": scope["geography_label"],
            "geography_recognised": scope["geography_recognised"],
            "period": "2024",
            "subject_keywords": scope["subject"],
        },
        "model_fit": {
            "ok": scope["model_fit_ok"],
            "note": "The engine runs the efficiency-rebound model (an efficiency gain with direct rebound and "
            "embodied emissions). Your question sets the geography and wording of the search; it does not change "
            "the model."
            + (
                ""
                if scope["model_fit_ok"]
                else " This question does not obviously concern an efficiency improvement; check that the rebound model is the right tool."
            ),
        },
        "web_research": web,
        "parameters": params,
        "disclaimer": "Values below are CANDIDATES. Web candidates are 'sourced_unreviewed': a citation exists, nobody has "
        "checked it against the value. Nothing runs until you confirm.",
    }
