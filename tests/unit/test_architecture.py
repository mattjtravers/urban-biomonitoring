"""Package layout and import boundaries, checked statically over src/urbanbio/."""

from __future__ import annotations

import ast
import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
PACKAGE = SRC / "urbanbio"

STAGES = ("ingest", "screen", "detect", "validate", "curate", "publish")
SHARED = ("models", "storage", "provenance", "config")

# config-cli § Package Layout: top-level modules and packages.
LAYOUT = (
    "urbanbio.cli",
    "urbanbio.config",
    "urbanbio.errors",
    "urbanbio.logging",
    "urbanbio.models",
    "urbanbio.storage",
    "urbanbio.provenance",
    "urbanbio.ingest",
    "urbanbio.ingest.adapters",
    "urbanbio.screen",
    "urbanbio.detect",
    "urbanbio.validate",
    "urbanbio.curate",
    "urbanbio.publish",
)


def module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imports(path: Path) -> Iterator[str]:
    """Absolute names of the urbanbio modules a file imports, relative imports resolved."""
    name = module_name(path)
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - node.level + 1]
                target = ".".join([*base, node.module] if node.module else base)
            else:
                target = node.module or ""
            yield target
            # `from urbanbio import ingest` imports the ingest package.
            yield from (f"{target}.{a.name}" for a in node.names)


def top_package(module: str) -> str | None:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[0] == "urbanbio" else None


def source_files() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


# @spec CLI-PKG-001
@pytest.mark.parametrize("module", LAYOUT)
def test_package_layout(module: str) -> None:
    importlib.import_module(module)


# @spec CLI-PKG-002
def test_stage_packages_do_not_import_each_other() -> None:
    violations = []
    for path in source_files():
        owner = top_package(module_name(path))
        for target in imports(path):
            imported = top_package(target)
            if imported not in STAGES or imported == owner:
                continue
            if owner in STAGES:
                violations.append(f"{module_name(path)} imports {target}")
            elif owner in SHARED or owner in ("errors", "logging"):
                sanctioned = module_name(path) == "urbanbio.storage.archive" and (
                    target == "urbanbio.screen.mask" or target.startswith("urbanbio.screen.mask.")
                )
                if not sanctioned:
                    violations.append(f"{module_name(path)} imports {target}")
    assert violations == []


# @spec CLI-PKG-002
def test_cli_imports_only_stage_entry_points() -> None:
    """cli.py reaches each stage through the stage package itself, never its submodules."""
    violations = []
    for target in imports(PACKAGE / "cli.py"):
        parts = target.split(".")
        if len(parts) < 3 or top_package(target) not in STAGES:
            continue
        stage, name = parts[1], parts[2]
        if (PACKAGE / stage / f"{name}.py").exists() or (PACKAGE / stage / name).is_dir():
            violations.append(target)
    assert violations == []


def _catches_privacy_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    broad = {"PrivacyError", "UrbanbioError", "Exception", "BaseException"}
    return any(
        (isinstance(n, ast.Name) and n.id in broad)
        or (isinstance(n, ast.Attribute) and n.attr in broad)
        for n in names
    )


def _reraises_privacy_first(handlers: list[ast.ExceptHandler], index: int) -> bool:
    """An earlier `except PrivacyError: raise` lets later broad handlers through."""
    for handler in handlers[:index]:
        t = handler.type
        named = (isinstance(t, ast.Name) and t.id == "PrivacyError") or (
            isinstance(t, ast.Attribute) and t.attr == "PrivacyError"
        )
        if named and len(handler.body) == 1 and isinstance(handler.body[0], ast.Raise):
            return handler.body[0].exc is None
    return False


# @spec CLI-ERR-006
def test_stage_code_never_catches_privacy_error() -> None:
    violations = []
    for path in source_files():
        if top_package(module_name(path)) not in STAGES:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Try | ast.TryStar):
                continue
            for i, handler in enumerate(node.handlers):
                is_bare_reraise = (
                    len(handler.body) == 1
                    and isinstance(handler.body[0], ast.Raise)
                    and handler.body[0].exc is None
                )
                if (
                    _catches_privacy_error(handler)
                    and not is_bare_reraise
                    and not _reraises_privacy_first(node.handlers, i)
                ):
                    violations.append(f"{module_name(path)}:{handler.lineno}")
    assert violations == []
