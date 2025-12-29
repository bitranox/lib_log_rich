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
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Final, cast

import lib_cli_exit_tools
import rich_click as click
from click.core import ParameterSource

from . import __init__conf__
from . import config as config_module
from .adapters.console.rich_console import CONSOLE_PRESETS
from .demo import LogDemoResult
from .domain.dump_filter import FilterSpecValue
from .domain.palettes import CONSOLE_STYLE_THEMES
from .lib_log_rich import (
    hello_world as _hello_world,
)
from .lib_log_rich import (
    i_should_fail as _fail,
)
from .lib_log_rich import (
    logdemo as _logdemo,
)
from .lib_log_rich import (
    summary_info as _summary_info,
)

CLICK_CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])  # noqa: C408
# Show a concise excerpt by default so CLI errors remain readable.
_TRACEBACK_SUMMARY_LIMIT: Final[int] = 500
# Allow opt-in verbose mode to print large tracebacks for debugging sessions.
_TRACEBACK_VERBOSE_LIMIT: Final[int] = 10_000
TRACEBACK_SUMMARY_LIMIT: Final[int] = _TRACEBACK_SUMMARY_LIMIT
TRACEBACK_VERBOSE_LIMIT: Final[int] = _TRACEBACK_VERBOSE_LIMIT


def _dump_extension(fmt: str) -> str:
    """Return the file extension for ``fmt``.

    Keeps CLI behaviour consistent with dump adapter naming conventions.

    Args:
        fmt: Human-entered format name (case-insensitive).

    Returns:
        File extension beginning with a dot.

    Example:
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


def _resolve_dump_path(base: Path, preset: str, theme: str, fmt: str) -> Path:
    """Derive a per-preset-theme path from ``base`` and ``fmt``.

    Centralises the filesystem rules documented in `EXAMPLES.md` so repeated
    logdemo invocations do not overwrite each other unexpectedly.

    Args:
        base: Target directory or file supplied via ``--dump-path``.
        preset: Preset identifier used in the filename.
        theme: Theme identifier used in the filename.
        fmt: Dump format string passed to :func:`_dump_extension`.

    Returns:
        Fully-qualified path ready for writing.

    Example:
        >>> from pathlib import Path
        >>> _resolve_dump_path(Path('.'), 'short', 'classic', 'text')  # doctest: +SKIP
        PosixPath('logdemo-short-classic.log')

    """
    base = base.expanduser()
    extension = _dump_extension(fmt)
    combo = f"{preset}-{theme}"

    if base.exists() and base.is_dir():
        return base / f"logdemo-{combo}{extension}"

    if base.suffix:
        parent = base.parent if base.parent != Path("") else Path(".")
        parent.mkdir(parents=True, exist_ok=True)
        return parent / f"{base.stem}-{combo}{base.suffix}"

    base.mkdir(parents=True, exist_ok=True)
    return base / f"logdemo-{combo}{extension}"


def _parse_graylog_endpoint(value: str | None) -> tuple[str, int] | None:
    """Normalise ``HOST:PORT`` strings for Graylog targets.

    Shares parsing logic between the CLI and :func:`logdemo`, ensuring helpful
    error messages when arguments are malformed.

    Args:
        value: Raw string supplied via ``--graylog-endpoint``.

    Returns:
        Parsed endpoint or ``None`` when ``value`` is ``None``.

    Raises:
        click.BadParameter: If the string is not of the form ``HOST:PORT``.

    Example:
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

    Args:
        context_exact: Exact match filters for context fields.
        context_contains: Substring match filters for context fields.
        context_icontains: Case-insensitive substring filters for context fields.
        context_regex: Regex pattern filters for context fields.
        context_extra_exact: Exact match filters for context extra fields.
        context_extra_contains: Substring match filters for context extra fields.
        context_extra_icontains: Case-insensitive substring filters for context extra.
        context_extra_regex: Regex pattern filters for context extra fields.
        extra_exact: Exact match filters for event extra payloads.
        extra_contains: Substring match filters for event extra payloads.
        extra_icontains: Case-insensitive substring filters for event extra.
        extra_regex: Regex pattern filters for event extra payloads.

    Returns:
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

    Args:
        ctx: Click context containing inherited values.
        console_format_preset: Explicit preset from CLI argument.
        console_format_template: Explicit template from CLI argument.

    Returns:
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

    Args:
        theme_name: Name of the theme being displayed.
        styles: Level->style mapping for this theme.

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

    Args:
        enable_graylog: Whether Graylog adapter is enabled.
        enable_journald: Whether journald adapter is enabled.
        enable_eventlog: Whether Windows Event Log adapter is enabled.
        endpoint_tuple: Graylog (host, port) if configured.
        graylog_protocol: Graylog protocol ('tcp' or 'udp').
        graylog_tls: Whether TLS is enabled for Graylog.

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
) -> LogDemoResult:
    """Execute logdemo for a single theme and return results.

    Args:
        theme_name: Theme to use for this demo run.
        service: Service name for log context.
        environment: Environment label for log context.
        dump_format: Optional dump format name.
        target_path: Optional dump target path.
        console_format_preset: Console output format preset.
        console_format_template: Console output format template.
        dump_format_preset: Dump output format preset.
        dump_format_template: Dump output format template.
        enable_graylog: Whether Graylog adapter is enabled.
        graylog_endpoint: Graylog (host, port) if configured.
        graylog_protocol: Graylog protocol ('tcp' or 'udp').
        graylog_tls: Whether TLS is enabled for Graylog.
        enable_journald: Whether journald adapter is enabled.
        enable_eventlog: Whether Windows Event Log adapter is enabled.
        context_filters: Filter mappings for context fields.
        context_extra_filters: Filter mappings for context extra fields.
        extra_filters: Filter mappings for event extra payloads.

    Returns:
        LogDemoResult from _logdemo containing events, dump, and metadata.

    Raises:
        click.ClickException: If _logdemo raises ValueError.

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


def _resolve_combo_dump_path(
    base_path: Path | None,
    preset_name: str,
    theme_name: str,
    dump_format: str | None,
) -> Path | None:
    """Determine dump target path for a preset-theme combo and ensure parent exists."""
    if not dump_format or base_path is None:
        return None
    target_path = _resolve_dump_path(base_path, preset_name, theme_name, dump_format)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    return target_path


def _report_combo_result(
    result: LogDemoResult,
    target_path: Path | None,
    dump_format: str | None,
    preset_name: str,
    theme_name: str,
    dumps: list[tuple[str, str, str]],
    enable_graylog: bool,
    enable_journald: bool,
    enable_eventlog: bool,
    endpoint_tuple: tuple[str, int] | None,
    graylog_protocol: str,
    graylog_tls: bool,
) -> None:
    """Report demo results and collect dumps for later printing."""
    click.echo(f"  emitted {len(result.events)} events")
    _print_backend_status(
        enable_graylog=enable_graylog,
        enable_journald=enable_journald,
        enable_eventlog=enable_eventlog,
        endpoint_tuple=endpoint_tuple,
        graylog_protocol=graylog_protocol,
        graylog_tls=graylog_tls,
    )
    if target_path is not None:
        click.echo(f"  dump written to {target_path}")
    elif dump_format and result.dump:
        dumps.append((preset_name, theme_name, result.dump))
    click.echo()


def _print_accumulated_dumps(dumps: list[tuple[str, str, str]], dump_format: str) -> None:
    """Print all accumulated dumps to console."""
    for preset_name, theme_name, payload in dumps:
        click.echo(click.style(f"--- dump ({dump_format}) preset={preset_name} theme={theme_name} ---", bold=True))
        click.echo(payload)
        click.echo()


def _select_themes(themes: tuple[str, ...]) -> list[str]:
    """Return list of themes to demo, defaulting to all available themes."""
    if themes:
        return [name.lower() for name in themes]
    return list(CONSOLE_STYLE_THEMES.keys())


def _select_presets(presets: tuple[str, ...]) -> list[str]:
    """Return list of presets to demo, defaulting to all available presets."""
    if presets:
        return [name.lower() for name in presets]
    return list(CONSOLE_PRESETS)


def _iterate_presets_and_themes(
    *,
    selected_presets: list[str],
    selected_themes: list[str],
    base_path: Path | None,
    dump_format: str | None,
    service: str,
    environment: str,
    console_format_template: str | None,
    dump_format_preset: str | None,
    dump_format_template: str | None,
    enable_graylog: bool,
    endpoint_tuple: tuple[str, int] | None,
    graylog_protocol: str,
    graylog_tls: bool,
    enable_journald: bool,
    enable_eventlog: bool,
    context_filters: FilterMapping | None,
    context_extra_filters: FilterMapping | None,
    extra_filters: FilterMapping | None,
) -> list[tuple[str, str, str]]:
    """Iterate through preset-theme combinations, run demo for each, and collect dumps.

    Args:
        selected_presets: List of preset names to demo.
        selected_themes: List of theme names to demo.
        base_path: Base path for dump files.
        dump_format: Format for dumps (text, json, etc.).
        service: Service name for log context.
        environment: Environment label for log context.
        console_format_template: Console output format template (overrides preset).
        dump_format_preset: Dump output format preset.
        dump_format_template: Dump output format template.
        enable_graylog: Whether Graylog adapter is enabled.
        endpoint_tuple: Graylog (host, port) if configured.
        graylog_protocol: Graylog protocol.
        graylog_tls: Whether TLS is enabled for Graylog.
        enable_journald: Whether journald adapter is enabled.
        enable_eventlog: Whether Windows Event Log adapter is enabled.
        context_filters: Filter mappings for context fields.
        context_extra_filters: Filter mappings for context extra fields.
        extra_filters: Filter mappings for event extra payloads.

    Returns:
        List of (preset_name, theme_name, dump_content) tuples for console output.

    """
    dumps: list[tuple[str, str, str]] = []
    for preset_name in selected_presets:
        for theme_name in selected_themes:
            click.echo(click.style(f"=== Preset: {preset_name}, Theme: {theme_name} ===", bold=True))
            _print_theme_styles(theme_name, CONSOLE_STYLE_THEMES[theme_name])
            target_path = _resolve_combo_dump_path(base_path, preset_name, theme_name, dump_format)
            result = _run_theme_demo(
                theme_name=theme_name,
                service=service,
                environment=environment,
                dump_format=dump_format,
                target_path=target_path,
                console_format_preset=preset_name,
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
            _report_combo_result(
                result=result,
                target_path=target_path,
                dump_format=dump_format,
                preset_name=preset_name,
                theme_name=theme_name,
                dumps=dumps,
                enable_graylog=enable_graylog,
                enable_journald=enable_journald,
                enable_eventlog=enable_eventlog,
                endpoint_tuple=endpoint_tuple,
                graylog_protocol=graylog_protocol,
                graylog_tls=graylog_tls,
            )
    return dumps


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
    type=click.Choice(["full", "short", "full_loc", "short_loc", "short_loc_icon"], case_sensitive=False),
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

    Acts as the entry point for the console script, wiring environment toggles
    and the default behaviour described in the CLI design notes. Optionally
    loads ``.env`` files, persists traceback preferences, and invokes the
    requested subcommand (or prints the banner when none is provided).

    Args:
        ctx: Click context used to persist state between callbacks.
        use_dotenv: Boolean toggle derived from ``--use-dotenv``.
        hello: Whether to print the hello-world stub before the banner when no
            subcommand is invoked.
        traceback: Enables verbose tracebacks for subsequent command execution.
        console_format_preset: Preset layout forwarded to subcommands.
        console_format_template: Custom format template forwarded to subcommands.
        queue_stop_timeout: Override for the default queue drain timeout.

    Note:
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

    Matches the scaffold behaviour so ``lib_log_rich`` without
    arguments prints install metadata.

    Example:
        >>> from unittest import mock
        >>> with mock.patch('lib_log_rich.cli.click.echo') as echo:
        ...     cli_main()
        >>> echo.assert_called()

    """
    click.echo(_summary_info(), nl=False)


@cli.command("info", context_settings=CLICK_CONTEXT_SETTINGS)
def cli_info() -> None:
    """Print resolved metadata so users can inspect installation details.

    Provides an explicit command for scripts tooling to read the metadata banner
    without invoking other demo behaviour.
    """
    click.echo(_summary_info(), nl=False)


@cli.command("hello", context_settings=CLICK_CONTEXT_SETTINGS)
def cli_hello() -> None:
    """Demonstrate the success path stub.

    Maintains compatibility with the scaffold's hello-world example used in
    documentation and smoke tests.
    """
    _hello_world()


@cli.command("fail", context_settings=CLICK_CONTEXT_SETTINGS)
def cli_fail() -> None:
    """Trigger the intentional failure helper.

    Exercises the error-handling path so users can see how traceback toggles
    influence output.
    """
    _fail()


@cli.command("logdemo", context_settings=CLICK_CONTEXT_SETTINGS)
@click.option(
    "--preset",
    "presets",
    type=click.Choice(["full", "short", "full_loc", "short_loc", "short_loc_icon"], case_sensitive=False),
    multiple=True,
    help="Restrict the demo to specific presets (defaults to all presets).",
)
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
    help="Optional file or directory used when writing dumps per preset-theme combination.",
)
@click.option(
    "--console-format-template",
    help="Custom console format template overriding the preset when provided.",
)
@click.option(
    "--dump-format-preset",
    type=click.Choice(["full", "short", "full_loc", "short_loc", "short_loc_icon"], case_sensitive=False),
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
    presets: tuple[str, ...],
    themes: tuple[str, ...],
    dump_format: str | None,
    dump_path: Path | None,
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
    """Preview console presets and themes, optionally persist rendered dumps.

    Gives users a safe playground for testing console layouts and palettes,
    Graylog wiring, and dump formats without instrumenting their applications.
    Iterates through all preset × theme combinations, prints style mappings,
    reuses :func:`logdemo` to emit sample events, and reports which backends
    were exercised.

    Args:
        ctx: Click context object (unused, required by decorator).
        presets: Optional subset of presets; empty tuple means all presets.
        themes: Optional subset of themes; empty tuple means all themes.
        dump_format: Optional format name for dumps generated per combo.
        dump_path: Destination directory or file for persisted dumps.
        console_format_template: Custom template overriding preset when provided.
        dump_format_preset: Optional override for text dump layout.
        dump_format_template: Custom template for text dump layout.
        service: Service name for log context.
        environment: Environment label for log context.
        enable_graylog: Whether to send demo events to Graylog.
        graylog_endpoint: Graylog endpoint in HOST:PORT form.
        graylog_protocol: Transport used for Graylog ('tcp' or 'udp').
        graylog_tls: Whether TLS is enabled for Graylog TCP transport.
        enable_journald: Whether to send events to systemd-journald.
        enable_eventlog: Whether to send events to Windows Event Log.
        context_exact: Exact match filters for context fields.
        context_contains: Substring filters for context fields.
        context_icontains: Case-insensitive substring filters for context fields.
        context_regex: Regex filters for context fields.
        context_extra_exact: Exact match filters for context extra fields.
        context_extra_contains: Substring filters for context extra fields.
        context_extra_icontains: Case-insensitive substring filters for context extra.
        context_extra_regex: Regex filters for context extra fields.
        extra_exact: Exact match filters for event extra payloads.
        extra_contains: Substring filters for event extra payloads.
        extra_icontains: Case-insensitive substring filters for event extra.
        extra_regex: Regex filters for event extra payloads.

    Note:
        Prints diagnostic information, may create dump files, and may emit events
        to external logging systems depending on the flags.

    """
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
    _, console_format_template = _resolve_format_presets(ctx, None, console_format_template)
    dumps = _iterate_presets_and_themes(
        selected_presets=_select_presets(presets),
        selected_themes=_select_themes(themes),
        base_path=dump_path.expanduser() if dump_path is not None else None,
        dump_format=dump_format,
        service=service,
        environment=environment,
        console_format_template=console_format_template,
        dump_format_preset=dump_format_preset,
        dump_format_template=dump_format_template,
        enable_graylog=enable_graylog,
        endpoint_tuple=_parse_graylog_endpoint(graylog_endpoint),
        graylog_protocol=graylog_protocol,
        graylog_tls=graylog_tls,
        enable_journald=enable_journald,
        enable_eventlog=enable_eventlog,
        context_filters=context_filters,
        context_extra_filters=context_extra_filters,
        extra_filters=extra_filters,
    )
    if dump_format and dumps:
        _print_accumulated_dumps(dumps, dump_format)


@cli.command("stresstest", context_settings=CLICK_CONTEXT_SETTINGS)
def cli_stresstest() -> None:
    """Launch the interactive stress-test TUI (requires textual)."""
    from .cli_stresstest import run as run_stresstest

    run_stresstest()


def main(argv: Sequence[str] | None = None, *, restore_traceback: bool = True) -> int:
    """Execute the CLI under ``cli_session`` management and return the exit code.

    Allows tests and auxiliary tooling to run the CLI with the same managed
    traceback handling used by console scripts and module execution. Opens a
    :func:`lib_cli_exit_tools.cli_session`, passing through the project's
    traceback character limits and deferring exception formatting to the
    shared handler.

    Args:
        argv: Optional argument list overriding ``sys.argv[1:]``.
        restore_traceback: When ``True`` resets ``lib_cli_exit_tools``
            configuration after running the command.

    Returns:
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
