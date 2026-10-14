"""The `urbanbio` command line (config-cli LLD § CLI).

A thin shell: each command loads configuration and calls one entry point. Commands whose stage
isn't implemented yet are registered with their arguments and say so when run, after the
profile and dev-profile checks every command makes.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, TypeVar

import typer

from urbanbio import config
from urbanbio import logging as ulog
from urbanbio.errors import ConfigError, IntegrityError, StorageError
from urbanbio.storage.marker import init_store

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_CONFIG = 3

F = TypeVar("F", bound=Callable[..., Any])

app = typer.Typer(name="urbanbio", no_args_is_help=True, add_completion=False)
config_app = typer.Typer(no_args_is_help=True, help="Configuration.")
store_app = typer.Typer(no_args_is_help=True, help="The store.")
site_app = typer.Typer(no_args_is_help=True, help="Sites.")
deployment_app = typer.Typer(no_args_is_help=True, help="Deployments.")
retrieval_app = typer.Typer(no_args_is_help=True, help="Media retrievals.")
speech_app = typer.Typer(no_args_is_help=True, help="Speech labeling and recall.")
runs_app = typer.Typer(no_args_is_help=True, help="Provenance.")
app.add_typer(config_app, name="config")
app.add_typer(store_app, name="store")
app.add_typer(site_app, name="site")
app.add_typer(deployment_app, name="deployment")
app.add_typer(retrieval_app, name="retrieval")
app.add_typer(speech_app, name="speech")
app.add_typer(runs_app, name="runs")


def main() -> None:
    """Console-script entry point."""
    app(prog_name="urbanbio")


@app.callback()
def _root(
    verbose: Annotated[bool, typer.Option("--verbose", help="Log DEBUG to the console.")] = False,
) -> None:
    """Passive acoustic recordings to validated species occurrence data."""
    ulog.configure_console(verbose=verbose)


def _fail(err: ConfigError) -> typer.Exit:
    typer.echo(f"error ({err.reason_code}): {err}", err=True)
    return typer.Exit(EXIT_CONFIG)


# @spec CLI-PROF-001, CLI-DEV-002, CLI-DEV-003, CLI-EXIT-001
def _command(*, refused_in_dev: bool = False) -> Callable[[F], F]:
    """Wrap a command: load configuration (no private values), refuse what the profile forbids,
    register path roots for logging, and turn a ConfigError into exit 3."""

    def decorate(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                cfg = config.load(with_sites=False)
                if refused_in_dev and cfg.profile == "dev":
                    raise ConfigError(
                        "this command reads real media or writes the archive, which the dev "
                        "profile refuses",
                        reason_code="dev_refused",
                    )
                ulog.set_path_roots(cfg.paths.as_roots())
                return func(*args, **kwargs)
            except ConfigError as err:
                raise _fail(err) from None

        return wrapper  # type: ignore[return-value]

    return decorate


def _not_implemented(name: str) -> None:
    typer.echo(f"urbanbio {name} isn't implemented yet", err=True)
    raise typer.Exit(EXIT_RUN_FAILED)


# --- config ---------------------------------------------------------------------------------


# @spec CLI-CHECK-009
@config_app.command("check")
def config_check() -> None:
    """Validate configuration, private values, directories, and the store."""
    results = config.check()
    for result in results:
        typer.echo(f"{result.status.upper():4} {result.name}: {result.message}")
        if result.status != "ok" and result.fix:
            typer.echo(f"     fix: {result.fix}")
    if any(result.status == "fail" for result in results):
        raise typer.Exit(EXIT_CONFIG)


# @spec CLI-CMD-003
@config_app.command("paths")
@_command()
def config_paths() -> None:
    """Print the configured paths for the current profile, expanded and resolved."""
    cfg = config.load(with_sites=False)
    for name, path in cfg.paths.as_roots().items():
        typer.echo(f"{name}: {path}")
    if cfg.store.root is not None:
        typer.echo(f"store_root: {cfg.store.root}")


# --- store, site, deployment ----------------------------------------------------------------


# @spec STORE-MARK-001, STORE-MARK-002, STORE-MARK-003, STORE-MARK-004
@store_app.command("init")
@_command()
def store_init() -> None:
    """Write the store marker for the current profile."""
    cfg = config.load(with_sites=False)
    try:
        marker = init_store(cfg)
    except (StorageError, IntegrityError) as err:
        typer.echo(f"error ({err.reason_code}): {err}", err=True)
        raise typer.Exit(EXIT_RUN_FAILED) from None
    typer.echo(f"initialized store {marker['store_id']} for the {marker['profile']} profile")


# @spec CLI-CMD-004
@site_app.command("list")
@_command()
def site_list() -> None:
    """Show sites with generalized coordinates."""
    cfg = config.load(with_sites=True)
    for site in cfg.sites or []:
        typer.echo(
            f"{site.site_id}\t{site.latitude_generalized_deg}\t{site.longitude_generalized_deg}"
            f"\t±{site.coordinate_uncertainty_m} m\t{site.timezone}\t{site.generalization}"
        )


@deployment_app.command("create")
@_command()
def deployment_create(
    site: Annotated[str, typer.Option("--site")],
    serial: Annotated[str, typer.Option("--serial")],
    recorder_name: Annotated[str, typer.Option("--recorder-name")],
    start: Annotated[str, typer.Option("--start")],
    adapter: Annotated[str | None, typer.Option("--adapter")] = None,
    settings: Annotated[Path | None, typer.Option("--settings")] = None,
) -> None:
    """Register a deployment."""
    _not_implemented("deployment create")


@deployment_app.command("end")
@_command()
def deployment_end(
    deployment: Annotated[str, typer.Argument()],
    end: Annotated[str, typer.Option("--end")],
) -> None:
    """Close a deployment."""
    _not_implemented("deployment end")


# --- stages ---------------------------------------------------------------------------------


@app.command("ingest")
@_command(refused_in_dev=True)
def ingest(
    path: Annotated[Path, typer.Argument()],
    deployment: Annotated[str, typer.Option("--deployment")],
    retrieved: Annotated[str, typer.Option("--retrieved")],
    clock_offset_s: Annotated[float | None, typer.Option("--clock-offset-s")] = None,
    retrieval: Annotated[str | None, typer.Option("--retrieval")] = None,
) -> None:
    """Hash, parse, and register a card copied into the quarantine."""
    _not_implemented("ingest")


@app.command("screen")
@_command(refused_in_dev=True)
def screen(retrieval: Annotated[str, typer.Option("--retrieval")]) -> None:
    """Run both speech detectors; write speech events."""
    _not_implemented("screen")


@app.command("archive")
@_command(refused_in_dev=True)
def archive(
    retrieval: Annotated[str, typer.Option("--retrieval")],
    no_purge: Annotated[bool, typer.Option("--no-purge")] = False,
) -> None:
    """Mask, encode FLAC, write to the store, verify, and purge verified originals."""
    _not_implemented("archive")


@app.command("detect")
@_command()
def detect(
    retrieval: Annotated[str | None, typer.Option("--retrieval")] = None,
    all_pending: Annotated[bool, typer.Option("--all-pending")] = False,
) -> None:
    """Run BirdNET on archived audio."""
    _not_implemented("detect")


@app.command("purge")
@_command(refused_in_dev=True)
def purge(
    retrieval: Annotated[str, typer.Option("--retrieval")],
    discard: Annotated[list[str] | None, typer.Option("--discard")] = None,
    reason: Annotated[str | None, typer.Option("--reason")] = None,
) -> None:
    """Delete verified originals from the quarantine; discard named flagged audio."""
    _not_implemented("purge")


@app.command("process")
@_command(refused_in_dev=True)
def process(
    path: Annotated[Path, typer.Argument()],
    deployment: Annotated[str, typer.Option("--deployment")],
    retrieved: Annotated[str, typer.Option("--retrieved")],
    clock_offset_s: Annotated[float | None, typer.Option("--clock-offset-s")] = None,
    retrieval: Annotated[str | None, typer.Option("--retrieval")] = None,
    speech_sample: Annotated[int | None, typer.Option("--speech-sample")] = None,
) -> None:
    """ingest → screen → [speech sample] → archive → detect."""
    _not_implemented("process")


@retrieval_app.command("status")
@_command()
def retrieval_status(retrieval: Annotated[str, typer.Argument()]) -> None:
    """Per-file status, and whether the card can be wiped."""
    _not_implemented("retrieval status")


@speech_app.command("sample")
@_command(refused_in_dev=True)
def speech_sample(
    retrieval: Annotated[str, typer.Option("--retrieval")],
    n: Annotated[int, typer.Option("--n")],
) -> None:
    """Hold a stratified sample in quarantine for speech labeling."""
    _not_implemented("speech sample")


@speech_app.command("label")
@_command(refused_in_dev=True)
def speech_label(
    import_: Annotated[bool, typer.Option("--import", help="Import labels from labels.csv.")],
) -> None:
    """Import labels for held samples and release the holds."""
    _not_implemented("speech label")


@speech_app.command("recall")
@_command()
def speech_recall() -> None:
    """Report speech recall and masked fraction."""
    _not_implemented("speech recall")


@app.command("rescreen")
@_command(refused_in_dev=True)
def rescreen(retrieval: Annotated[str, typer.Option("--retrieval")]) -> None:
    """Re-screen archived audio and re-mask."""
    _not_implemented("rescreen")


# --- runs, query ----------------------------------------------------------------------------


@runs_app.command("list")
@_command()
def runs_list(stage: Annotated[str | None, typer.Option("--stage")] = None) -> None:
    """List run records."""
    _not_implemented("runs list")


@runs_app.command("show")
@_command()
def runs_show(run: Annotated[str, typer.Argument()]) -> None:
    """Show one run record."""
    _not_implemented("runs show")


@runs_app.command("unflag")
@_command()
def runs_unflag(
    stage: Annotated[str, typer.Argument()],
    unit: Annotated[str, typer.Argument()],
    reason: Annotated[str, typer.Option("--reason")],
    note: Annotated[str, typer.Option("--note")],
) -> None:
    """Waive one check for one unit."""
    _not_implemented("runs unflag")


@app.command("query")
@_command()
def query() -> None:
    """Open a DuckDB shell with curated views registered."""
    _not_implemented("query")
