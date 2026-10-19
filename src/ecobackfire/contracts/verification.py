"""VerificationReport and ReproducibilityManifest (layer 0)."""

from __future__ import annotations

from typing import Literal

from pydantic import SerializerFunctionWrapHandler, model_serializer

from ecobackfire.contracts.common import Frozen
from ecobackfire.contracts.resolution import omit_empty

CheckStatus = Literal["passed", "failed", "inconclusive", "skipped", "not_applicable", "limits_claim"]
Label = Literal[
    "not_applicable",  # no candidate finding existed to label
    "unverified_hypothesis",
    "candidate",
    "conditional",
    "verified_internal",
    "refuted",
    "inconclusive",
]


class CheckResult(Frozen):
    check_id: str
    family: str
    required: bool
    status: CheckStatus
    summary: str
    details: dict[str, object]
    tolerance_used: str | None = None


class VerificationReport(Frozen):
    schema_version: str = "1.0.0"
    experiment_id: str
    config_sha256: str
    profile: str
    claim: str | None
    checks: list[CheckResult]
    unresolved_uncertainties: list[str]
    failed_attempts_to_falsify: list[str]  # attempts that did NOT refute the claim
    successful_falsifications: list[str]  # attempts that refuted or narrowed the claim
    recommended_label: Label
    label_justification: list[str]
    check_suite_version: str


class ReproducibilityManifest(Frozen):
    schema_version: str = "1.0.0"
    run_id: str
    created_at: str  # metadata; not part of any hash
    package_version: str
    git_commit: str | None
    git_dirty: bool | None
    python: str
    platform: str
    dependencies: dict[str, str]
    config_sha256: str
    config_file: str
    source_config_path: str | None
    model_ref: str
    dataset_release_ref: str | None
    seed: int | None
    artifact_hashes: dict[str, str]
    reproduce_command: str
    expected_tolerance: dict[str, float]
    #: SHA-256 of the evidence-resolution table (inputs, records, sources, conversions) and of the dataset
    #: registry it was resolved against. None (and omitted) for runs that used no evidence.
    evidence_resolution_sha256: str | None = None
    evidence_registry_sha256: str | None = None

    @model_serializer(mode="wrap")
    def _omit_unused_evidence(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        return omit_empty(handler(self), "evidence_resolution_sha256", "evidence_registry_sha256")
