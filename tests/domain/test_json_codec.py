"""The JSON codec uses orjson where it can, and stdlib json on a free-threaded build.

orjson ships no free-threaded build (its source build refuses one), so on such an
interpreter the library must still serialise events, checkpoints and dumps, with
the same bytes wherever the two encoders can agree. The stdlib backend follows
orjson's choices for the values that differ by default: ``null`` for NaN and
infinity, ISO text for datetimes, the string form of a UUID, an Enum's value.
"""

from __future__ import annotations

import datetime as dt
import enum
import math
import sys
import sysconfig
import uuid
from importlib import metadata
from typing import TYPE_CHECKING, Any

import pytest
from packaging.requirements import Requirement

from lib_log_rich.domain import json_codec
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from types import ModuleType

pytestmark = [OS_AGNOSTIC]

_FREE_THREADED = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))


class _Colour(enum.Enum):
    RED = "red"


# Values on which orjson and the stdlib backend must produce the same bytes.
_SHARED = {
    "text": "plain",
    "unicode": "grüße ☃",
    "control": "a\x00b\nc",
    "int": 42,
    "negative": -7,
    "float": 0.30000000000000004,
    "big_float": 1e20,
    "bool": True,
    "none": None,
    "list": [1, "two", 3.5, None],
    "nested": {"b": 2, "a": {"z": 1, "y": [{"k": "v"}]}},
    "nan": math.nan,
    "inf": math.inf,
    "neg_inf": -math.inf,
    "aware": dt.datetime(2025, 9, 30, 12, 0, 5, 6, tzinfo=dt.timezone.utc),
    "naive": dt.datetime(2025, 9, 30, 12, 0, 5),
    "date": dt.date(2025, 9, 30),
    "time": dt.time(12, 0, 5),
    "uuid": uuid.UUID(int=1),
    "enum": _Colour.RED,
    "empty_list": [],
    "empty_dict": {},
}


def _orjson() -> ModuleType:
    return pytest.importorskip("orjson")


def test_the_stdlib_backend_writes_null_for_non_finite_floats_at_any_depth() -> None:
    codec = json_codec.StdlibCodec()

    assert codec.dumps({"a": [math.nan, {"b": math.inf}], "c": -math.inf}) == '{"a":[null,{"b":null}],"c":null}'


@pytest.mark.parametrize(("sort_keys", "indent"), [(False, False), (True, False), (False, True)])
def test_both_backends_write_the_same_bytes(sort_keys: bool, indent: bool) -> None:
    orjson_codec = json_codec.OrjsonCodec(_orjson())
    stdlib_codec = json_codec.StdlibCodec()

    expected = orjson_codec.dumps(_SHARED, sort_keys=sort_keys, indent=indent)

    assert stdlib_codec.dumps(_SHARED, sort_keys=sort_keys, indent=indent) == expected


def test_both_backends_apply_a_caller_default_to_what_neither_encodes() -> None:
    orjson_codec = json_codec.OrjsonCodec(_orjson())
    stdlib_codec = json_codec.StdlibCodec()
    payload = {"raw": b"\x00", "obj": object.__new__(_Opaque)}

    assert stdlib_codec.dumps(payload, default=str) == orjson_codec.dumps(payload, default=str)


@pytest.mark.parametrize("backend", ["orjson", "stdlib"])
def test_a_value_neither_backend_encodes_raises_type_error(backend: str) -> None:
    codec = json_codec.OrjsonCodec(_orjson()) if backend == "orjson" else json_codec.StdlibCodec()

    with pytest.raises(TypeError):
        codec.dumps({"raw": b"\x00"})


class _Opaque:
    def __str__(self) -> str:
        return "opaque"


def test_a_free_threaded_build_never_imports_orjson() -> None:
    def must_not_import() -> ModuleType | None:
        raise AssertionError("orjson imported on a free-threaded build")

    codec = json_codec.select_codec(free_threaded=True, import_orjson=must_not_import)

    assert isinstance(codec, json_codec.StdlibCodec)


def test_without_orjson_the_stdlib_backend_is_chosen() -> None:
    codec = json_codec.select_codec(free_threaded=False, import_orjson=lambda: None)

    assert isinstance(codec, json_codec.StdlibCodec)


def test_with_orjson_on_a_gil_build_orjson_is_chosen() -> None:
    orjson = _orjson()

    codec = json_codec.select_codec(free_threaded=False, import_orjson=lambda: orjson)

    assert isinstance(codec, json_codec.OrjsonCodec)


def test_the_running_interpreter_gets_the_backend_its_build_allows() -> None:
    expected: type[Any] = json_codec.StdlibCodec if _FREE_THREADED or not _orjson_installed() else json_codec.OrjsonCodec

    assert isinstance(json_codec.CODEC, expected)


def test_importing_the_package_leaves_the_gil_off_on_a_free_threaded_build() -> None:
    if not _FREE_THREADED:
        pytest.skip("only a free-threaded build can turn the GIL back on")
    is_gil_enabled = getattr(sys, "_is_gil_enabled")  # noqa: B009 - absent before 3.13

    assert is_gil_enabled() is False


def _orjson_installed() -> bool:
    try:
        metadata.version("orjson")
    except metadata.PackageNotFoundError:
        return False
    return True


def _orjson_installed_by(python_version: str, extras: tuple[str, ...]) -> bool:
    """Whether the installed metadata pulls orjson in for that Python and those extras."""
    requirements = [Requirement(line) for line in metadata.requires("lib_log_rich") or []]
    orjson = [req for req in requirements if req.name == "orjson"]
    environments = [{"python_version": python_version, "extra": extra} for extra in ("", *extras)]
    return any(req.marker is None or req.marker.evaluate(env) for req in orjson for env in environments)


@pytest.mark.parametrize(
    ("python_version", "extras", "expected"),
    [
        pytest.param("3.10", (), True, id="3.10 plain"),
        pytest.param("3.13", (), True, id="3.13 plain"),
        pytest.param("3.14", (), False, id="3.14 plain"),
        pytest.param("3.14", ("orjson",), True, id="3.14 with the orjson extra"),
        pytest.param("3.15", (), False, id="3.15 plain"),
        pytest.param("3.15", ("orjson",), True, id="3.15 with the orjson extra"),
    ],
)
def test_orjson_is_required_below_3_14_and_an_extra_from_3_14(python_version: str, extras: tuple[str, ...], expected: bool) -> None:
    assert _orjson_installed_by(python_version, extras) is expected
