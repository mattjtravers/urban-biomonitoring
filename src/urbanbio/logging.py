"""Console and JSON-lines file logging (config-cli LLD § Logging).

Every handler carries a privacy filter. It rewrites local `Path` arguments relative to the
configured paths registered with `set_path_roots`, and redacts private values. Redaction is a
backstop: code never puts private values into a message in the first place.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import sys
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

LOGGER_NAME = "urbanbio"
REDACTED = "[redacted]"
CONSOLE_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"

_REDACTED_VARS = ("URBANBIO_BUCKET", "URBANBIO_SITES")
_COORDINATE_LINE = re.compile(
    r"^\s*(?:latitude_exact|longitude_exact)\s*=\s*([-+]?[0-9][0-9_.eE+-]*)", re.MULTILINE
)
# Attributes every LogRecord has; anything else on a record came from `extra=`.
_RECORD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys() | {"message", "asctime"}
)

_path_roots: dict[str, Path] = {}


# @spec CLI-LOG-007
def set_path_roots(roots: Mapping[str, Path]) -> None:
    """Register the configured paths, by name, that local paths in log messages are shown under."""
    _path_roots.clear()
    _path_roots.update({name: Path(path).resolve() for name, path in roots.items()})


# @spec CLI-LOG-001
def configure_console(*, verbose: bool) -> None:
    """Log human-readable lines to stderr: INFO by default, DEBUG when verbose."""
    logger = _logger()
    for handler in [h for h in logger.handlers if getattr(h, "_urbanbio_console", False)]:
        logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler._urbanbio_console = True  # type: ignore[attr-defined]
    handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    handler.addFilter(_PrivacyFilter())
    logger.addHandler(handler)


# @spec CLI-LOG-002
@contextmanager
def run_log(state_dir: Path, run_id: str, stage: str) -> Iterator[Path]:
    """Write the run's JSON-lines log at `<state_dir>/logs/<run_id>.log` while open."""
    path = Path(state_dir) / "logs" / f"{run_id}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_JsonFormatter(run_id=run_id, stage=stage))
    handler.addFilter(_PrivacyFilter())
    logger = _logger()
    logger.addHandler(handler)
    try:
        yield path
    finally:
        logger.removeHandler(handler)
        handler.close()


def _logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)  # Handlers choose what they emit.
    return logger


# --- Privacy filter -------------------------------------------------------------------------


# @spec CLI-LOG-005, CLI-LOG-007
class _PrivacyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            if isinstance(record.args, Mapping):
                record.args = {k: _show(v) for k, v in record.args.items()}
            else:
                record.args = tuple(_show(arg) for arg in record.args)
        record.msg = _redact(record.getMessage())
        record.args = None
        for key, value in list(vars(record).items()):
            if key not in _RECORD_ATTRS:
                setattr(
                    record,
                    key,
                    _redact(_show(value)) if isinstance(value, str | PurePath) else value,
                )
        return True


def _show(value: Any) -> Any:
    return _local_path(value) if isinstance(value, PurePath) else value


def _local_path(path: PurePath) -> str:
    """`<name>:<relative path>` under the closest configured path, else the final component."""
    resolved = Path(path).resolve()
    candidates = [
        (name, root) for name, root in _path_roots.items() if resolved.is_relative_to(root)
    ]
    if not candidates:
        return resolved.name
    name, root = max(candidates, key=lambda item: len(item[1].parts))
    relative = resolved.relative_to(root).as_posix()
    return f"{name}:{'' if relative == '.' else relative}"


def _redact(text: str) -> str:
    for value in _private_values(_private_env()):
        text = text.replace(value, REDACTED)
    return text


def _private_env() -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (name, value)
            for name, value in os.environ.items()
            if value and (name in _REDACTED_VARS or name.startswith("AWS_"))
        )
    )


@functools.lru_cache(maxsize=8)
def _private_values(env: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    """Every string to redact, longest first so a value is never left partly redacted."""
    values = {value for _, value in env}
    sites = dict(env).get("URBANBIO_SITES")
    if sites:
        values |= _coordinate_forms(sites)
    return tuple(sorted(values, key=len, reverse=True))


def _coordinate_forms(sites: str) -> set[str]:
    """Each exact coordinate as written in URBANBIO_SITES and as Python formats the parsed value."""
    written = set(_COORDINATE_LINE.findall(sites))
    try:
        for table in tomllib.loads(sites).get("site", []):
            for key in ("latitude_exact", "longitude_exact"):
                if isinstance(table.get(key), int | float):
                    written.add(repr(float(table[key])))
    except (tomllib.TOMLDecodeError, AttributeError, TypeError):
        pass
    forms: set[str] = set()
    for token in written:
        forms |= {token, token.lstrip("+-")}
    return {form for form in forms if form}


# --- JSON lines ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    def __init__(self, *, run_id: str, stage: str) -> None:
        super().__init__()
        self._run_id = run_id
        self._stage = stage

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "ts_utc": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "run_id": self._run_id,
            "stage": self._stage,
            "unit_id": getattr(record, "unit_id", None),
            "event": getattr(record, "event", None),
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RECORD_ATTRS and key not in line:
                line[key] = value
        if record.exc_info:
            line["exception"] = _redact(self.formatException(record.exc_info))
        return json.dumps(line, default=str)
