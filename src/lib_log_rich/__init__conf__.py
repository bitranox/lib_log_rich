"""Runtime metadata facade kept in sync with the installed distribution.

Purpose
-------
Expose key package metadata (name, version, homepage, author) as simple module
attributes so that documentation and tooling can present authoritative
information without parsing project files at runtime.

Contents
--------
* Private helpers (``_meta``, ``_version``, etc.) encapsulate the
  :mod:`importlib.metadata` lookups and their fallbacks.
* Module-level constants mirror the fields enumerated in
  ``docs/systemdesign/module_reference.md`` and stay aligned with
  ``pyproject.toml``.
* :func:`print_info` provides a single place to render the metadata in a human
  readable form for tooling and documentation examples.

System Role
-----------
Lives in the adapters/platform layer: domain code does not depend on these
details, but tooling references them to keep messages and release automation
consistent with the published package.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import metadata as _im
from typing import Any, Callable, Iterable, cast

# ``pyproject.toml`` defines the package name; we mirror it here so the metadata
# lookups and fallbacks stay in lockstep with the published distribution.
#: Distribution slug that must stay aligned with ``[project].name`` in ``pyproject.toml``.
#: Keeping this constant authoritative ensures documentation and metadata queries
#: reference the same package identifier described in ``docs/systemdesign/concept_architecture.md``.
_DIST_NAME = "lib_log_rich"

#: Version string returned when no installed distribution metadata is present.
#: The value is referenced throughout the documentation so tooling can rely on a stable dev marker.
_FALLBACK_VERSION = "0.0.0.dev0"

#: Default homepage used when packaging metadata omits a URL.
#: Mirrors the canonical repository referenced in the system design documentation.
_DEFAULT_HOMEPAGE = "https://github.com/bitranox/lib_log_rich"

#: Default author attribution exported for docs and metadata fallbacks.
_DEFAULT_AUTHOR: tuple[str, str] = ("bitranox", "bitranox@gmail.com")

#: Default summary used by documentation before packaging metadata is available.
_DEFAULT_SUMMARY = "Rich-powered logging runtime with contextual metadata and multi-sink fan-out"


def _get_str(m: Mapping[str, object] | None, key: str, default: str = "") -> str:
    """Return a string metadata value or fall back when the key is absent.

    Why
        Metadata objects behave like mappings but may return non-string values;
        this helper enforces the string contract expected by downstream
        consumers.

    Parameters
    ----------
    m:
        Metadata mapping implementing ``.get``.
    key:
        Field name to fetch (e.g., ``"Author"``).
    default:
        Fallback returned when the key is missing or not a ``str``.

    Returns
    -------
    str
        The resolved string or ``default`` when the value is missing/invalid.

    Examples
    --------
    >>> sample = {"Author": "bitranox", "Author-email": 42}
    >>> _get_str(sample, "Author")
    'bitranox'
    >>> _get_str(sample, "Author-email", "fallback@example.com")
    'fallback@example.com'
    """

    if m is None:
        return default
    v = m.get(key, default)
    return v if isinstance(v, str) else default


def _meta(dist_name: str = _DIST_NAME) -> Any | None:
    """Load distribution metadata if the package is installed.

    Why
        Running from a working tree (without an editable install) should not
        raise; returning ``None`` lets callers pick sensible fallbacks.

    Parameters
    ----------
    dist_name:
        Distribution name to query; defaults to the project distribution.

    Returns
    -------
    Any | None
        Metadata object when available, otherwise ``None``.

    Examples
    --------
    >>> import importlib.metadata as _md
    >>> original = _md.metadata
    >>> try:
    ...     _md.metadata = lambda _: {"Summary": "Demo"}
    ...     _meta("demo-package")
    ... finally:
    ...     _md.metadata = original
    {'Summary': 'Demo'}
    """

    try:
        return _im.metadata(dist_name)
    except _im.PackageNotFoundError:
        return None


def _version(dist_name: str = _DIST_NAME) -> str:
    """Fetch the installed version or return the development fallback.

    Why
        Version numbers must remain a single source of truth; when the package
        is not installed yet, returning a predictable dev version keeps tooling
        deterministic.

    Parameters
    ----------
    dist_name:
        Distribution name to query.

    Returns
    -------
    str
        The installed version or ``"0.0.0.dev0"`` when missing.

    Examples
    --------
    >>> import importlib.metadata as _md
    >>> original = _md.version
    >>> try:
    ...     _md.version = lambda _: "1.2.3"
    ...     _version("demo-package")
    ... finally:
    ...     _md.version = original
    '1.2.3'
    >>> _version("non-existent-demo") == "0.0.0.dev0"
    True
    """

    try:
        return _im.version(dist_name)
    except _im.PackageNotFoundError:
        return _FALLBACK_VERSION


def _home_page(m: Any | None) -> str:
    """Resolve the project homepage URL with sensible fallbacks.

    Why
        Packaging metadata may omit the homepage. Providing a default ensures
        developer tooling and docs always have a stable link.

    Parameters
    ----------
    m:
        Metadata mapping or ``None`` when the package is not installed.

    Returns
    -------
    str
        Homepage URL, defaulting to the GitHub repository when missing.

    Examples
    --------
    >>> _home_page(None)
    'https://github.com/bitranox/lib_log_rich'
    >>> _home_page({"Homepage": "https://example.test"})
    'https://example.test'
    """

    if not isinstance(m, Mapping):
        return _DEFAULT_HOMEPAGE
    mm = cast(Mapping[str, object], m)
    hp = _get_str(mm, "Home-page") or _get_str(mm, "Homepage")
    return hp or _DEFAULT_HOMEPAGE


def _author(m: Any | None) -> tuple[str, str]:
    """Return author metadata as a ``(name, email)`` tuple.

    Why
        Several commands print attribution; falling back to project defaults
        keeps the message friendly even before packaging metadata exists.

    Parameters
    ----------
    m:
        Metadata mapping or ``None`` when the package is absent.

    Returns
    -------
    tuple[str, str]
        Author name and email, empty strings when not provided.

    Examples
    --------
    >>> _author(None)
    ('bitranox', 'bitranox@gmail.com')
    >>> _author({"Author": "Alice", "Author-email": "alice@example"})
    ('Alice', 'alice@example')
    """

    if not isinstance(m, Mapping):
        return _DEFAULT_AUTHOR
    mm = cast(Mapping[str, object], m)
    return (_get_str(mm, "Author", ""), _get_str(mm, "Author-email", ""))


def _summary(m: Any | None) -> str:
    """Return the short project description used for titles.

    Why
        The metadata banner and documentation headings pull from this value;
        providing a default keeps the scaffold informative before packaging
        metadata is present.

    Parameters
    ----------
    m:
        Metadata mapping or ``None`` when metadata is unavailable.

    Returns
    -------
    str
        Summary string describing the project.

    Examples
    --------
    >>> _summary(None)
    'Rich-powered logging runtime with contextual metadata and multi-sink fan-out'
    >>> _summary({"Summary": "Demo"})
    'Demo'
    """

    if not isinstance(m, Mapping):
        return _DEFAULT_SUMMARY
    mm = cast(Mapping[str, object], m)
    return _get_str(mm, "Summary", _DEFAULT_SUMMARY)


def _shell_command(entry_points: Iterable[Any] | None = None) -> str:
    """Derive the console-script name registered for the CLI entry point.

    Why
        Documentation should reflect the executable name actually published by
        the distribution—if any entry points are registered. Even without a CLI
        today we keep the lookup for compatibility with historical tooling.

    Parameters
    ----------
    entry_points:
        Iterable of entry point objects with ``.value`` and ``.name`` attributes.
        Defaults to querying :mod:`importlib.metadata`.

    Returns
    -------
    str
        Console script name or the distribution name when not registered.

    Examples
    --------
    >>> class Ep:
    ...     def __init__(self, name, value):
    ...         self.name = name
    ...         self.value = value
    >>> fake_eps = [Ep("bt-cli", "lib_log_rich.tools:main")]
    >>> _shell_command(fake_eps)
    'bt-cli'
    >>> _shell_command([])
    'lib_log_rich'
    """

    eps = entry_points if entry_points is not None else _im.entry_points(group="console_scripts")
    for ep in list(eps):
        value = getattr(ep, "value", "") or ""
        if isinstance(value, str):
            module = value.split(":", 1)[0]
            root = module.split(".", 1)[0]
            if root == _DIST_NAME:
                return getattr(ep, "name")
    return _DIST_NAME


# Public values (resolve metadata once)
#: Cached metadata instance so repeated attribute resolves stay consistent with the installed distribution.
_m = _meta()
# Exported metadata mirrors the distribution so documentation stays authoritative.
#: Public distribution name referenced throughout ``docs/systemdesign/module_reference.md``.
name = _DIST_NAME
#: Human-readable summary aligning with the system design glossary.
title = _summary(_m)
#: Installed version number or the documented development fallback.
version = _version()
#: Homepage URL shared with external documentation and support tooling.
homepage = _home_page(_m)
#: Author attribution tuple used by documentation examples and support material.
author, author_email = _author(_m)
#: Console entry point name retained for backwards compatibility with the CLI documented in earlier revisions.
shell_command = _shell_command()


def print_info(*, writer: Callable[[str], None] | None = None) -> None:
    """Render the summarised metadata block for the package.

    Why
        Provides a single, auditable rendering function so documentation and
        library output always match the system design reference.

    What
        Formats the key metadata fields with aligned labels. When no ``writer``
        is provided the text is emitted to ``stdout``. Callers may pass a
        writer to capture the rendered text programmatically.

    Parameters
    ----------
    writer:
        Optional callback that receives the formatted metadata string. When
        ``None`` (default), the text is written to ``stdout``.

    Returns
    -------
    None
        The helper is evaluated for its output side effect only.

    Side Effects
        Writes to ``stdout`` when no writer is supplied.

    Examples
    --------
    >>> print_info()  # doctest: +ELLIPSIS
    Info for lib_log_rich:
    ...
    >>> bucket: list[str] = []
    >>> print_info(writer=bucket.append)
    >>> bucket[-1].startswith('Info for lib_log_rich')
    True
    """

    fields = [
        ("name", name),
        ("title", title),
        ("version", version),
        ("homepage", homepage),
        ("author", author),
        ("author_email", author_email),
        ("shell_command", shell_command),
    ]
    pad = max(len(k) for k, _ in fields)
    lines = [f"Info for {name}:", ""]
    lines += [f"    {k.ljust(pad)} = {v}" for k, v in fields]
    text = "\n".join(lines)
    if writer is None:
        print(text)
    else:
        writer(f"{text}\n")
