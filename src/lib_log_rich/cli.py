"""CLI adapter wiring the logging helpers into a rich-click interface.

Purpose
-------
Provide a stable command surface that mirrors the bitranox scaffold while
exposing the lib_log_rich behaviours (`summary_info`, `hello_world`,
`i_should_fail`, and `logdemo`). The adapter keeps traceback handling
aligned with ``lib_cli_exit_tools`` so console scripts and ``python -m``
invocations act consistently.

Contents
--------
* :data:`CLICK_CONTEXT_SETTINGS` – shared configuration exposing ``-h`` and
  ``--help`` across commands.
* :func:`cli` – root command wiring the traceback toggle and metadata banner.
* :func:`cli_main` – prints the metadata banner when no subcommand is used.
* :func:`cli_info`, :func:`cli_hello`, :func:`cli_fail`, :func:`cli_logdemo`
  – subcommands for metadata, success path, failure path, and the demo.
* :func:`main` – composition helper delegating to ``lib_cli_exit_tools``.

System Role
-----------
Acts as the primary presentation-layer adapter. Packaging registers the
`lib_log_rich` console script which ultimately calls this module.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from contextlib import AbstractContextManager
from typing import Any, Callable, Final, Mapping, Optional, Sequence, cast

import lib_cli_exit_tools
import rich_click as click
from click.core import ParameterSource

from . import __init__conf__
from . import config as config_module
from .domain.dump_filter import FilterSpecValue
from .lib_log_rich import (
    hello_world as _hello_world,
    i_should_fail as _fail,
    logdemo as _logdemo,
    summary_info as _summary_info,
)
from .domain.palettes import CONSOLE_STYLE_THEMES

CLICK_CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])  # noqa: C408
# Show a concise excerpt by default so CLI errors remain readable.
_TRACEBACK_SUMMARY_LIMIT: Final[int] = 500
# Allow opt-in verbose mode to print large tracebacks for debugging sessions.
_TRACEBACK_VERBOSE_LIMIT: Final[int] = 10_000
TRACEBACK_SUMMARY_LIMIT: Final[int] = _TRACEBACK_SUMMARY_LIMIT
TRACEBACK_VERBOSE_LIMIT: Final[int] = _TRACEBACK_VERBOSE_LIMIT


def _dump_extension(fmt: str) -> str:
    """Return the file extension for ``fmt``.

    Why
    ---
    Keeps CLI behaviour consistent with dump adapter naming conventions.

    Parameters
    ----------
    fmt:
        Human-entered format name (case-insensitive).

    Returns
    -------
    str
        File extension beginning with a dot.

    Examples
    --------
    >>> _dump_extension('text')
    '.log'
    >>> _dump_extension('HTML')
    '.html'
    """

    mapping = {"text": ".log", "json": ".json", "html_table": ".html", "html_txt": ".html"}
    return mapping.get(fmt.lower(), f".{fmt.lower()}")


FilterMapping = Mapping[str, FilterSpecValue]


def _parse_key_value(entry: str, option: str) -> tuple[str, str]:
    """Split ``entry`` into key/value enforcing ``KEY=VALUE`` syntax."""

    if "=" not in entry:
        raise click.BadParameter(f"{option} expects KEY=VALUE pairs; received {entry!r}")
    key, value = entry.split("=", 1)
    key = key.strip()
    if not key:
        raise click.BadParameter(f"{option} requires a non-empty key")
    return key, value


def _append_filter_spec(target: dict[str, FilterSpecValue], key: str, spec: FilterSpecValue) -> None:
    """Accumulate ``spec`` for ``key`` supporting OR semantics."""

    existing = target.get(key)
    if existing is None:
        target[key] = spec
        return
    if isinstance(existing, list):
        existing.append(spec)
        target[key] = existing
        return
    target[key] = [existing, spec]


def _collect_field_filters(
    *,
    option_prefix: str,
    exact: Sequence[str] = (),
    contains: Sequence[str] = (),
    icontains: Sequence[str] = (),
    regex: Sequence[str] = (),
) -> dict[str, FilterSpecValue]:
    """Build filter specifications for a family of CLI options."""

    filters: dict[str, FilterSpecValue] = {}
    for entry in exact:
        key, value = _parse_key_value(entry, f"{option_prefix}-exact")
        _append_filter_spec(filters, key, value)
    for entry in contains:
        key, value = _parse_key_value(entry, f"{option_prefix}-contains")
        _append_filter_spec(filters, key, {"contains": value})
    for entry in icontains:
        key, value = _parse_key_value(entry, f"{option_prefix}-icontains")
        _append_filter_spec(filters, key, {"icontains": value})
    for entry in regex:
        key, value = _parse_key_value(entry, f"{option_prefix}-regex")
        option_name = f"{option_prefix}-regex"
        try:
            pattern = re.compile(value)
        except re.error as exc:
            raise click.BadParameter(
                f"Invalid regular expression for {option_name}: {exc}",
                param_hint=option_name,
            ) from exc
        _append_filter_spec(filters, key, {"pattern": pattern, "regex": True})
    return filters


def _none_if_empty(mapping: dict[str, FilterSpecValue]) -> FilterMapping | None:
    """Return ``None`` when ``mapping`` is empty."""

    return mapping or None


def _resolve_dump_path(base: Path, theme: str, fmt: str) -> Path:
    """Derive a per-theme path from ``base`` and ``fmt``.

    Why
    ---
    Centralises the filesystem rules documented in `EXAMPLES.md` so repeated
    logdemo invocations do not overwrite each other unexpectedly.

    Parameters
    ----------
    base:
        Target directory or file supplied via ``--dump-path``.
    theme:
        Theme identifier used to suffix filenames.
    fmt:
        Dump format string passed to :func:`_dump_extension`.

    Returns
    -------
    Path
        Fully-qualified path ready for writing.

    Examples
    --------
    >>> from pathlib import Path
    >>> _resolve_dump_path(Path('.'), 'classic', 'text')  # doctest: +SKIP
    PosixPath('logdemo-classic.log')
    """

    base = base.expanduser()
    extension = _dump_extension(fmt)

    if base.exists() and base.is_dir():
        return base / f"logdemo-{theme}{extension}"

    if base.suffix:
        parent = base.parent if base.parent != Path("") else Path(".")
        parent.mkdir(parents=True, exist_ok=True)
        return parent / f"{base.stem}-{theme}{base.suffix}"

    base.mkdir(parents=True, exist_ok=True)
    return base / f"logdemo-{theme}{extension}"


def _parse_graylog_endpoint(value: str | None) -> tuple[str, int] | None:
    """Normalise ``HOST:PORT`` strings for Graylog targets.

    Why
    ---
    Shares parsing logic between the CLI and :func:`logdemo`, ensuring helpful
    error messages when arguments are malformed.

    Parameters
    ----------
    value:
        Raw string supplied via ``--graylog-endpoint``.

    Returns
    -------
    tuple[str, int] | None
        Parsed endpoint or ``None`` when ``value`` is ``None``.

    Raises
    ------
    click.BadParameter
        If the string is not of the form ``HOST:PORT``.

    Examples
    --------
    >>> _parse_graylog_endpoint('graylog.local:12201')
    ('graylog.local', 12201)
    >>> _parse_graylog_endpoint(None) is None
    True
    """

    if value is None:
        return None
    host, _, port = value.partition(":")
    if not host or not port.isdigit():
        raise click.BadParameter("Expected HOST:PORT for --graylog-endpoint")
    return host, int(port)


def _build_dump_filters(
    *,
    context_exact: tuple[str, ...],
    context_contains: tuple[str, ...],
    context_icontains: tuple[str, ...],
    context_regex: tuple[str, ...],
    context_extra_exact: tuple[str, ...],
    context_extra_contains: tuple[str, ...],
    context_extra_icontains: tuple[str, ...],
    context_extra_regex: tuple[str, ...],
    extra_exact: tuple[str, ...],
    extra_contains: tuple[str, ...],
    extra_icontains: tuple[str, ...],
    extra_regex: tuple[str, ...],
) -> tuple[FilterMapping | None, FilterMapping | None, FilterMapping | None]:
    """Build filter mappings from CLI options for context, context_extra, and extra fields.

    Parameters
    ----------
    context_*, context_extra_*, extra_*:
        Filter option tuples from CLI arguments.

    Returns
    -------
    tuple[FilterMapping | None, FilterMapping | None, FilterMapping | None]
        Triple of (context_filters, context_extra_filters, extra_filters).
    """
    context_filters = _none_if_empty(
        _collect_field_filters(
            option_prefix="--context",
            exact=context_exact,
            contains=context_contains,
            icontains=context_icontains,
            regex=context_regex,
        ),
    )
    context_extra_filters = _none_if_empty(
        _collect_field_filters(
            option_prefix="--context-extra",
            exact=context_extra_exact,
            contains=context_extra_contains,
            icontains=context_extra_icontains,
            regex=context_extra_regex,
        ),
    )
    extra_filters = _none_if_empty(
        _collect_field_filters(
            option_prefix="--extra",
            exact=extra_exact,
            contains=extra_contains,
            icontains=extra_icontains,
            regex=extra_regex,
        ),
    )
    return context_filters, context_extra_filters, extra_filters


def _resolve_format_presets(
    ctx: click.Context,
    console_format_preset: str | None,
    console_format_template: str | None,
) -> tuple[str | None, str | None]:
    """Resolve console format preset and template from CLI args and context inheritance.

    Parameters
    ----------
    ctx:
        Click context containing inherited values.
    console_format_preset:
        Explicit preset from CLI argument.
    console_format_template:
        Explicit template from CLI argument.

    Returns
    -------
    tuple[str | None, str | None]
        Resolved (preset, template) pair, preferring explicit args over inheritance.
    """
    inherited_preset = ctx.obj.get("console_format_preset") if ctx.obj else None
    inherited_template = ctx.obj.get("console_format_template") if ctx.obj else None

    if console_format_preset is None:
        console_format_preset = inherited_preset
    if console_format_template is None:
        console_format_template = inherited_template

    return console_format_preset, console_format_template


def _print_theme_styles(theme_name: str, styles: dict[str, str]) -> None:
    """Print theme header and style mappings to console.

    Parameters
    ----------
    theme_name:
        Name of the theme being displayed.
    styles:
        Level->style mapping for this theme.
    """
    click.echo(click.style(f"=== Theme: {theme_name} ===", bold=True))
    for level, style in styles.items():
        click.echo(f"  {level:<8} -> {style}")
    click.echo("  emitting sample events…")


def _print_backend_status(
    *,
    enable_graylog: bool,
    enable_journald: bool,
    enable_eventlog: bool,
    endpoint_tuple: tuple[str, int] | None,
    graylog_protocol: str,
    graylog_tls: bool,
) -> None:
    """Print status of enabled backend adapters.

    Parameters
    ----------
    enable_graylog:
        Whether Graylog adapter is enabled.
    enable_journald:
        Whether journald adapter is enabled.
    enable_eventlog:
        Whether Windows Event Log adapter is enabled.
    endpoint_tuple:
        Graylog (host, port) if configured.
    graylog_protocol:
        Graylog protocol ('tcp' or 'udp').
    graylog_tls:
        Whether TLS is enabled for Graylog.
    """
    if enable_graylog:
        destination = endpoint_tuple or ("127.0.0.1", 12201)
        scheme = graylog_protocol.upper() + ("+TLS" if graylog_tls and graylog_protocol == "tcp" else "")
        click.echo(f"  graylog -> {destination[0]}:{destination[1]} via {scheme}")
    if enable_journald:
        click.echo("  journald -> systemd.journal.send")
    if enable_eventlog:
        click.echo("  eventlog -> Windows Event Log")


def _run_theme_demo(
    *,
    theme_name: str,
    service: str,
    environment: str,
    dump_format: str | None,
    target_path: Path | None,
    console_format_preset: str | None,
    console_format_template: str | None,
    dump_format_preset: str | None,
    dump_format_template: str | None,
    enable_graylog: bool,
    graylog_endpoint: tuple[str, int] | None,
    graylog_protocol: str,
    graylog_tls: bool,
    enable_journald: bool,
    enable_eventlog: bool,
    context_filters: FilterMapping | None,
    context_extra_filters: FilterMapping | None,
    extra_filters: FilterMapping | None,
) -> dict[str, Any]:
    """Execute logdemo for a single theme and return results.

    Parameters
    ----------
    theme_name:
        Theme to use for this demo run.
    service, environment:
        Metadata fields for log context.
    dump_format, target_path:
        Optional dump configuration.
    console_format_preset, console_format_template:
        Console output formatting.
    dump_format_preset, dump_format_template:
        Dump output formatting.
    enable_graylog, graylog_endpoint, graylog_protocol, graylog_tls:
        Graylog adapter configuration.
    enable_journald, enable_eventlog:
        Platform adapter toggles.
    context_filters, context_extra_filters, extra_filters:
        Filter mappings for dump output.

    Returns
    -------
    dict[str, Any]
        Result dictionary from _logdemo containing 'events' and optional 'dump'.

    Raises
    ------
    click.ClickException:
        If _logdemo raises ValueError.
    """
    try:
        return _logdemo(
            theme=theme_name,
            service=service,
            environment=f"{environment}-{theme_name}" if environment else None,
            dump_format=dump_format,
            dump_path=target_path,
            console_format_preset=console_format_preset,
            console_format_template=console_format_template,
            dump_format_preset=dump_format_preset,
            dump_format_template=dump_format_template,
            enable_graylog=enable_graylog,
            graylog_endpoint=graylog_endpoint,
            graylog_protocol=graylog_protocol,
            graylog_tls=graylog_tls,
            enable_journald=enable_journald,
            enable_eventlog=enable_eventlog,
            context_filters=context_filters,
            context_extra_filters=context_extra_filters,
            extra_filters=extra_filters,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


@click.group(
    help=__init__conf__.title,
    context_settings=CLICK_CONTEXT_SETTINGS,
    invoke_without_command=True,
)
@click.version_option(
    version=__init__conf__.version,
    prog_name=__init__conf__.shell_command,
    message=f"{__init__conf__.shell_command} version {__init__conf__.version}",
)
@click.option(
    "--use-dotenv/--no-use-dotenv",
    default=False,
    help="Load environment variables from a nearby .env before running commands.",
)
@click.option(
    "--hello",
    is_flag=True,
    help="Print the canonical Hello World greeting before the metadata banner.",
)
@click.option(
    "--traceback/--no-traceback",
    is_flag=True,
    default=True,
    help="Show full Python traceback on errors (use --no-traceback to suppress).",
)
@click.option(
    "--console-format-preset",
    "--console_format_preset",
    type=click.Choice(["full", "short", "full_loc", "short_loc"], case_sensitive=False),
    help="Preset console layout forwarded to subcommands unless overridden.",
)
@click.option(
    "--console-format-template",
    "--console_format_template",
    help="Custom console format template forwarded to subcommands unless overridden.",
)
@click.option(
    "--queue-stop-timeout",
    "--queue_stop_timeout",
    type=float,
    metavar="SECONDS",
    help="Override the default queue drain timeout; values <= 0 wait indefinitely (fallback: LOG_QUEUE_STOP_TIMEOUT).",
)
@click.pass_context
def cli(
    ctx: click.Context,
    use_dotenv: bool,
    hello: bool,
    traceback: bool,
    console_format_preset: str | None,
    console_format_template: str | None,
    queue_stop_timeout: float | None,
) -> None:
    """Root command storing the traceback preference and default action.

    Why
    ---
    Acts as the entry point for the console script, wiring environment toggles
    and the default behaviour described in the CLI design notes.

    What
    ----
    Optionally loads ``.env`` files, persists traceback preferences, and invokes
    the requested subcommand (or prints the banner when none is provided). The
    help output also points to queue-tuning knobs and accepts a
    ``--queue-stop-timeout`` override so operators can cap how long shutdown
    waits for draining without changing code (fallback remains
    ``LOG_QUEUE_STOP_TIMEOUT``).

    Parameters
    ----------
    ctx:
        Click context used to persist state between callbacks.
    use_dotenv:
        Boolean toggle derived from ``--use-dotenv``.
    hello:
        Whether to print the hello-world stub before the banner when no
        subcommand is invoked.
    traceback:
        Enables verbose tracebacks for subsequent command execution.

    Side Effects
    ------------
    Mutates ``lib_cli_exit_tools.config`` so shared exit handling honours the
    traceback preference.
    """

    source = ctx.get_parameter_source("use_dotenv")
    explicit: bool | None = None
    if isinstance(source, ParameterSource) and source is not ParameterSource.DEFAULT:
        explicit = use_dotenv
    env_toggle = os.getenv(config_module.DOTENV_ENV_VAR)
    if config_module.should_use_dotenv(explicit=explicit, env_value=env_toggle):
        config_module.enable_dotenv()

    ctx.ensure_object(dict)
    ctx.obj["traceback"] = traceback
    if console_format_preset is not None:
        ctx.obj["console_format_preset"] = console_format_preset
    if console_format_template is not None:
        ctx.obj["console_format_template"] = console_format_template
    if queue_stop_timeout is not None:
        ctx.obj["queue_stop_timeout"] = queue_stop_timeout
    lib_cli_exit_tools.config.traceback = traceback
    lib_cli_exit_tools.config.traceback_force_color = traceback
    if ctx.invoked_subcommand is not None:
        return
    if hello:
        _hello_world()
    cli_main()


def cli_main() -> None:
    """Print the metadata banner when invoked without a subcommand.

    Why
    ---
    Matches the scaffold behaviour so ``lib_log_rich`` without
    arguments prints install metadata.

    Side Effects
    ------------
    Writes the banner to stdout via :func:`click.echo`.

    Examples
    --------
    >>> from unittest import mock
    >>> with mock.patch('lib_log_rich.cli.click.echo') as echo:
    ...     cli_main()
    >>> echo.assert_called()
    """

    click.echo(_summary_info(), nl=False)


@cli.command("info", context_settings=CLICK_CONTEXT_SETTINGS)
def cli_info() -> None:
    """Print resolved metadata so users can inspect installation details.

    Why
    ---
    Provides an explicit command for scripts tooling to read the metadata banner
    without invoking other demo behaviour.

    Side Effects
    ------------
    Emits the banner to stdout.
    """

    click.echo(_summary_info(), nl=False)


@cli.command("hello", context_settings=CLICK_CONTEXT_SETTINGS)
def cli_hello() -> None:
    """Demonstrate the success path stub.

    Why
    ---
    Maintains compatibility with the scaffold's hello-world example used in
    documentation and smoke tests.
    """

    _hello_world()


@cli.command("fail", context_settings=CLICK_CONTEXT_SETTINGS)
def cli_fail() -> None:
    """Trigger the intentional failure helper.

    Why
    ---
    Exercises the error-handling path so users can see how traceback toggles
    influence output.
    """

    _fail()


@cli.command("logdemo", context_settings=CLICK_CONTEXT_SETTINGS)
@click.option(
    "--theme",
    "themes",
    type=click.Choice(sorted(CONSOLE_STYLE_THEMES.keys())),
    multiple=True,
    help="Restrict the demo to specific themes (defaults to all themes).",
)
@click.option(
    "--dump-format",
    type=click.Choice(["text", "json", "html_table", "html_txt"]),
    help="Render the emitted events into the selected format after emission.",
)
@click.option(
    "--dump-path",
    type=click.Path(path_type=Path),
    help="Optional file or directory used when writing dumps per theme.",
)
@click.option(
    "--console-format-preset",
    type=click.Choice(["full", "short", "full_loc", "short_loc"], case_sensitive=False),
    help="Preset console layout to use during the demo (default inherits runtime).",
)
@click.option(
    "--console-format-template",
    help="Custom console format template overriding the preset when provided.",
)
@click.option(
    "--dump-format-preset",
    type=click.Choice(["full", "short", "full_loc", "short_loc"], case_sensitive=False),
    help="Preset used when rendering text dumps (default inherits runtime).",
)
@click.option(
    "--dump-format-template",
    help="Custom text dump template overriding the preset when provided.",
)
@click.option("--service", default="logdemo", show_default=True, help="Service name bound inside the demo runtime.")
@click.option("--environment", default="demo", show_default=True, help="Environment label used when emitting demo events.")
@click.option("--enable-graylog", is_flag=True, help="Send demo events to Graylog using the configured endpoint.")
@click.option("--graylog-endpoint", help="Graylog endpoint in HOST:PORT form (defaults to 127.0.0.1:12201).")
@click.option("--graylog-protocol", type=click.Choice(["tcp", "udp"]), default="tcp", show_default=True, help="Transport used for Graylog.")
@click.option("--graylog-tls", is_flag=True, help="Enable TLS for the Graylog TCP transport.")
@click.option("--enable-journald", is_flag=True, help="Send events to systemd-journald (Linux only).")
@click.option("--enable-eventlog", is_flag=True, help="Send events to the Windows Event Log (Windows only).")
@click.option("--context-exact", multiple=True, metavar="KEY=VALUE", help="Filter context fields by exact match (logical AND across keys).")
@click.option("--context-contains", multiple=True, metavar="KEY=VALUE", help="Filter context fields by substring match (case-sensitive).")
@click.option("--context-icontains", multiple=True, metavar="KEY=VALUE", help="Filter context fields by case-insensitive substring.")
@click.option("--context-regex", multiple=True, metavar="KEY=PATTERN", help="Filter context fields using regular expressions (uses Python syntax).")
@click.option("--context-extra-exact", multiple=True, metavar="KEY=VALUE", help="Filter context extra metadata by exact match.")
@click.option("--context-extra-contains", multiple=True, metavar="KEY=VALUE", help="Filter context extra metadata by substring.")
@click.option("--context-extra-icontains", multiple=True, metavar="KEY=VALUE", help="Case-insensitive substring filter for context extra metadata.")
@click.option("--context-extra-regex", multiple=True, metavar="KEY=PATTERN", help="Regex filter for context extra metadata.")
@click.option("--extra-exact", multiple=True, metavar="KEY=VALUE", help="Filter event extra payloads by exact match.")
@click.option("--extra-contains", multiple=True, metavar="KEY=VALUE", help="Filter event extra payloads by substring.")
@click.option("--extra-icontains", multiple=True, metavar="KEY=VALUE", help="Case-insensitive substring filter for event extra payloads.")
@click.option("--extra-regex", multiple=True, metavar="KEY=PATTERN", help="Regex filter for event extra payloads.")
@click.pass_context
def cli_logdemo(
    ctx: click.Context,
    *,
    themes: tuple[str, ...],
    dump_format: str | None,
    dump_path: Path | None,
    console_format_preset: str | None,
    console_format_template: str | None,
    dump_format_preset: str | None,
    dump_format_template: str | None,
    service: str,
    environment: str,
    enable_graylog: bool,
    graylog_endpoint: str | None,
    graylog_protocol: str,
    graylog_tls: bool,
    enable_journald: bool,
    enable_eventlog: bool,
    context_exact: tuple[str, ...],
    context_contains: tuple[str, ...],
    context_icontains: tuple[str, ...],
    context_regex: tuple[str, ...],
    context_extra_exact: tuple[str, ...],
    context_extra_contains: tuple[str, ...],
    context_extra_icontains: tuple[str, ...],
    context_extra_regex: tuple[str, ...],
    extra_exact: tuple[str, ...],
    extra_contains: tuple[str, ...],
    extra_icontains: tuple[str, ...],
    extra_regex: tuple[str, ...],
) -> None:
    """Preview console themes and optionally persist rendered dumps.

    Why
    ---
    Gives users a safe playground for testing console palettes, Graylog wiring,
    and dump formats without instrumenting their applications.

    What
    ----
    Iterates through the requested themes, prints style mappings, reuses
    :func:`logdemo` to emit sample events, and reports which backends were
    exercised.

    Parameters
    ----------
    themes:
        Optional subset of themes; empty tuple means all themes.
    dump_format:
        Optional format name for dumps generated per theme.
    dump_path:
        Destination directory or file for persisted dumps.
    console_format_preset, console_format_template:
        Optional overrides forwarded to :func:`logdemo` to control console line
        rendering. Templates win over presets.
    dump_format_preset, dump_format_template:
        Optional overrides forwarded to :func:`logdemo` for text dump layout.
    service, environment:
        Metadata forwarded to :func:`logdemo` for each run.
    enable_graylog, graylog_endpoint, graylog_protocol, graylog_tls:
        Graylog configuration mirroring the command-line flags.
    enable_journald, enable_eventlog:
        Platform adapter toggles passed through to :func:`logdemo`.

    context_* / context_extra_* / extra_*:
        Filter predicates forwarded to :func:`logdemo` to limit dump output.

    Side Effects
    ------------
    Prints diagnostic information, may create dump files, and may emit events to
    external logging systems depending on the flags.
    """
    # Build filter mappings from CLI filter options
    context_filters, context_extra_filters, extra_filters = _build_dump_filters(
        context_exact=context_exact,
        context_contains=context_contains,
        context_icontains=context_icontains,
        context_regex=context_regex,
        context_extra_exact=context_extra_exact,
        context_extra_contains=context_extra_contains,
        context_extra_icontains=context_extra_icontains,
        context_extra_regex=context_extra_regex,
        extra_exact=extra_exact,
        extra_contains=extra_contains,
        extra_icontains=extra_icontains,
        extra_regex=extra_regex,
    )

    # Resolve format presets from CLI args and context inheritance
    console_format_preset, console_format_template = _resolve_format_presets(ctx, console_format_preset, console_format_template)

    # Prepare theme list and dump state
    selected_themes = [name.lower() for name in themes] if themes else list(CONSOLE_STYLE_THEMES.keys())
    dumps: list[tuple[str, str]] = []
    base_path = dump_path.expanduser() if dump_path is not None else None
    endpoint_tuple = _parse_graylog_endpoint(graylog_endpoint)

    # Iterate through themes, run demo for each
    for theme_name in selected_themes:
        _print_theme_styles(theme_name, CONSOLE_STYLE_THEMES[theme_name])

        # Determine dump target path for this theme
        target_path: Optional[Path] = None
        if dump_format and base_path is not None:
            target_path = _resolve_dump_path(base_path, theme_name, dump_format)
            target_path.parent.mkdir(parents=True, exist_ok=True)

        # Run demo for this theme
        result = _run_theme_demo(
            theme_name=theme_name,
            service=service,
            environment=environment,
            dump_format=dump_format,
            target_path=target_path,
            console_format_preset=console_format_preset,
            console_format_template=console_format_template,
            dump_format_preset=dump_format_preset,
            dump_format_template=dump_format_template,
            enable_graylog=enable_graylog,
            graylog_endpoint=endpoint_tuple,
            graylog_protocol=graylog_protocol,
            graylog_tls=graylog_tls,
            enable_journald=enable_journald,
            enable_eventlog=enable_eventlog,
            context_filters=context_filters,
            context_extra_filters=context_extra_filters,
            extra_filters=extra_filters,
        )

        # Report results
        events = result["events"]
        click.echo(f"  emitted {len(events)} events")
        _print_backend_status(
            enable_graylog=enable_graylog,
            enable_journald=enable_journald,
            enable_eventlog=enable_eventlog,
            endpoint_tuple=endpoint_tuple,
            graylog_protocol=graylog_protocol,
            graylog_tls=graylog_tls,
        )

        # Handle dump output
        if target_path is not None:
            click.echo(f"  dump written to {target_path}")
        elif dump_format and result.get("dump"):
            dumps.append((theme_name, result["dump"]))

        click.echo()

    # Print accumulated dumps to console
    if dump_format and dumps:
        for theme_name, payload in dumps:
            click.echo(click.style(f"--- dump ({dump_format}) theme={theme_name} ---", bold=True))
            click.echo(payload)
            click.echo()


@cli.command("stresstest", context_settings=CLICK_CONTEXT_SETTINGS)
def cli_stresstest() -> None:
    """Launch the interactive stress-test TUI (requires textual)."""

    from .cli_stresstest import run as run_stresstest

    run_stresstest()


def main(argv: Optional[Sequence[str]] = None, *, restore_traceback: bool = True) -> int:
    """Execute the CLI under ``cli_session`` management and return the exit code.

    Why
    ---
    Allows tests and auxiliary tooling to run the CLI with the same managed
    traceback handling used by console scripts and module execution.

    What
    ----
    Opens a :func:`lib_cli_exit_tools.cli_session`, passing through the
    project's traceback character limits and deferring exception formatting to
    the shared handler. When ``restore_traceback`` is ``True`` the session will
    restore the prior configuration once the command exits.

    Parameters
    ----------
    argv:
        Optional argument list overriding ``sys.argv[1:]``.
    restore_traceback:
        When ``True`` resets ``lib_cli_exit_tools`` configuration after running
        the command.

    Returns
    -------
    int
        Process exit code representing success or the mapped error state.
    """

    session = cast(
        AbstractContextManager[Callable[..., int]],
        lib_cli_exit_tools.cli_session(
            summary_limit=_TRACEBACK_SUMMARY_LIMIT,
            verbose_limit=_TRACEBACK_VERBOSE_LIMIT,
            restore=restore_traceback,
        ),
    )

    with session as run:
        result = run(
            cli,
            argv=list(argv) if argv is not None else None,
            prog_name=__init__conf__.shell_command,
        )
    return result
