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
* :data:`CLICK_CONTEXT_SETTINGS` - shared configuration exposing ``-h`` and
  ``--help`` across commands.
* :func:`cli` - root command wiring the traceback toggle and metadata banner.
* :func:`cli_main` - prints the metadata banner when no subcommand is used.
* :func:`cli_info`, :func:`cli_hello`, :func:`cli_fail`, :func:`cli_logdemo`
  - subcommands for metadata, success path, failure path, and the demo.
* :func:`main` - composition helper delegating to ``lib_cli_exit_tools``.

System Role
-----------
Acts as the primary presentation-layer adapter. Packaging registers the
`lib_log_rich` console script which ultimately calls this module.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

import lib_cli_exit_tools
import rich_click as click
from click.core import ParameterSource

from . import __init__conf__, safe_console
from . import config as config_module
from .domain.dump import DumpFormat
from .domain.dump_filter import FilterSpecValue
from .domain.enums import ConsoleTheme, FormatPreset, GraylogProtocol
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
from .typed_click import option, version_option

if TYPE_CHECKING:
    from .demo import LogDemoResult

CLICK_CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])  # noqa: C408
# Show a concise excerpt by default so CLI errors remain readable.
_TRACEBACK_SUMMARY_LIMIT: Final[int] = 500
# Allow opt-in verbose mode to print large tracebacks for debugging sessions.
_TRACEBACK_VERBOSE_LIMIT: Final[int] = 10_000


# Choice lists are built from the enum VALUES: click.Choice matches an Enum member
# by its NAME, so passing the members would reject the lowercase spellings users type.
_FORMAT_PRESET_CHOICES: Final[tuple[str, ...]] = tuple(preset.value for preset in FormatPreset)
_DUMP_FORMAT_PRESET_CHOICES: Final[tuple[str, ...]] = tuple(preset.value for preset in FormatPreset if preset.renders_in_text_dump)
_DUMP_FORMAT_CHOICES: Final[tuple[str, ...]] = tuple(fmt.value for fmt in DumpFormat)
_GRAYLOG_PROTOCOL_CHOICES: Final[tuple[str, ...]] = tuple(protocol.value for protocol in GraylogProtocol)
_THEME_CHOICES: Final[tuple[str, ...]] = tuple(theme.value for theme in ConsoleTheme)

_DUMP_EXTENSIONS: Final[Mapping[DumpFormat, str]] = {
    DumpFormat.TEXT: ".log",
    DumpFormat.JSON: ".json",
    DumpFormat.HTML_TABLE: ".html",
    DumpFormat.HTML_TXT: ".html",
}


@dataclass(slots=True, frozen=True)
class _RootOptions:
    """Options the root group hands down to its subcommands through ``ctx.obj``."""

    traceback: bool
    console_format_template: str | None = None


@dataclass(slots=True, frozen=True)
class _ComboDump:
    """A dump rendered for one preset-theme combination, printed after the run."""

    preset: FormatPreset
    theme: ConsoleTheme
    payload: str


def _to_format_preset(_ctx: click.Context, _param: click.Parameter, value: str | None) -> FormatPreset | None:
    """Convert a ``--*-format-preset`` choice into its :class:`FormatPreset`."""
    return None if value is None else FormatPreset.from_str(value)


def _to_format_presets(_ctx: click.Context, _param: click.Parameter, values: tuple[str, ...]) -> tuple[FormatPreset, ...]:
    """Convert the repeated ``--preset`` choices into :class:`FormatPreset` members."""
    return tuple(FormatPreset.from_str(value) for value in values)


def _to_themes(_ctx: click.Context, _param: click.Parameter, values: tuple[str, ...]) -> tuple[ConsoleTheme, ...]:
    """Convert the repeated ``--theme`` choices into :class:`ConsoleTheme` members."""
    return tuple(ConsoleTheme.from_str(value) for value in values)


def _to_dump_format(_ctx: click.Context, _param: click.Parameter, value: str | None) -> DumpFormat | None:
    """Convert the ``--dump-format`` choice into its :class:`DumpFormat`."""
    return None if value is None else DumpFormat(value)


def _to_graylog_protocol(_ctx: click.Context, _param: click.Parameter, value: str) -> GraylogProtocol:
    """Convert the ``--graylog-protocol`` choice into its :class:`GraylogProtocol`."""
    return GraylogProtocol.from_str(value)


def _dump_extension(fmt: DumpFormat) -> str:
    """Return the file extension for ``fmt``.

    Keeps CLI behaviour consistent with dump adapter naming conventions.

    Args:
        fmt: Dump format the file is written in.

    Returns:
        File extension beginning with a dot.

    Example:
        >>> _dump_extension(DumpFormat.TEXT)
        '.log'
        >>> _dump_extension(DumpFormat.HTML_TXT)
        '.html'

    """
    return _DUMP_EXTENSIONS[fmt]


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
        # A compiled pattern is itself a regex spec, so no option mapping is needed.
        _append_filter_spec(filters, key, pattern)
    return filters


def _none_if_empty(mapping: dict[str, FilterSpecValue]) -> FilterMapping | None:
    """Return ``None`` when ``mapping`` is empty."""
    return mapping or None


def _resolve_dump_path(base: Path, preset: FormatPreset, theme: ConsoleTheme, fmt: DumpFormat) -> Path:
    """Derive a per-preset-theme path from ``base`` and ``fmt``.

    Centralises the filesystem rules documented in `EXAMPLES.md` so repeated
    logdemo invocations do not overwrite each other unexpectedly.

    Args:
        base: Target directory or file supplied via ``--dump-path``.
        preset: Preset whose value is used in the filename.
        theme: Theme whose value is used in the filename.
        fmt: Dump format passed to :func:`_dump_extension`.

    Returns:
        Fully-qualified path ready for writing.

    Example:
        >>> from pathlib import Path
        >>> _resolve_dump_path(Path('.'), FormatPreset.SHORT, ConsoleTheme.CLASSIC, DumpFormat.TEXT)  # doctest: +SKIP
        PosixPath('logdemo-short-classic.log')

    """
    base = base.expanduser()
    extension = _dump_extension(fmt)
    combo = f"{preset}-{theme}"

    if base.exists() and base.is_dir():
        return base / f"logdemo-{combo}{extension}"

    if base.suffix:
        parent = base.parent if base.parent != Path() else Path()
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


def _resolve_format_template(ctx: click.Context, console_format_template: str | None) -> str | None:
    """Return the subcommand's own template, else the one given to the root group.

    Args:
        ctx: Click context holding the root group's options.
        console_format_template: Template given to the subcommand itself.

    Returns:
        The template to use, or ``None`` when neither command was given one.

    """
    if console_format_template is not None:
        return console_format_template
    inherited = ctx.find_object(_RootOptions)
    return None if inherited is None else inherited.console_format_template


def _print_theme_styles(theme: ConsoleTheme) -> None:
    """Print theme header and style mappings to console.

    Args:
        theme: Theme whose palette is displayed.

    """
    safe_console.echo(click.style(f"=== Theme: {theme} ===", bold=True))
    for level, style in CONSOLE_STYLE_THEMES[theme].items():
        safe_console.echo(f"  {level:<8} -> {style}")
    safe_console.echo("  emitting sample events…")


def _print_backend_status(
    *,
    enable_graylog: bool,
    enable_journald: bool,
    enable_eventlog: bool,
    endpoint_tuple: tuple[str, int] | None,
    graylog_protocol: GraylogProtocol,
    graylog_tls: bool,
) -> None:
    """Print status of enabled backend adapters.

    Args:
        enable_graylog: Whether Graylog adapter is enabled.
        enable_journald: Whether journald adapter is enabled.
        enable_eventlog: Whether Windows Event Log adapter is enabled.
        endpoint_tuple: Graylog (host, port) if configured.
        graylog_protocol: Graylog transport protocol.
        graylog_tls: Whether TLS is enabled for Graylog.

    """
    if enable_graylog:
        destination = endpoint_tuple or ("127.0.0.1", 12201)
        tls_suffix = "+TLS" if graylog_tls and graylog_protocol is GraylogProtocol.TCP else ""
        scheme = graylog_protocol.value.upper() + tls_suffix
        safe_console.echo(f"  graylog -> {destination[0]}:{destination[1]} via {scheme}")
    if enable_journald:
        safe_console.echo("  journald -> systemd.journal.send")
    if enable_eventlog:
        safe_console.echo("  eventlog -> Windows Event Log")


def _run_theme_demo(
    *,
    theme: ConsoleTheme,
    service: str,
    environment: str,
    dump_format: DumpFormat | None,
    target_path: Path | None,
    console_format_preset: FormatPreset | None,
    console_format_template: str | None,
    dump_format_preset: FormatPreset | None,
    dump_format_template: str | None,
    enable_graylog: bool,
    graylog_endpoint: tuple[str, int] | None,
    graylog_protocol: GraylogProtocol,
    graylog_tls: bool,
    enable_journald: bool,
    enable_eventlog: bool,
    context_filters: FilterMapping | None,
    context_extra_filters: FilterMapping | None,
    extra_filters: FilterMapping | None,
) -> LogDemoResult:
    """Execute logdemo for a single theme and return results.

    Args:
        theme: Theme to use for this demo run.
        service: Service name for log context.
        environment: Environment label for log context.
        dump_format: Optional dump format.
        target_path: Optional dump target path.
        console_format_preset: Console output format preset.
        console_format_template: Console output format template.
        dump_format_preset: Dump output format preset.
        dump_format_template: Dump output format template.
        enable_graylog: Whether Graylog adapter is enabled.
        graylog_endpoint: Graylog (host, port) if configured.
        graylog_protocol: Graylog transport protocol.
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
            theme=theme,
            service=service,
            environment=f"{environment}-{theme}" if environment else None,
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
    preset: FormatPreset,
    theme: ConsoleTheme,
    dump_format: DumpFormat | None,
) -> Path | None:
    """Determine dump target path for a preset-theme combo and ensure parent exists."""
    if dump_format is None or base_path is None:
        return None
    target_path = _resolve_dump_path(base_path, preset, theme, dump_format)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    return target_path


def _report_combo_result(
    *,
    result: LogDemoResult,
    target_path: Path | None,
    dump_format: DumpFormat | None,
    preset: FormatPreset,
    theme: ConsoleTheme,
    dumps: list[_ComboDump],
    enable_graylog: bool,
    enable_journald: bool,
    enable_eventlog: bool,
    endpoint_tuple: tuple[str, int] | None,
    graylog_protocol: GraylogProtocol,
    graylog_tls: bool,
) -> None:
    """Report demo results and collect dumps for later printing."""
    safe_console.echo(f"  emitted {len(result.events)} events")
    _print_backend_status(
        enable_graylog=enable_graylog,
        enable_journald=enable_journald,
        enable_eventlog=enable_eventlog,
        endpoint_tuple=endpoint_tuple,
        graylog_protocol=graylog_protocol,
        graylog_tls=graylog_tls,
    )
    if target_path is not None:
        safe_console.echo(f"  dump written to {target_path}")
    elif dump_format is not None and result.dump:
        dumps.append(_ComboDump(preset=preset, theme=theme, payload=result.dump))
    safe_console.echo()


def _print_accumulated_dumps(dumps: list[_ComboDump], dump_format: DumpFormat) -> None:
    """Print all accumulated dumps to console."""
    for combo in dumps:
        # ``.value``: DumpFormat is a plain Enum, whose f-string form is ``DumpFormat.JSON``.
        header = f"--- dump ({dump_format.value}) preset={combo.preset} theme={combo.theme} ---"
        safe_console.echo(click.style(header, bold=True))
        safe_console.echo(combo.payload)
        safe_console.echo()


def _select_themes(themes: tuple[ConsoleTheme, ...]) -> list[ConsoleTheme]:
    """Return list of themes to demo, defaulting to all available themes."""
    if themes:
        return list(themes)
    return list(ConsoleTheme)


def _select_presets(presets: tuple[FormatPreset, ...]) -> list[FormatPreset]:
    """Return list of presets to demo, defaulting to all available presets."""
    if presets:
        return list(presets)
    return list(FormatPreset)


def _iterate_presets_and_themes(
    *,
    selected_presets: list[FormatPreset],
    selected_themes: list[ConsoleTheme],
    base_path: Path | None,
    dump_format: DumpFormat | None,
    service: str,
    environment: str,
    console_format_template: str | None,
    dump_format_preset: FormatPreset | None,
    dump_format_template: str | None,
    enable_graylog: bool,
    endpoint_tuple: tuple[str, int] | None,
    graylog_protocol: GraylogProtocol,
    graylog_tls: bool,
    enable_journald: bool,
    enable_eventlog: bool,
    context_filters: FilterMapping | None,
    context_extra_filters: FilterMapping | None,
    extra_filters: FilterMapping | None,
) -> list[_ComboDump]:
    """Iterate through preset-theme combinations, run demo for each, and collect dumps.

    Args:
        selected_presets: Presets to demo.
        selected_themes: Themes to demo.
        base_path: Base path for dump files.
        dump_format: Format for dumps, or ``None`` for no dump.
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
        Dumps rendered per combination that were not written to disk, for console output.

    """
    dumps: list[_ComboDump] = []
    for preset in selected_presets:
        for theme in selected_themes:
            safe_console.echo(click.style(f"=== Preset: {preset}, Theme: {theme} ===", bold=True))
            _print_theme_styles(theme)
            target_path = _resolve_combo_dump_path(base_path, preset, theme, dump_format)
            result = _run_theme_demo(
                theme=theme,
                service=service,
                environment=environment,
                dump_format=dump_format,
                target_path=target_path,
                console_format_preset=preset,
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
                preset=preset,
                theme=theme,
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
@version_option(
    version=__init__conf__.version,
    prog_name=__init__conf__.shell_command,
    message=f"{__init__conf__.shell_command} version {__init__conf__.version}",
)
@option(
    "--use-dotenv/--no-use-dotenv",
    default=False,
    help="Load environment variables from a nearby .env before running commands.",
)
@option(
    "--hello",
    is_flag=True,
    help="Print the canonical Hello World greeting before the metadata banner.",
)
@option(
    "--traceback/--no-traceback",
    is_flag=True,
    default=True,
    help="Show full Python traceback on errors (use --no-traceback to suppress).",
)
@option(
    "--console-format-template",
    help="Custom console format template forwarded to subcommands unless overridden.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    *,
    use_dotenv: bool,
    hello: bool,
    traceback: bool,
    console_format_template: str | None,
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
        console_format_template: Custom format template forwarded to subcommands.

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

    ctx.obj = _RootOptions(
        traceback=traceback,
        console_format_template=console_format_template,
    )
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
    safe_console.echo(_summary_info(), nl=False)


@cli.command("info", context_settings=CLICK_CONTEXT_SETTINGS)
def cli_info() -> None:
    """Print resolved metadata so users can inspect installation details.

    Provides an explicit command for scripts tooling to read the metadata banner
    without invoking other demo behaviour.
    """
    safe_console.echo(_summary_info(), nl=False)


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
@option(
    "--preset",
    "presets",
    type=click.Choice(_FORMAT_PRESET_CHOICES, case_sensitive=False),
    callback=_to_format_presets,
    multiple=True,
    help="Restrict the demo to specific presets (defaults to all presets).",
)
@option(
    "--theme",
    "themes",
    type=click.Choice(_THEME_CHOICES, case_sensitive=False),
    callback=_to_themes,
    multiple=True,
    help="Restrict the demo to specific themes (defaults to all themes).",
)
@option(
    "--dump-format",
    type=click.Choice(_DUMP_FORMAT_CHOICES),
    callback=_to_dump_format,
    help="Render the emitted events into the selected format after emission.",
)
@option(
    "--dump-path",
    type=click.Path(path_type=Path),
    help="Optional file or directory used when writing dumps per preset-theme combination.",
)
@option(
    "--console-format-template",
    help="Custom console format template overriding the preset when provided.",
)
@option(
    "--dump-format-preset",
    type=click.Choice(_DUMP_FORMAT_PRESET_CHOICES, case_sensitive=False),
    callback=_to_format_preset,
    help="Preset used when rendering text dumps (default inherits runtime).",
)
@option(
    "--dump-format-template",
    help="Custom text dump template overriding the preset when provided.",
)
@option("--service", default="logdemo", show_default=True, help="Service name bound inside the demo runtime.")
@option("--environment", default="demo", show_default=True, help="Environment label used when emitting demo events.")
@option("--enable-graylog", is_flag=True, help="Send demo events to Graylog using the configured endpoint.")
@option("--graylog-endpoint", help="Graylog endpoint in HOST:PORT form (defaults to 127.0.0.1:12201).")
@option(
    "--graylog-protocol",
    type=click.Choice(_GRAYLOG_PROTOCOL_CHOICES),
    default=GraylogProtocol.TCP.value,
    show_default=True,
    callback=_to_graylog_protocol,
    help="Transport used for Graylog.",
)
@option("--graylog-tls", is_flag=True, help="Enable TLS for the Graylog TCP transport.")
@option("--enable-journald", is_flag=True, help="Send events to systemd-journald (Linux only).")
@option("--enable-eventlog", is_flag=True, help="Send events to the Windows Event Log (Windows only).")
@option("--context-exact", multiple=True, metavar="KEY=VALUE", help="Filter context fields by exact match (logical AND across keys).")
@option("--context-contains", multiple=True, metavar="KEY=VALUE", help="Filter context fields by substring match (case-sensitive).")
@option("--context-icontains", multiple=True, metavar="KEY=VALUE", help="Filter context fields by case-insensitive substring.")
@option("--context-regex", multiple=True, metavar="KEY=PATTERN", help="Filter context fields using regular expressions (uses Python syntax).")
@option("--context-extra-exact", multiple=True, metavar="KEY=VALUE", help="Filter context extra metadata by exact match.")
@option("--context-extra-contains", multiple=True, metavar="KEY=VALUE", help="Filter context extra metadata by substring.")
@option("--context-extra-icontains", multiple=True, metavar="KEY=VALUE", help="Case-insensitive substring filter for context extra metadata.")
@option("--context-extra-regex", multiple=True, metavar="KEY=PATTERN", help="Regex filter for context extra metadata.")
@option("--extra-exact", multiple=True, metavar="KEY=VALUE", help="Filter event extra payloads by exact match.")
@option("--extra-contains", multiple=True, metavar="KEY=VALUE", help="Filter event extra payloads by substring.")
@option("--extra-icontains", multiple=True, metavar="KEY=VALUE", help="Case-insensitive substring filter for event extra payloads.")
@option("--extra-regex", multiple=True, metavar="KEY=PATTERN", help="Regex filter for event extra payloads.")
@click.pass_context
def cli_logdemo(
    ctx: click.Context,
    *,
    presets: tuple[FormatPreset, ...],
    themes: tuple[ConsoleTheme, ...],
    dump_format: DumpFormat | None,
    dump_path: Path | None,
    console_format_template: str | None,
    dump_format_preset: FormatPreset | None,
    dump_format_template: str | None,
    service: str,
    environment: str,
    enable_graylog: bool,
    graylog_endpoint: str | None,
    graylog_protocol: GraylogProtocol,
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
    Iterates through all preset x theme combinations, prints style mappings,
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
        graylog_protocol: Transport used for Graylog.
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
    console_format_template = _resolve_format_template(ctx, console_format_template)
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
    if dump_format is not None and dumps:
        _print_accumulated_dumps(dumps, dump_format)


@cli.command("stresstest", context_settings=CLICK_CONTEXT_SETTINGS)
def cli_stresstest() -> None:
    """Launch the interactive stress-test TUI (requires textual)."""
    from .cli_stresstest import run as run_stresstest  # noqa: PLC0415 - defers the optional textual dependency to this subcommand

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
    with lib_cli_exit_tools.cli_session(
        summary_limit=_TRACEBACK_SUMMARY_LIMIT,
        verbose_limit=_TRACEBACK_VERBOSE_LIMIT,
        restore=restore_traceback,
    ) as run:
        result = run(
            cli,
            argv=list(argv) if argv is not None else None,
            prog_name=__init__conf__.shell_command,
        )
    return result
