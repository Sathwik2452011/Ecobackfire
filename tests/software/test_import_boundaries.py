"""Layer rules from docs/architecture.md section 4, enforced with ast (no extra dependency)."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "ecobackfire"
LAYER = {
    "errors": 0,
    "contracts": 0,
    "units": 0,
    "core": 1,
    "models": 2,
    "evidence": 2,
    "datasets": 2,
    "experiments": 3,
    "verification": 4,
    "discovery": 4,
    "reporting": 5,
    "ai": 6,
    "cli": 7,
    "api": 7,
    "mission": 7,
    "__main__": 7,
}
FORBIDDEN_EXTERNAL = {"requests", "urllib", "http", "socket", "httpx", "aiohttp", "anthropic", "openai"}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def _top(rel: Path) -> str:
    parts = rel.with_suffix("").parts
    return parts[0]


def test_imports_point_downward_only() -> None:
    bad = []
    for py in SRC.rglob("*.py"):
        rel = py.relative_to(SRC)
        if rel.name == "__init__.py" and len(rel.parts) == 1:
            continue
        mine = _top(rel)
        for name in _imports(py):
            if name.split(".")[0] != "ecobackfire":
                continue
            parts = name.split(".")
            target = parts[1] if len(parts) > 1 else None
            if target is None or target not in LAYER:
                continue
            if LAYER[target] > LAYER[mine]:
                bad.append(f"{rel}: layer {LAYER[mine]} ({mine}) imports {name} (layer {LAYER[target]})")
    assert not bad, "\n".join(bad)


def test_experiments_do_not_import_verification() -> None:
    for py in (SRC / "experiments").rglob("*.py"):
        assert not any(n.startswith("ecobackfire.verification") for n in _imports(py)), py


def test_reference_implementations_are_independent() -> None:
    for py in (SRC / "verification" / "reference").glob("*.py"):
        offenders = [n for n in _imports(py) if n.startswith("ecobackfire")]
        assert not offenders, f"{py.name} must not import production code: {offenders}"


def test_scientific_core_has_no_network_or_provider_imports() -> None:
    for py in SRC.rglob("*.py"):
        for name in _imports(py):
            assert name.split(".")[0] not in FORBIDDEN_EXTERNAL, f"{py}: imports {name}"
            assert not name.startswith("ecobackfire.ai"), f"{py}: core must not import ai"
