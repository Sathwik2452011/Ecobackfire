"""Typed exceptions (layer 0: imports nothing from ecobackfire)."""


class EcoBackfireError(Exception):
    """Base class for all expected, user-facing errors."""


class UnitError(EcoBackfireError):
    """Unknown unit, or a unit incompatible with the variable's dimension."""


class PhysicalConstraintError(EcoBackfireError):
    """An input or state violates a declared physical/logical constraint."""


class MissingEvidenceError(EcoBackfireError):
    """A required input has no value. Missing data is never treated as zero."""


class EvidenceNotAvailableError(EcoBackfireError):
    """A binding claims sourced/measured evidence but no evidence store exists in this phase."""


class BoundaryMismatchError(EcoBackfireError):
    """Results with different system boundaries/functional units were compared implicitly."""


class NumericalError(EcoBackfireError):
    """Non-finite or unstable numerical result (overflow, NaN, failed bracketing)."""


class ConfigError(EcoBackfireError):
    """Invalid experiment configuration."""


class EvidenceValidationError(EcoBackfireError):
    """An evidence record or evidence store is malformed or makes an unsupported claim."""


class DatasetValidationError(EvidenceValidationError):
    """A dataset release file or registry directory is malformed or internally inconsistent."""


class DatasetNotFoundError(MissingEvidenceError):
    """A requested dataset or dataset version does not exist in the registry."""


class EvidenceConflictError(EcoBackfireError):
    """Several applicable evidence records disagree and the caller has not chosen one (never auto-resolved)."""
