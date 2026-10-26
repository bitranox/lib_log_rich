"""Bridge between stdlib logging records and the lib_log_rich runtime pipeline.

Purpose
-------
Expose a drop-in :class:`logging.Handler` that forwards `logging.LogRecord`
instances into the runtime's `process` callable. The adapter keeps the
application layer blissfully unaware of stdlib logging internals while enabling
hosts to keep existing `logging`-based modules untouched.

Contents
--------
* :class:`StdlibLoggingHandler` - normalises `LogRecord` instances and invokes
  the runtime pipeline.
* :func:`attach_std_logging` - convenience bootstrapper to register the handler
  on a target logger (root by default) and optionally tweak levels/propagation.

System Role
-----------
Lives in the runtime layer where outer integrations belong. Clean architecture
is preserved by funnelling data through the already-composed `process` callable
instead of reimplementing the logging pipeline.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, MutableMapping
from typing import TYPE_CHECKING, Any, cast

from lib_log_rich.domain import LogLevel

from ._state import LoggingRuntime, current_runtime, get_minimum_log_level

if TYPE_CHECKING:
    from lib_log_rich.application.use_cases._types import MessageArgs

_IGNORED_LOGGER_NAMESPACE = "lib_log_rich"
_SKIP_ATTR = "lib_log_rich_skip"
_REPORTED_ATTR = "_lib_log_rich_reported"

_BASE_RECORD = logging.LogRecord(
    name="lib_log_rich.bootstrap",
    level=logging.INFO,
    pathname=__file__,
    lineno=0,
    msg="bootstrap",
    args=(),
    exc_info=None,
    func=None,
)
_STANDARD_RECORD_FIELDS = set(_BASE_RECORD.__dict__.keys()) | {"stacklevel"}
_PRESERVED_STANDARD_FIELDS = {
    "pathname",
    "lineno",
    "funcName",
    "filename",
    "module",
}
_EXCLUDED_EXTRA_FIELDS = (_STANDARD_RECORD_FIELDS - _PRESERVED_STANDARD_FIELDS) | {_SKIP_ATTR}


class StdlibLoggingHandler(logging.Handler):
    """Forward stdlib :class:`logging.LogRecord` instances into the runtime.

    Notes
    -----
    Dependency inversion is honoured through the ``runtime_resolver`` callable,
    allowing tests to supply fakes while production resolves the active
    singleton via :func:`current_runtime`.

    """

    def __init__(
        self,
        *,
        runtime_resolver: Callable[[], LoggingRuntime] | None = None,
        namespace: str = _IGNORED_LOGGER_NAMESPACE,
    ) -> None:
        """Initialize the handler with optional runtime resolver and namespace."""
        super().__init__()
        self._resolve_runtime = runtime_resolver or current_runtime
        self._namespace = namespace

    def emit(self, record: logging.LogRecord) -> None:
        """Emit the supplied record by translating it into the runtime pipeline.

        Note:
            This method performs synchronous processing. In async contexts with
            high log volume, consider using queue-based logging adapters to
            prevent event loop blocking.
        """
        if getattr(record, _SKIP_ATTR, False):
            return
        if self._is_own_namespace(record):
            _report_declined_record(record)
            return
        try:
            runtime = self._resolve_runtime()
        except RuntimeError:
            # Runtime not initialised; mirror stdlib behaviour by dropping the record.
            return
        try:
            self._forward(runtime, record)
        except Exception:  # pragma: no cover - defensive; handled by base class
            self.handleError(record)

    def _is_own_namespace(self, record: logging.LogRecord) -> bool:
        name = record.name or ""
        return name == self._namespace or name.startswith(f"{self._namespace}.")

    def _forward(self, runtime: LoggingRuntime, record: logging.LogRecord) -> None:
        """Hand ``record`` to ``runtime.process`` as the pipeline's named arguments."""
        level, fallback = self._coerce_level(record)
        runtime.process(
            logger_name=record.name,
            level=level,
            message=record.msg,
            args=self._normalise_args(record.args),
            exc_info=record.exc_info,
            stack_info=record.stack_info,
            stacklevel=getattr(record, "stacklevel", 1) or 1,
            extra=self._extract_extra(record, level_fallback=fallback),
        )

    def _coerce_level(self, record: logging.LogRecord) -> tuple[LogLevel, bool]:
        try:
            return LogLevel.coerce(record.levelno), False
        except ValueError:
            try:
                return LogLevel.coerce(record.levelname), False
            except ValueError:
                return LogLevel.INFO, True

    def _extract_extra(self, record: logging.LogRecord, *, level_fallback: bool) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in _EXCLUDED_EXTRA_FIELDS:
                continue
            extra[str(key)] = value
        self._ensure_location_fields(record, extra)
        if level_fallback:
            extra.setdefault("stdlib_levelno", record.levelno)
            extra.setdefault("stdlib_levelname", record.levelname)
        if record.exc_text and "exc_text" not in extra:
            extra["exc_text"] = record.exc_text
        return extra

    def _ensure_location_fields(self, record: logging.LogRecord, extra: MutableMapping[str, Any]) -> None:
        if record.pathname and "pathname" not in extra:
            extra["pathname"] = record.pathname
        if record.lineno and "lineno" not in extra:
            extra["lineno"] = record.lineno
        func_name = getattr(record, "funcName", None)
        if func_name and "funcName" not in extra:
            extra["funcName"] = func_name

    @staticmethod
    def _normalise_args(args: Any) -> MessageArgs:
        # Typed Any on purpose: typeshed declares LogRecord.args as tuple | Mapping,
        # but records built by third-party code or logging.makeLogRecord carry None
        # or a bare scalar, which stdlib's getMessage still renders ("x %s" % 5).
        if args is None:
            return ()
        if isinstance(args, tuple):
            return tuple(cast("tuple[object, ...]", args))
        if isinstance(args, Mapping):
            return cast("Mapping[str, object]", args)
        return (args,)


def _report_declined_record(record: logging.LogRecord) -> None:
    """Give a declined ``lib_log_rich`` record the output stdlib would have given it.

    The bridge declines the library's own records so they cannot loop back into
    the pipeline. But stdlib uses ``logging.lastResort`` only when NO handler
    sees a record, and the bridge counts as one, so a bridge on root silenced
    every adapter failure the library reports. When nothing but bridges would
    see the record, hand it to ``lastResort`` as stdlib would; any real handler
    on the chain prints it already, and printing here as well would duplicate it.
    """
    if getattr(record, _REPORTED_ATTR, False):
        return  # a second bridge further up the same chain
    last_resort = logging.lastResort
    if last_resort is None or record.levelno < last_resort.level:
        return
    if not _only_bridges_handle(record):
        return
    setattr(record, _REPORTED_ATTR, True)
    last_resort.handle(record)


def _only_bridges_handle(record: logging.LogRecord) -> bool:
    """Return True when every handler stdlib would call for ``record`` is a bridge."""
    logger: logging.Logger | None = logging.getLogger(record.name)
    while logger is not None:
        if any(not isinstance(handler, StdlibLoggingHandler) for handler in logger.handlers):
            return False
        logger = logger.parent if logger.propagate else None
    return True


_USE_MINIMUM_LEVEL = object()


def attach_std_logging(
    *,
    logger: logging.Logger | None = None,
    handler_level: int | str | LogLevel | None = None,
    logger_level: int | str | LogLevel | object = _USE_MINIMUM_LEVEL,
    propagate: bool = False,
) -> StdlibLoggingHandler:
    """Attach :class:`StdlibLoggingHandler` to ``logger`` and tweak stdlib toggles.

    Args:
        logger: Target logger to receive stdlib log events. Defaults to the root
            logger.
        handler_level: Optional level controlling when the bridge emits into the
            runtime.
        logger_level: Level applied to the target logger itself. Defaults to
            ``get_minimum_log_level()`` so the root logger captures all events
            that any backend might accept. Pass ``None`` to leave unchanged.
        propagate: Override the logger's ``propagate`` flag. Defaults to ``False``
            to prevent duplicate emission when other handlers exist in the
            hierarchy.

    Returns:
        The handler registered on the logger, useful for later removal.

    """
    target = logger or logging.getLogger()
    handler = _ensure_handler_attached(target)
    if handler_level is not None:
        handler.setLevel(_coerce_logging_level(handler_level))
    if logger_level is _USE_MINIMUM_LEVEL:
        target.setLevel(_coerce_logging_level(get_minimum_log_level()))
    elif logger_level is not None:
        target.setLevel(_coerce_logging_level(cast("int | str | LogLevel", logger_level)))
    target.propagate = propagate
    return handler


def _ensure_handler_attached(target: logging.Logger) -> StdlibLoggingHandler:
    for existing in target.handlers:
        if isinstance(existing, StdlibLoggingHandler):
            return existing
    handler = StdlibLoggingHandler()
    target.addHandler(handler)
    return handler


def _coerce_logging_level(level: int | str | LogLevel) -> int:
    if isinstance(level, LogLevel):
        return level.to_python_level()
    if isinstance(level, int):
        return level
    # getLevelNamesMapping() added in Python 3.11; fall back to _nameToLevel for 3.10
    mapping: dict[str, int] = getattr(logging, "getLevelNamesMapping", lambda: logging._nameToLevel)()  # pyright: ignore[reportPrivateUsage]
    candidate = mapping.get(level.strip().upper())
    if candidate is not None:
        return candidate
    raise ValueError(f"Unsupported logging level: {level!r}")


__all__ = ["StdlibLoggingHandler", "attach_std_logging"]
