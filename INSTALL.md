# Installation Guide

`lib_log_rich` can be installed in a variety of environments. Choose the option that matches your workflow.

> **Python requirement:** Python 3.10 or newer is required for all installation methods.

## From PyPI

```bash
pip install lib_log_rich
# or
uv pip install lib_log_rich
```

Optional extras pull in the platform bindings and the faster JSON encoder:

| Extra      | Installs                      | Use it for                                                                                                                                                                                                                                     |
|------------|-------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `journald` | `systemd-python` (Linux only) | The journald adapter's native bindings; building it needs the system packages in [INSTALL_JOURNAL.md](INSTALL_JOURNAL.md).                                                                                                                     |
| `eventlog` | `pywin32` (Windows only)      | The Windows Event Log adapter.                                                                                                                                                                                                                 |
| `orjson`   | `orjson`                      | The faster JSON encoder on a normal (GIL) Python 3.14 or later; below 3.14 orjson is installed with the package. Never on a free-threaded build, see [JSON encoder and free-threaded Python](README.md#json-encoder-and-free-threaded-python). |

```bash
pip install "lib_log_rich[journald]"
pip install "lib_log_rich[eventlog]"
pip install "lib_log_rich[orjson]"   # normal Python 3.14+ only
```

### Journald adapter prerequisites

If you plan to emit logs to systemd journald, follow the platform checklist in [INSTALL_JOURNAL.md](INSTALL_JOURNAL.md). It covers package installation, socket permissions, and smoke tests to confirm the adapter can write entries once `lib_log_rich` is configured with `enable_journald=True`.

## 1. Standard Virtual Environment (pip, from a checkout)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .[dev]       # development install
# or, for runtime only:
pip install .
```

## 2. Per-User Install (no virtualenv)

```bash
pip install --user .
```

> **Note:** Respects PEP 668. Avoid this on a system Python marked as "externally managed". Ensure `~/.local/bin` (POSIX) or `%APPDATA%\Python\Scripts` (Windows) is on your `PATH`.

## 3. pipx (isolated, recommended for CLI use)

```bash
pipx install .
pipx upgrade lib_log_rich
# install directly from a Git ref
pipx install "git+https://github.com/bitranox/lib_log_rich"
```

## 4. uv (fast installer/runner)

```bash
uv pip install -e .[dev]
uv tool install .
```

## 5. From Built Artifacts

```bash
python -m build
pip install dist/lib_log_rich-*.whl
pip install dist/lib_log_rich-*.tar.gz   # sdist
```

## 6. Poetry / PDM Managed Environments

```bash
# Poetry
poetry add lib_log_rich
poetry install

# PDM
pdm add lib_log_rich
pdm install
```

## 7. Install Directly from Git (CI-friendly)

```bash
pip install "git+https://github.com/bitranox/lib_log_rich#egg=lib_log_rich"
```

## 8. System Package Managers (optional)

- **Deb/RPM:** generate packages with `fpm` as needed

## Development Environments

For contributor workflows, see [DEVELOPMENT.md](DEVELOPMENT.md) for make targets, CI automation, and release notes.
