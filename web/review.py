"""Reviewed parameters -> the EXISTING EcoBackfire mission + dataset-registry contracts.

This module contains no calculation. It only translates what the user reviewed into the objects the engine already
understands, using the engine's own provenance vocabulary:

  user accepted a WEB candidate      -> a ``sourced`` record in a run-scoped dataset release (status
                                         ``sourced_unreviewed``) + a mission evidence requirement pinned to it. All
                                         other candidates for that input go into the same release, so the engine's own
                                         evidence challenges see the alternative sources (source-dependence).
  user accepted a REGISTRY candidate -> an evidence requirement on the existing pinned release (e.g. OWID/Ember ``f``)
  user OVERRODE a value              -> a ``user_supplied`` binding (the engine reports it as an assumption, never as
                                         evidence), whose note carries the researched value it replaced
  user chose the illustrative default-> the engine's own ``illustrative`` binding (unchanged placeholder)
  user marked a parameter unavailable-> the run is refused here (no value is invented to fill the gap)

The engine itself requires at least one resolved evidence record (nothing documented => nothing to challenge the
candidates with). That rule is NOT weakened: if the user overrides every sourced input, the run is refused.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ecobackfire.contracts.mission import ResearchMission
from ecobackfire.core.hashing import canonical_json, sha256_text
from ecobackfire.datasets.registry import DatasetRegistry
from ecobackfire.datasets.schema import DatasetRelease
from ecobackfire.errors import EcoBackfireError
from ecobackfire.mission.loader import load_mission
from ecobackfire.models.registry import get_model
from ecobackfire.units import to_canonical
from web.research import MODEL_OPTIONS, MODEL_REF, validate_source_url

SECRET_ENV = "ECOBACKFIRE_SESSION_SECRET"
_PROCESS_SECRET = secrets.token_bytes(32)
PERIOD = "2024"


class RunBlocked(Exception):
    """The reviewed parameters cannot be turned into an engine run. ``messages`` are user-readable; nothing ran."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = messages


# ------------------------------------------------------------------------------------------------ signed research


def _secret() -> bytes:
    v = os.environ.get(SECRET_ENV, "")
    return v.encode("utf-8") if v else _PROCESS_SECRET


def _normalize(x: Any) -> Any:
    """JSON round-trips through a browser turn 10.0 into 10: make every number a float before hashing."""
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, dict):
        return {k: _normalize(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_normalize(v) for v in x]
    return x


def sign_report(report: dict[str, Any]) -> str:
    """HMAC over the canonical report. It stops a client from inventing 'sourced' candidates: only values the server
    produced in /api/research can be accepted as researched; anything else must be an explicit user override."""
    return hmac.new(_secret(), canonical_json(_normalize(report)).encode("utf-8"), hashlib.sha256).hexdigest()


def verify_report(report: dict[str, Any], signature: str) -> bool:
    try:
        return hmac.compare_digest(sign_report(report), signature)
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------------------------------------------ request models


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parameter: str
    action: Literal["accept", "override", "illustrative", "unavailable"]
    candidate_id: str | None = None
    #: override only (canonical unit of the parameter)
    value: float | None = None
    low: float | None = None
    high: float | None = None
    note: str | None = Field(default=None, max_length=300)

    @field_validator("value", "low", "high")
    @classmethod
    def _finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("numbers must be finite")
        return v


# ------------------------------------------------------------------------------------------------ helpers


def _in_bounds(model_spec: Any, vid: str, x: float) -> bool:
    b = model_spec.input_by_id(vid).physical_bounds
    if b is None:
        return True
    if b.min is not None and (x < b.min or (x == b.min and not b.inclusive_min)):
        return False
    return not (b.max is not None and (x > b.max or (x == b.max and not b.inclusive_max)))


def _fmt(x: float) -> str:
    return f"{x:.6g}"


@dataclass
class ParameterOutcome:
    """What the run used for ONE parameter and where it came from (kept with the result as provenance)."""

    parameter: str
    unit: str
    basis: Literal["web_sourced", "registry_sourced", "user_override", "illustrative_default"]
    used_value: float
    used_range: list[float] | None
    researched_status: str
    sources: list[dict[str, Any]] = field(default_factory=list)  # the source(s) behind the used value
    alternatives: list[dict[str, Any]] = field(default_factory=list)  # other researched candidates (kept visible)
    overrides: dict[str, Any] | None = None  # original researched value(s) and the override
    note: str = ""
    engine_ref: str | None = None  # evidence id (qualified) when the engine resolved it from a record

    def to_json(self) -> dict[str, Any]:
        return {
            "parameter": self.parameter,
            "unit": self.unit,
            "basis": self.basis,
            "used_value": self.used_value,
            "used_range": self.used_range,
            "researched_status": self.researched_status,
            "sources": self.sources,
            "alternatives": self.alternatives,
            "user_override": self.overrides,
            "note": self.note,
            "engine_evidence_id": self.engine_ref,
        }


@dataclass
class BuiltRun:
    key: str
    mission: ResearchMission
    registry_dir: Path
    outcomes: list[ParameterOutcome]
    review_doc: dict[str, Any]


def _cand_brief(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": c["candidate_id"],
        "value": c.get("value"),
        "range": c.get("range"),
        "unit": c["unit"],
        "source_title": c["source"]["title"],
        "source_url": c["source"].get("url"),
        "origin": c["origin"],
        "evidence_status": c.get("evidence_status"),
    }


def _record_id(candidate_id: str) -> str:
    return candidate_id.replace(".", "_")


def _web_record(vid: str, c: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    src = c["source"]
    host = src.get("publisher") or "web"
    url = validate_source_url(src.get("url"))
    if url is None:
        raise RunBlocked(
            [f"{vid}: candidate {c['candidate_id']} has no valid http(s) source URL; it cannot be recorded as sourced"]
        )
    sid = f"web:{host}#{hashlib.sha256(url.encode()).hexdigest()[:10]}"
    ev: dict[str, Any] = {
        "record_id": _record_id(c["candidate_id"]),
        "variable_id": vid,
        "description": f"Web-research candidate for {vid}: {c.get('matched_text', '')}"[:300],
        "value": float(c["base_value"]),
        "unit": c["unit"],
        "provenance_class": "sourced",
        "source": {
            "source_id": sid,
            "citation": f"{src['title']} ({host})"[:500],
            "url": url,
            "locator": f"search-result text: “{c['quote']}”"[:600],
            "retrieved_on": src.get("retrieved_on"),
        },
        "scope": {
            "geography": report["scope"]["geography_label"],
            "period": f"not verified against the source (matched by the web layer to the mission period {PERIOD})",
            "system_description": report["scope"].get("subject_keywords") or None,
        },
        "note": (
            f"Found by automatic web search + keyword/unit extraction; NOT reviewed against the full source. "
            f"Base value basis: {c['base_value_basis']}. {c.get('why_relevant', '')}"
        )[:900],
    }
    if c.get("range"):
        lo, hi = c["range"]
        ev["uncertainty"] = {"kind": "interval", "low": lo, "high": hi}
    return {
        "evidence": ev,
        "geography_codes": [report["scope"]["geography_code"]],
        "temporal": {"start": PERIOD, "end": PERIOD},
        "metadata": {"origin": "web_search_candidate", "candidate_id": c["candidate_id"]},
    }


# ------------------------------------------------------------------------------------------------ the builder


def build_run(
    report: dict[str, Any],
    decisions: list[Decision],
    *,
    mission_file: Path,
    releases_dir: Path,
    work_root: Path,
    today: str,
) -> BuiltRun:
    model = get_model(MODEL_REF)
    required = sorted(model.required_inputs(model.resolve_options(dict(MODEL_OPTIONS))))
    params = {p["id"]: p for p in report["parameters"]}
    if sorted(params) != required:
        raise RunBlocked(["the research report does not list exactly the parameters the engine requires"])

    # ---- one decision per required parameter, validated against what the server researched
    by_param: dict[str, Decision] = {}
    problems: list[str] = []
    for d in decisions:
        if d.parameter not in params:
            problems.append(f"unknown parameter {d.parameter!r}")
        elif d.parameter in by_param:
            problems.append(f"{d.parameter}: more than one decision")
        else:
            by_param[d.parameter] = d
    for vid in required:
        if vid not in by_param:
            problems.append(f"{vid}: no decision (accept, override, illustrative default, or unavailable)")
    if problems:
        raise RunBlocked(problems)

    unavailable = [v for v in required if by_param[v].action == "unavailable"]
    if unavailable:
        raise RunBlocked(
            [
                f"{v}: marked unavailable. EcoBackfire will not invent a value; provide a value (override), accept a "
                "researched candidate, or explicitly choose the illustrative default."
                for v in unavailable
            ]
        )

    template = load_mission(mission_file).model_dump(mode="json")
    tmpl_bindings: dict[str, Any] = template["bindings"]
    outcomes: list[ParameterOutcome] = []
    bindings: dict[str, Any] = {}
    requirements: dict[str, Any] = {}
    web_records: list[dict[str, Any]] = []
    web_release_id = ""  # filled once the key is known; requirements are patched afterwards
    web_selects: dict[str, str] = {}
    base_of: dict[str, float] = {}  # canonical base value used for each non-illustrative input
    has_range: dict[str, bool] = {}  # does the supplied evidence/override carry a documented interval?

    for vid in required:
        p, d = params[vid], by_param[vid]
        unit = p["unit"]
        cands = {c["candidate_id"]: c for c in p["candidates"]}
        ill = tmpl_bindings.get(vid)
        alts_all = [_cand_brief(c) for c in p["candidates"]]

        if d.action == "illustrative":
            if ill is None:
                raise RunBlocked(
                    [
                        f"{vid}: the demo mission deliberately has no illustrative default for this input (it is evidence-only); accept a researched candidate or enter an override"
                    ]
                )
            bindings[vid] = ill
            outcomes.append(
                ParameterOutcome(
                    vid, ill["unit"], "illustrative_default", ill["value"],
                    [ill["uncertainty"]["low"], ill["uncertainty"]["high"]] if ill["uncertainty"].get("kind") == "interval" else None,
                    p["status"], alternatives=alts_all,
                    note="ILLUSTRATIVE — not evidence: the engine's own placeholder, chosen explicitly by the user.",
                )
            )  # fmt: skip

        elif d.action == "override":
            if d.value is None:
                raise RunBlocked([f"{vid}: an override needs a value (in {unit})"])
            if not _in_bounds(model.spec, vid, d.value):
                raise RunBlocked([f"{vid}: {d.value} is outside the physical bounds of the model input"])
            lo, hi = d.low, d.high
            if (lo is None) != (hi is None):
                raise RunBlocked([f"{vid}: give both ends of the range or neither"])
            if lo is not None and hi is not None and not (lo <= d.value <= hi):
                raise RunBlocked([f"{vid}: the override value must lie inside its range [{_fmt(lo)}, {_fmt(hi)}]"])
            if (
                lo is not None
                and hi is not None
                and not (_in_bounds(model.spec, vid, lo) and _in_bounds(model.spec, vid, hi))
            ):
                raise RunBlocked([f"{vid}: the range is outside the physical bounds of the model input"])
            original = [_cand_brief(c) for c in p["candidates"]]
            was = original[0] if original else None
            note = (
                "USER OVERRIDE (not sourced). Researched candidate(s) were: "
                + (
                    "; ".join(
                        f"{_fmt(c['value']) if c['value'] is not None else c['range']} {c['unit']} [{c['source_title'][:60]}]"
                        for c in original
                    )
                    or "none"
                )
                + (f". User note: {d.note}" if d.note else "")
            )[:900]
            base_of[vid], has_range[vid] = d.value, lo is not None
            bindings[vid] = {
                "value": d.value,
                "unit": unit,
                "provenance_class": "user_supplied",
                "note": note,
                "uncertainty": {"kind": "interval", "low": lo, "high": hi} if lo is not None else {"kind": "unknown"},
            }
            outcomes.append(
                ParameterOutcome(
                    vid, unit, "user_override", d.value, [lo, hi] if lo is not None else None, p["status"],
                    alternatives=alts_all,
                    overrides={"researched": original, "override_value": d.value, "override_range": [lo, hi] if lo is not None else None, "user_note": d.note, "original_first_candidate": was},
                    note="USER OVERRIDE: this is the user's assumption, not an externally sourced fact.",
                )
            )  # fmt: skip

        else:  # accept
            c = cands.get(d.candidate_id or "")
            if c is None:
                raise RunBlocked(
                    [
                        f"{vid}: accept needs the id of one of the researched candidates ({', '.join(cands) or 'none exist'})"
                    ]
                )
            canon_lo, canon_hi = c["canonical_low"], c["canonical_high"]
            base = to_canonical(c["base_value"], c["unit"], unit, what=vid)
            used_rng = [canon_lo, canon_hi] if c.get("range") else None
            base_of[vid], has_range[vid] = base, used_rng is not None
            # search-range carrier binding: the engine's own illustrative placeholder, labelled as such (see module
            # doc). Inputs the demo mission declares evidence-only (``f``) have none and need none.
            if ill is not None:
                carrier = dict(ill)
                carrier["note"] = (
                    "ILLUSTRATIVE — not evidence: search-range/fallback placeholder only. The base value of this "
                    f"input comes from the evidence record {c['candidate_id']} (see the plan)."
                )
                if used_rng:
                    carrier["uncertainty"] = {"kind": "interval", "low": used_rng[0], "high": used_rng[1]}
                else:
                    iv = ill["uncertainty"]
                    carrier["uncertainty"] = (
                        iv if iv.get("kind") == "interval" and iv["low"] <= base <= iv["high"] else {"kind": "unknown"}
                    )
                bindings[vid] = carrier
            alts = [_cand_brief(o) for o in p["candidates"] if o["candidate_id"] != c["candidate_id"]]
            if c["origin"] == "registry":
                requirements[vid] = {
                    "dataset_id": c["dataset_id"],
                    "version": c["dataset_version"],
                    "required": True,
                    "select": c["evidence_id"],
                    "note": "pinned local dataset record chosen by the user in the review step",
                }
                engine_ref = c["evidence_id"]
                basis: Literal["web_sourced", "registry_sourced"] = "registry_sourced"
            else:
                web_selects[vid] = _record_id(c["candidate_id"])
                for o in p["candidates"]:
                    if o["origin"] == "web":
                        web_records.append(_web_record(vid, o, report))
                engine_ref = None
                basis = "web_sourced"
            outcomes.append(
                ParameterOutcome(
                    vid, unit, basis, base, used_rng, p["status"],
                    sources=[{"title": c["source"]["title"], "url": c["source"].get("url"), "publisher": c["source"].get("publisher"), "quote": c.get("quote"), "base_value_basis": c.get("base_value_basis")}],
                    alternatives=alts, engine_ref=engine_ref,
                    note=("CONFLICTING evidence existed; the user explicitly chose this source. " if p["status"] == "conflicting" else "")
                    + ("Web candidate: sourced_unreviewed (citation not checked)." if basis == "web_sourced" else "Pinned local dataset record."),
                )
            )  # fmt: skip

    if not requirements and not web_selects:
        raise RunBlocked(
            [
                "No parameter is backed by a researched or registry source. The engine requires at least one sourced "
                "input to challenge its candidates with (nothing documented => nothing to challenge them with), so "
                "it will not run on overrides and illustrative defaults alone. Accept at least one sourced candidate."
            ]
        )

    # ---- stable run key from everything that determines the run
    key_src = {
        "question": report["question"],
        "scope": report["scope"],
        "bindings": bindings,
        "requirements": requirements,
        "web_selects": web_selects,
        "web_records": web_records,
    }
    key = sha256_text(canonical_json(key_src))[:12]
    web_release_id = f"web-review-{key}"
    for vid, rid in web_selects.items():
        requirements[vid] = {
            "dataset_id": web_release_id,
            "version": "1.0.0",
            "required": True,
            "select": f"{web_release_id}@1.0.0:{rid}",
            "note": "researched candidate chosen by the user in the review step",
        }
    for o in outcomes:
        if o.basis == "web_sourced":
            o.engine_ref = f"{web_release_id}@1.0.0:{web_selects[o.parameter]}"

    # ---- mission = the canonical mission with the reviewed inputs substituted (search/falsification/robustness/budget unchanged)
    m = dict(template)
    m["mission_id"] = f"web_{key}"
    m["title"] = f"Web-researched mission ({report['scope']['geography_label']}): {report['question']}"[:300]
    m["research_question"] = report["question"]
    m["scope"] = {"geography": report["scope"]["geography_code"], "period": PERIOD}
    m["bindings"] = bindings
    m["evidence_requirements"] = requirements
    # [[search]] entries WITHOUT explicit bounds take their range from the input's documented interval. For an input that
    # now comes from evidence or an override, that interval exists only if the source/user gave a range. Otherwise the
    # engine would refuse (it never invents bounds), so the web layer declares the demo's own ILLUSTRATIVE interval
    # as an explicit, labelled search range if it contains the base value, and drops the entry if it does not.
    search_notes: list[str] = []
    new_search = []
    for sp in template["search"]:
        vid = sp["variable_id"]
        if sp.get("low") is not None or vid not in base_of or has_range.get(vid):
            new_search.append(sp)
            continue
        iv = tmpl_bindings.get(vid, {}).get("uncertainty", {})
        if iv.get("kind") == "interval" and iv["low"] <= base_of[vid] <= iv["high"]:
            sp = dict(sp, low=iv["low"], high=iv["high"], provenance_class="illustrative",
                      basis="ILLUSTRATIVE — not evidence: demonstration search range (the supplied value has no documented range)")  # fmt: skip
            new_search.append(sp)
            search_notes.append(
                f"{vid}: searched over the demo's ILLUSTRATIVE interval [{_fmt(iv['low'])}, {_fmt(iv['high'])}] (the supplied value has no documented range)"
            )
        else:
            search_notes.append(
                f"{vid}: NOT searched: the supplied value has no documented range and lies outside the demo's illustrative interval; bounds are never invented"
            )
    m["search"] = new_search
    m["acknowledges_illustrative_inputs"] = True
    try:
        mission = ResearchMission.model_validate(m)
    except ValidationError as exc:
        raise RunBlocked([f"the reviewed parameters do not form a valid mission: {exc.errors()[0]['msg']}"]) from exc

    # ---- run-scoped registry: the existing releases + (if needed) one web-review release
    run_root = work_root / key
    registry_dir = run_root / "registry"
    if registry_dir.exists():
        shutil.rmtree(registry_dir)
    shutil.copytree(releases_dir, registry_dir)
    if web_records:
        release = {
            "schema_version": "1.0.0",
            "dataset_id": web_release_id,
            "release_version": "1.0.0",
            "title": "Web-research candidates reviewed by a user (run-scoped)",
            "description": "Candidates found by automatic web search + keyword/unit extraction for one EcoBackfire web "
            "run. Every record is a citation that nobody has checked against the value (status sourced_unreviewed).",
            "provider": "EcoBackfire web demo (automatic web search; user-reviewed selection)",
            "last_updated": today,
            "quality": "provisional",
            "changelog": "Created for one run from the research report the user reviewed.",
            "records": web_records,
        }
        try:
            DatasetRelease.model_validate(release)
        except ValidationError as exc:
            raise RunBlocked(
                [f"a researched candidate could not be recorded as evidence: {exc.errors()[0]['msg']}"]
            ) from exc
        rd = registry_dir / web_release_id / "1.0.0"
        rd.mkdir(parents=True)
        (rd / "dataset.json").write_text(json.dumps(release, indent=2, sort_keys=True), encoding="utf-8")
    try:
        DatasetRegistry.from_directory(registry_dir, strict=True)
    except EcoBackfireError as exc:
        raise RunBlocked([f"the run-scoped evidence registry is invalid: {exc}"]) from exc

    review_doc = {
        "schema": "ecobackfire-web-parameter-review/1",
        "note": "Written by the web layer. The engine's own provenance is in mission.json / plan.json / "
        "mission_result.json; this file adds what the web research found and what the user decided. It is NOT listed "
        "in the engine's manifest hashes.",
        "question": report["question"],
        "research_id": report["research_id"],
        "researched_utc": report["created_utc"],
        "web_research": report["web_research"],
        "scope": report["scope"],
        "run_key": key,
        "mission_id": mission.mission_id,
        "parameters": [o.to_json() for o in outcomes],
        "search_range_notes": search_notes,
        "researched_report": report["parameters"],
    }
    return BuiltRun(key=key, mission=mission, registry_dir=registry_dir, outcomes=outcomes, review_doc=review_doc)
