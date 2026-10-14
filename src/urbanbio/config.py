"""Configuration models, loading, and the private/public split (config-cli LLD).

Configuration is read in three layers: `config/pipeline.toml` (stage parameters),
`config/<URBANBIO_PROFILE>.toml` (deployment tables), and an optional operator file named by
`URBANBIO_CONFIG` (deployment tables only). Private values come only from environment variables.

Exact site coordinates live only in `SiteSecret` objects in this module's private registry.
Nothing returned from here exposes them: stages get generalized coordinates on each `Site` and
distances through `site_offset_m`.
"""

from __future__ import annotations

import math
import os
import stat
import tomllib
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from urbanbio.errors import ConfigError
from urbanbio.models.site import Site, check_timezone

PROFILES = ("processing", "dev")
DEPLOYMENT_TABLES = frozenset({"paths", "resources", "quarantine", "store"})
PRIVATE_VARS = ("URBANBIO_SITES", "URBANBIO_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
S3_VARS = ("URBANBIO_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
DATA_DIRS = ("quarantine", "work", "state")
DATA_DIR_MODE = 0o700
EARTH_RADIUS_M = 6_371_008.8  # Mean radius (IUGG)
COORDINATE_TOLERANCE_DEG = 1e-9


# --- Models -------------------------------------------------------------------------------


class _Table(BaseModel):
    # Strict: a value of the wrong type is an error, never coerced.
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class RecordingConfig(_Table):
    sample_rate_hz: int
    duty_on_s: int
    duty_off_s: int
    max_file_length_s: int


class SongMeterMicro2Config(_Table):
    low_battery_v: float


class IngestConfig(_Table):
    songmeter_micro2: SongMeterMicro2Config


class ScreenConfig(_Table):
    padding_s: float
    birdnet_mask_labels: list[str]
    birdnet_threshold: float
    birdnet_stats_floor: float
    birdnet_overlap_s: float
    silero_threshold: float
    silero_min_speech_ms: int
    max_masked_fraction: float


class DetectConfig(_Table):
    model: str
    backend: str
    precision: str
    confidence_floor: float
    top_k: Literal["none"] | int
    overlap_s: float
    sigmoid_sensitivity: float
    bandpass_fmin_hz: int
    bandpass_fmax_hz: int
    geo_model: str
    geo_min_confidence: float


class ArchiveConfig(_Table):
    flac_compression_level: float


class PublishConfig(_Table):
    coordinate_precision_deg: float = Field(gt=0)
    coordinate_uncertainty_m: int = Field(gt=0)


class PathsConfig(_Table):
    quarantine: Path = Field(strict=False)
    work: Path = Field(strict=False)
    state: Path = Field(strict=False)
    model_cache: Path = Field(strict=False)
    test_audio_cache: Path = Field(strict=False)

    def as_roots(self) -> dict[str, Path]:
        """The configured paths by name, for logging local paths relative to them."""
        return {name: getattr(self, name) for name in type(self).model_fields}


class ResourcesConfig(_Table):
    workers: int = Field(ge=1)


class QuarantineConfig(_Table):
    budget_gb: float = Field(gt=0)
    min_free_gb: float = Field(ge=0)


# @spec STORE-CFG-001, STORE-CFG-002, STORE-CFG-004
class StoreConfig(_Table):
    backend: Literal["s3", "filesystem"]
    region: str | None = None
    glacier_transition_days: int | None = None
    root: Path | None = Field(default=None, strict=False)

    @model_validator(mode="after")
    def _keys_match_backend(self) -> StoreConfig:
        if self.backend == "s3" and self.root is not None:
            raise ValueError("`root` belongs to the filesystem backend, not s3")
        if self.backend == "filesystem":
            stray = [
                name
                for name in ("region", "glacier_transition_days")
                if getattr(self, name) is not None
            ]
            if stray:
                raise ValueError(f"{', '.join(stray)} belong to the s3 backend, not filesystem")
            if self.root is None:
                raise ValueError("the filesystem backend requires `root`")
        return self


class PipelineConfig(_Table):
    """Public configuration for one profile. It holds no exact coordinates (CLI-PRIV-003)."""

    profile: Literal["processing", "dev"]
    recording: RecordingConfig
    ingest: IngestConfig
    screen: ScreenConfig
    detect: DetectConfig
    archive: ArchiveConfig
    publish: PublishConfig
    paths: PathsConfig
    resources: ResourcesConfig
    quarantine: QuarantineConfig
    store: StoreConfig
    sites: list[Site] | None = None


# @spec CLI-PRIV-002, CLI-PRIV-006
class SiteSecret(BaseModel):
    """One site's private configuration, exact coordinates included. Never leaves this module."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    site_id: str
    latitude_exact: float = Field(ge=-90, le=90)
    longitude_exact: float = Field(ge=-180, le=180)
    timezone: str
    habitat: str | None = None
    nws_station_id: str | None = None
    coordinate_precision_deg: float | None = Field(default=None, gt=0)

    @field_validator("timezone")
    @classmethod
    def _timezone_is_iana(cls, value: str) -> str:
        return check_timezone(value)

    def __repr__(self) -> str:
        return f"SiteSecret(site_id={self.site_id!r}, coordinates=<masked>)"

    __str__ = __repr__


@dataclass(frozen=True)
class CheckResult:
    """One check of `urbanbio config check`."""

    name: str
    status: Literal["ok", "fail", "warn"]
    message: str
    fix: str = ""


# Private registry of exact coordinates, replaced by each load() that reads sites.
_SECRETS: dict[str, SiteSecret] = {}


# --- Public functions ---------------------------------------------------------------------


def repo_root() -> Path:
    """The repository working tree this package runs from (holds `config/`)."""
    return Path(__file__).resolve().parents[2]


# @spec CLI-PROF-001, CLI-PROF-002, CLI-CFG-001, CLI-CFG-002, CLI-CFG-003, CLI-CFG-004
# @spec CLI-CFG-005, CLI-DEV-001, CLI-DEV-002, CLI-PRIV-001, CLI-PRIV-003, CLI-PRIV-007
# @spec CLI-PRIV-008, DATA-SITE-001
def load(*, with_sites: bool) -> PipelineConfig:
    """Load the configuration of the profile named by `URBANBIO_PROFILE`.

    With `with_sites`, also read site configuration (from `URBANBIO_SITES` in `processing`,
    from `config/dev-sites.toml` in `dev`); without it, `sites` is None and no private value
    is read.
    """
    profile = _profile()
    _refuse_dev_private_sites(profile)
    root = repo_root()
    layers = _read_layers(profile, root)
    _check_split(layers)
    cfg = _validate(profile, layers)
    inside = _paths_inside(cfg, root)
    if inside:
        raise ConfigError(
            f"configured paths lie inside the repository working tree: {', '.join(inside)}",
            reason_code="path_in_repo",
        )
    if not with_sites:
        return cfg
    secrets = _read_sites(profile, root, cfg.publish)
    sites = [_generalize(secret, cfg.publish) for secret in secrets]
    _SECRETS.clear()
    _SECRETS.update({secret.site_id: secret for secret in secrets})
    return cfg.model_copy(update={"sites": sites})


# @spec CLI-PRIV-004, CLI-PRIV-005
def site_offset_m(site_id: str, latitude_deg: float, longitude_deg: float) -> float:
    """Distance in meters from the site's exact position to the given point (haversine)."""
    secret = _SECRETS.get(site_id)
    if secret is None:
        raise ConfigError(
            f"no site {site_id!r} in the loaded configuration", reason_code="unknown_site"
        )
    lat1, lon1 = math.radians(secret.latitude_exact), math.radians(secret.longitude_exact)
    lat2, lon2 = math.radians(latitude_deg), math.radians(longitude_deg)
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return float(2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h))))


# @spec CLI-CHECK-001, CLI-CHECK-002, CLI-CHECK-003, CLI-CHECK-004, CLI-CHECK-005, CLI-CHECK-006
# @spec CLI-CHECK-007, CLI-CHECK-008, CLI-CHECK-010, CLI-CHECK-011
def check() -> list[CheckResult]:
    """Run every `config check` check for the current profile. Never raises a ConfigError.

    Messages and fixes name variables and configured paths, never a private value.
    """
    results: list[CheckResult] = []
    rerun = "fix the failures above, then run `urbanbio config check` again"

    def add(name: str, status: Literal["ok", "fail", "warn"], message: str, fix: str = "") -> None:
        results.append(CheckResult(name, status, message, fix))

    def not_checked(name: str, why: str) -> None:
        add(name, "fail", f"not checked: {why}", rerun)

    root = repo_root()

    profile: str | None = None
    try:
        profile = _profile()
        add("profile", "ok", f"profile is {profile}")
    except ConfigError as err:
        add("profile", "fail", str(err), "set URBANBIO_PROFILE to `processing` or `dev`")

    try:
        operator = _operator_file(root)
        add("operator_config", "ok", f"operator config: {operator or 'none'}")
    except ConfigError as err:
        add(
            "operator_config",
            "fail",
            str(err),
            "set URBANBIO_CONFIG to the absolute path of an existing file outside the "
            "repository working tree, or unset it",
        )

    layers: _Layers | None = None
    cfg: PipelineConfig | None = None
    if profile is None:
        not_checked("layers", "no valid profile")
    else:
        try:
            layers = _read_layers(profile, root)
            _check_split(layers)
            cfg = _validate(profile, layers)
            add("layers", "ok", "configuration layers are valid")
        except ConfigError as err:
            add(
                "layers",
                "fail",
                str(err),
                "keep stage parameters in config/pipeline.toml and only [paths], [resources], "
                "[quarantine], and [store] in profile and operator files; fix misspelled keys "
                "and wrong types",
            )

    if layers is None:
        not_checked("coordinate_like", "configuration layers didn't load")
    else:
        found = _coordinate_like(layers)
        if found:
            add(
                "coordinate_like",
                "fail",
                "values that look like precise coordinates: " + ", ".join(found),
                "remove coordinates from configuration files; exact site coordinates belong "
                "only in URBANBIO_SITES",
            )
        else:
            add("coordinate_like", "ok", "no coordinate-like values in configuration files")

    directories_ok = False
    if cfg is None:
        not_checked("paths_outside_repo", "configuration didn't load")
        not_checked("directories", "configuration didn't load")
    else:
        inside = _paths_inside(cfg, root)
        if inside:
            add(
                "paths_outside_repo",
                "fail",
                "inside the repository working tree: " + ", ".join(inside),
                "configure these paths outside the repository working tree",
            )
        else:
            add("paths_outside_repo", "ok", "configured paths lie outside the working tree")
        problems, fixes = _data_dir_problems(cfg)
        if problems:
            add("directories", "fail", "; ".join(problems), "; ".join(fixes))
        else:
            directories_ok = True
            add("directories", "ok", "quarantine, work, and state exist with mode 0700")

    private_ok = False
    if profile is None or cfg is None:
        not_checked("private_vars", "configuration didn't load")
    else:
        required = (["URBANBIO_SITES"] if profile == "processing" else []) + (
            list(S3_VARS) if cfg.store.backend == "s3" else []
        )
        missing = [var for var in required if not os.environ.get(var)]
        if missing:
            add(
                "private_vars",
                "fail",
                "missing or empty: " + ", ".join(missing),
                "set these variables in the environment (see the runbooks)",
            )
        else:
            private_ok = True
            add("private_vars", "ok", "required private variables are set")

    dev_sites_set = profile == "dev" and "URBANBIO_SITES" in os.environ
    if dev_sites_set:
        add(
            "dev_private_sites",
            "fail",
            "URBANBIO_SITES is set in the dev profile",
            "unset URBANBIO_SITES; the dev profile uses config/dev-sites.toml",
        )
    else:
        add("dev_private_sites", "ok", "no private site configuration in dev")

    if profile is None or cfg is None:
        not_checked("sites", "configuration didn't load")
    elif dev_sites_set:
        not_checked("sites", "URBANBIO_SITES is set in the dev profile")
    else:
        try:
            for secret in _read_sites(profile, root, cfg.publish):
                _generalize(secret, cfg.publish)
            add("sites", "ok", "site configuration is valid")
        except ConfigError as err:
            add("sites", "fail", str(err), "fix the site configuration (format: config-cli LLD)")

    # The store is contacted only with a complete configuration (CLI-CHECK-011).
    same_filesystem_applies = (
        cfg is not None and profile == "processing" and cfg.store.backend == "filesystem"
    )
    if cfg is None:
        blocker = "configuration didn't load"
    elif not private_ok:
        blocker = "a required private variable is missing"
    elif not directories_ok:
        blocker = "the data directories failed their check"
    else:
        blocker = None
    if blocker is not None:
        not_checked("store", blocker)
        if same_filesystem_applies:
            not_checked("same_filesystem", blocker)
    else:
        assert cfg is not None
        add(*_store_result(cfg))
        if same_filesystem_applies:
            add(*_same_filesystem_result(cfg))

    if cfg is not None:
        cpus = os.cpu_count() or 1
        if cfg.resources.workers > cpus:
            add(
                "workers",
                "warn",
                f"resources.workers is {cfg.resources.workers}, more than the {cpus} CPUs "
                "the host reports",
                f"set resources.workers to at most {cpus} in the operator config file",
            )
        else:
            add("workers", "ok", f"resources.workers ({cfg.resources.workers}) fits {cpus} CPUs")
    return results


def _store_result(cfg: PipelineConfig) -> tuple[str, Literal["ok", "fail"], str, str]:
    """Open the store as every command does. Messages never name the bucket."""
    from urbanbio.errors import UrbanbioError
    from urbanbio.storage.store import open_store

    root = cfg.store.root
    if cfg.store.backend == "filesystem" and root is not None and not root.is_dir():
        return (
            "store",
            "fail",
            f"store.root doesn't exist as a directory: {root}",
            "create store.root (or mount the share), then run `urbanbio store init`",
        )
    try:
        open_store(cfg)
    except ConfigError as err:
        fix = (
            "run `urbanbio store init` to initialize a new store, or point the configuration "
            "at an existing one"
            if err.reason_code == "store_marker_missing"
            else "point the configuration at the store this profile initialized"
        )
        return "store", "fail", str(err), fix
    except (UrbanbioError, OSError) as err:
        return (
            "store",
            "fail",
            f"the store can't be opened: {err}",
            "check the network or share, then run `urbanbio config check` again",
        )
    return "store", "ok", f"the {cfg.store.backend} store opened and its marker matches", ""


def _same_filesystem_result(cfg: PipelineConfig) -> tuple[str, Literal["ok", "fail"], str, str]:
    from urbanbio.storage.filesystem import FilesystemStore

    assert cfg.store.root is not None
    if FilesystemStore(cfg.store.root).same_filesystem_as(cfg.paths.quarantine):
        return (
            "same_filesystem",
            "fail",
            "store.root is on the same filesystem as paths.quarantine",
            "put store.root on another disk or a mounted share (and check the share is mounted)",
        )
    return (
        "same_filesystem",
        "ok",
        "store.root and paths.quarantine are on different filesystems",
        "",
    )


# --- Layers ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Layers:
    pipeline: dict[str, Any]
    profile: dict[str, Any]
    operator: dict[str, Any]
    operator_path: Path | None


def _profile() -> str:
    value = os.environ.get("URBANBIO_PROFILE")
    if value is None:
        raise ConfigError("URBANBIO_PROFILE is not set", reason_code="profile_unset")
    if value not in PROFILES:
        raise ConfigError(
            f"URBANBIO_PROFILE names an unknown profile; expected one of {', '.join(PROFILES)}",
            reason_code="profile_unknown",
        )
    return value


def _refuse_dev_private_sites(profile: str) -> None:
    if profile == "dev" and "URBANBIO_SITES" in os.environ:
        raise ConfigError(
            "URBANBIO_SITES is set in the dev profile; dev uses config/dev-sites.toml",
            reason_code="dev_private_sites",
        )


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _operator_file(root: Path) -> Path | None:
    value = os.environ.get("URBANBIO_CONFIG")
    if value is None:
        return None
    path = Path(os.path.expanduser(value))
    if not path.is_absolute():
        raise ConfigError("URBANBIO_CONFIG must be an absolute path", reason_code="operator_config")
    if not path.exists():
        raise ConfigError(f"URBANBIO_CONFIG names no file: {path}", reason_code="operator_config")
    if not path.is_file():
        raise ConfigError(
            f"URBANBIO_CONFIG isn't a regular file: {path}", reason_code="operator_config"
        )
    if _inside(path, root):
        raise ConfigError(
            "URBANBIO_CONFIG names a file inside the repository working tree",
            reason_code="operator_config",
        )
    return path


def _read_toml(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"{label} not found: {path}", reason_code="config_missing") from None
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"{label} isn't valid TOML: {err}", reason_code="config_parse") from None


def _read_layers(profile: str, root: Path) -> _Layers:
    operator_path = _operator_file(root)
    return _Layers(
        pipeline=_read_toml(root / "config" / "pipeline.toml", "config/pipeline.toml"),
        profile=_read_toml(root / "config" / f"{profile}.toml", f"config/{profile}.toml"),
        operator={} if operator_path is None else _read_toml(operator_path, "operator config"),
        operator_path=operator_path,
    )


def _check_split(layers: _Layers) -> None:
    misplaced = sorted(DEPLOYMENT_TABLES & layers.pipeline.keys())
    if misplaced:
        raise ConfigError(
            f"config/pipeline.toml may not contain deployment tables: {', '.join(misplaced)}",
            reason_code="config_layer",
        )
    for label, doc in (("profile file", layers.profile), ("operator config", layers.operator)):
        extra = sorted(doc.keys() - DEPLOYMENT_TABLES)
        if extra:
            raise ConfigError(
                f"the {label} may contain only deployment tables, not: {', '.join(extra)}",
                reason_code="config_layer",
            )


def _merge(layers: _Layers) -> dict[str, Any]:
    merged: dict[str, Any] = {**layers.pipeline}
    for name, table in layers.profile.items():
        merged[name] = dict(table) if isinstance(table, dict) else table
    for name, table in layers.operator.items():
        base = merged.get(name)
        # A backend switch replaces the store table, so the other backend's keys don't carry over.
        switches_backend = name == "store" and isinstance(table, dict) and "backend" in table
        if isinstance(base, dict) and isinstance(table, dict) and not switches_backend:
            merged[name] = {**base, **table}
        else:
            merged[name] = table
    return merged


def _expand(value: Any) -> Any:
    return Path(os.path.expanduser(value)).resolve() if isinstance(value, str) else value


def _validate(profile: str, layers: _Layers) -> PipelineConfig:
    merged = _merge(layers)
    if isinstance(merged.get("paths"), dict):
        merged["paths"] = {k: _expand(v) for k, v in merged["paths"].items()}
    if isinstance(merged.get("store"), dict) and "root" in merged["store"]:
        merged["store"] = {**merged["store"], "root": _expand(merged["store"]["root"])}
    try:
        return PipelineConfig.model_validate({**merged, "profile": profile})
    except ValidationError as err:
        raise ConfigError(
            f"invalid configuration: {_describe(err)}", reason_code="config_invalid"
        ) from None


def _describe(err: ValidationError) -> str:
    """A validation error without the offending values, which may be private."""
    return "; ".join(
        f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}"
        for e in err.errors(include_input=False, include_url=False, include_context=False)
    )


def _paths_inside(cfg: PipelineConfig, root: Path) -> list[str]:
    named = {f"paths.{name}": path for name, path in cfg.paths.as_roots().items()}
    if cfg.store.root is not None:
        named["store.root"] = cfg.store.root
    return [name for name, path in named.items() if _inside(path, root)]


def _data_dir_problems(cfg: PipelineConfig) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    fixes: list[str] = []
    for name in DATA_DIRS:
        path: Path = getattr(cfg.paths, name)
        if not path.is_dir():
            problems.append(f"paths.{name} doesn't exist: {path}")
            fixes.append(f"mkdir -p -m 700 {path}")
        elif (mode := stat.S_IMODE(path.stat().st_mode)) != DATA_DIR_MODE:
            problems.append(f"paths.{name} has mode {mode:04o}, not 0700: {path}")
            fixes.append(f"chmod 700 {path}")
    return problems, fixes


def _coordinate_like(layers: _Layers) -> list[str]:
    """Tables in any layer holding a lat-like and a lon-like number off the published grid."""
    publish = layers.pipeline.get("publish")
    precision = publish.get("coordinate_precision_deg") if isinstance(publish, dict) else None
    if not _is_number(precision) or precision <= 0:
        precision = 0.01
    found: list[str] = []
    labels = (
        ("pipeline", layers.pipeline),
        ("profile", layers.profile),
        ("operator", layers.operator),
    )
    for label, doc in labels:
        for path, table in _tables(doc, label):
            lats = {k: v for k, v in table.items() if "lat" in k.lower() and _is_number(v)}
            lons = {k: v for k, v in table.items() if "lon" in k.lower() and _is_number(v)}
            if (
                lats
                and lons
                and not all(_on_grid(v, precision) for v in (*lats.values(), *lons.values()))
            ):
                found.append(f"{path} ({', '.join(sorted({*lats, *lons}))})")
    return found


def _tables(doc: dict[str, Any], path: str):
    yield path, doc
    for key, value in doc.items():
        if isinstance(value, dict):
            yield from _tables(value, f"{path}.{key}")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    yield from _tables(item, f"{path}.{key}[{i}]")


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _on_grid(value: float, precision: float) -> bool:
    steps = value / precision
    return abs(steps - round(steps)) * precision <= COORDINATE_TOLERANCE_DEG


# --- Sites ----------------------------------------------------------------------------------


def _read_sites(profile: str, root: Path, publish: PublishConfig) -> list[SiteSecret]:
    if profile == "dev":
        source = "config/dev-sites.toml"
        text = (root / "config" / "dev-sites.toml").read_text()
    else:
        source = "URBANBIO_SITES"
        text = os.environ.get("URBANBIO_SITES", "")
        if not text:
            raise ConfigError("URBANBIO_SITES is not set or empty", reason_code="sites_missing")
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"{source} isn't valid TOML: {err}", reason_code="sites_parse") from None
    tables = doc.get("site")
    if set(doc) != {"site"} or not isinstance(tables, list) or not tables:
        raise ConfigError(
            f"{source} must hold one or more [[site]] tables and nothing else",
            reason_code="sites_parse",
        )
    secrets: list[SiteSecret] = []
    for i, table in enumerate(tables):
        try:
            secrets.append(SiteSecret.model_validate(table))
        except ValidationError as err:
            raise ConfigError(
                f"{source}: site {i + 1}: {_describe(err)}", reason_code="sites_invalid"
            ) from None
    ids = [s.site_id for s in secrets]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"{source} names a site more than once", reason_code="sites_invalid")
    default = Decimal(str(publish.coordinate_precision_deg))
    for secret in secrets:
        if secret.coordinate_precision_deg is not None and (
            Decimal(str(secret.coordinate_precision_deg)) < default
        ):
            raise ConfigError(
                f"{source}: site {secret.site_id}: coordinate_precision_deg is finer than "
                f"publish.coordinate_precision_deg ({publish.coordinate_precision_deg})",
                reason_code="site_precision_too_fine",
            )
    return secrets


def _generalize(secret: SiteSecret, publish: PublishConfig) -> Site:
    default = Decimal(str(publish.coordinate_precision_deg))
    precision = (
        default
        if secret.coordinate_precision_deg is None
        else Decimal(str(secret.coordinate_precision_deg))
    )

    def rounded(value: float) -> float:
        steps = (Decimal(repr(value)) / precision).to_integral_value(rounding=ROUND_HALF_EVEN)
        return float(steps * precision)

    uncertainty = Decimal(publish.coordinate_uncertainty_m) * precision / default
    try:
        return Site(
            site_id=secret.site_id,
            latitude_generalized_deg=rounded(secret.latitude_exact),
            longitude_generalized_deg=rounded(secret.longitude_exact),
            coordinate_uncertainty_m=int(uncertainty.to_integral_value(rounding=ROUND_HALF_EVEN)),
            generalization=f"rounded to {precision.normalize():f}°",
            habitat=secret.habitat,
            timezone=secret.timezone,
            nws_station_id=secret.nws_station_id,
        )
    except ValidationError as err:
        raise ConfigError(
            f"site {secret.site_id!r}: {_describe(err)}", reason_code="sites_invalid"
        ) from None
