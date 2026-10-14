"""The store's module boundary, checked over src/urbanbio/ (store LLD § Store Interface)."""

from __future__ import annotations

import ast
import importlib
import re

import pytest

from tests.unit.test_architecture import imports, module_name, source_files

STORAGE_MODULES = (
    "urbanbio.storage.store",
    "urbanbio.storage.s3",
    "urbanbio.storage.filesystem",
    "urbanbio.storage.paths",
    "urbanbio.storage.marker",
)
SDKS = ("boto3", "botocore", "s3transfer")
# A string that starts like a store key: one of the top-level prefixes, or the marker.
KEY_LITERAL = re.compile(r"^(?:(?:archive|retrievals|curated|clips|runs)/|\.urbanbio-store\.json$)")


def outside_storage(module: str) -> bool:
    return not (module == "urbanbio.storage" or module.startswith("urbanbio.storage."))


@pytest.mark.parametrize("module", STORAGE_MODULES)
def test_storage_modules(module: str) -> None:
    importlib.import_module(module)


# @spec STORE-API-001
def test_only_storage_imports_a_storage_sdk() -> None:
    violations = [
        f"{module_name(path)} imports {target}"
        for path in source_files()
        if outside_storage(module_name(path))
        for target in imports(path)
        if target.split(".")[0] in SDKS
    ]
    assert violations == []


# @spec STORE-API-001, STORE-KEY-001
def test_only_storage_formats_store_keys() -> None:
    violations = []
    for path in source_files():
        if not outside_storage(module_name(path)):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.JoinedStr):
                head = node.values[0] if node.values else None
                text = head.value if isinstance(head, ast.Constant) else ""
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            else:
                continue
            if isinstance(text, str) and KEY_LITERAL.match(text):
                violations.append(f"{module_name(path)}:{node.lineno}: {text!r}")
    assert violations == []
