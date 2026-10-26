"""JSON encoding with orjson where it can run, and stdlib json where it cannot.

orjson ships no free-threaded build: its source build refuses a free-threaded
interpreter, and importing a build without free-threading support would turn
the GIL back on. On such an interpreter, or wherever orjson is not installed
(it is an optional extra from Python 3.14), events, checkpoints and dumps are
encoded with the standard library instead.

The stdlib backend follows orjson where the two differ by default, so a file
written by one reads the same under the other: NaN and infinity become
``null``, datetimes / dates / times their ISO text, a UUID its string form and
an Enum its value. Two differences remain and are valid JSON either way: a
float exponent is spelled ``1e-07`` instead of ``1e-7``, and an int beyond 64
bits is written out where orjson refuses it.
"""

from __future__ import annotations

import datetime as dt
import enum
import importlib
import json
import math
import sysconfig
import uuid
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

__all__ = ["CODEC", "JsonCodec", "OrjsonCodec", "StdlibCodec", "dumps", "select_codec"]


class JsonCodec(Protocol):
    """Encode a value as JSON text."""

    def dumps(
        self,
        value: object,
        *,
        sort_keys: bool = False,
        indent: bool = False,
        default: Callable[[Any], object] | None = None,
    ) -> str:
        """Return ``value`` as JSON: compact, or indented by two spaces.

        Raises:
            TypeError: For a value the encoder cannot represent and ``default``
                does not convert.
        """
        ...


class OrjsonCodec:
    """Encode with orjson, the fast path on every build that can run it."""

    def __init__(self, orjson: ModuleType) -> None:
        """Bind the codec to the imported ``orjson`` module."""
        self._orjson = orjson

    def dumps(
        self,
        value: object,
        *,
        sort_keys: bool = False,
        indent: bool = False,
        default: Callable[[Any], object] | None = None,
    ) -> str:
        """Return ``value`` as JSON text (see :class:`JsonCodec`)."""
        option = (self._orjson.OPT_SORT_KEYS if sort_keys else 0) | (self._orjson.OPT_INDENT_2 if indent else 0)
        encoded: bytes = self._orjson.dumps(value, default=default, option=option)
        return encoded.decode()


class StdlibCodec:
    """Encode with stdlib json, matching orjson's output where the two can agree.

    Example:
        >>> StdlibCodec().dumps({"b": float("nan"), "a": 1}, sort_keys=True)
        '{"a":1,"b":null}'
    """

    def dumps(
        self,
        value: object,
        *,
        sort_keys: bool = False,
        indent: bool = False,
        default: Callable[[Any], object] | None = None,
    ) -> str:
        """Return ``value`` as JSON text (see :class:`JsonCodec`)."""
        return json.dumps(
            _null_non_finite(value),
            ensure_ascii=False,
            sort_keys=sort_keys,
            indent=2 if indent else None,
            separators=None if indent else (",", ":"),
            default=_orjson_compatible_default(default),
        )


def _null_non_finite(value: object) -> object:
    # stdlib json writes NaN/Infinity (not JSON); orjson writes null. No encoder
    # hook sees a float, so the containers are rebuilt before encoding.
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, dict):
        mapping = cast("dict[object, object]", value)
        return {key: _null_non_finite(item) for key, item in mapping.items()}
    if isinstance(value, (list, tuple)):
        items = cast("list[object] | tuple[object, ...]", value)
        return [_null_non_finite(item) for item in items]
    return value


def _orjson_compatible_default(caller_default: Callable[[Any], object] | None) -> Callable[[Any], object]:
    """Encode the types orjson encodes natively, then defer to the caller's default."""

    def default(value: object) -> object:
        if isinstance(value, (dt.datetime, dt.date, dt.time)):
            return value.isoformat()
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, enum.Enum):
            return _null_non_finite(value.value)
        if caller_default is not None:
            return caller_default(value)
        raise TypeError(f"Type is not JSON serializable: {type(value).__name__}")

    return default


def _import_orjson() -> ModuleType | None:
    # By name, not an import statement: orjson is absent from every 3.14+ environment
    # of the dev install (no marker tells 3.14t from 3.14), and a static import would
    # leave the type checker unable to resolve the module there.
    try:
        return importlib.import_module("orjson")
    except ImportError:
        return None


def select_codec(*, free_threaded: bool, import_orjson: Callable[[], ModuleType | None]) -> JsonCodec:
    """Return the orjson codec when it can run here, else the stdlib codec.

    Args:
        free_threaded: Whether the interpreter is a free-threaded build. orjson is
            never imported there: it has no free-threaded build, and importing one
            without free-threading support would re-enable the GIL.
        import_orjson: Returns the ``orjson`` module, or ``None`` when it is not installed.

    Example:
        >>> isinstance(select_codec(free_threaded=True, import_orjson=lambda: None), StdlibCodec)
        True
    """
    if free_threaded:
        return StdlibCodec()
    orjson = import_orjson()
    return StdlibCodec() if orjson is None else OrjsonCodec(orjson)


#: The codec this interpreter uses, chosen once at import.
CODEC: JsonCodec = select_codec(
    free_threaded=bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
    import_orjson=_import_orjson,
)


def dumps(
    value: object,
    *,
    sort_keys: bool = False,
    indent: bool = False,
    default: Callable[[Any], object] | None = None,
) -> str:
    """Encode ``value`` with this interpreter's codec (see :class:`JsonCodec`).

    Example:
        >>> dumps({"b": 1, "a": [1.5, None]}, sort_keys=True)
        '{"a":[1.5,null],"b":1}'
    """
    return CODEC.dumps(value, sort_keys=sort_keys, indent=indent, default=default)
