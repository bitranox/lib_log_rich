# Install journal dependencies

## Quick fix by distro

install `python3-systemd` NOT `systemd` - in order to do that, You need to install probably `libsystemd-dev`(or similar) package in Linux. 


**Debian/Ubuntu (incl. WSL, *-slim Docker images):**

```bash
sudo apt-get update
sudo apt-get install -y libsystemd-dev pkg-config python3-dev build-essential
python3 -m pip install --no-cache-dir --force-reinstall systemd-python
# (Even easier: sudo apt-get install -y python3-systemd)  # uses distro wheel; no build step
```

**Fedora/RHEL/CentOS (with systemd):**

```bash
sudo dnf install -y systemd-devel pkgconf-pkg-config python3-devel gcc
python3 -m pip install --no-cache-dir --force-reinstall systemd-python
# Or: sudo dnf install -y python3-systemd
```

**Arch/Manjaro:**

```bash
sudo pacman -S --needed systemd pkgconf base-devel python
python3 -m pip install --no-cache-dir --force-reinstall systemd-python
# Or: sudo pacman -S python-systemd
```

**openSUSE:**

```bash
sudo zypper install -y systemd-devel pkg-config python3-devel gcc
python3 -m pip install --no-cache-dir --force-reinstall systemd-python
# Or: sudo zypper install python3-systemd
```

> If you're inside a **Debian/Ubuntu Docker** image, do the `apt-get install` steps **inside the container** before `pip install`.

---

## Important caveats

* **Alpine Linux (musl) or distros without systemd/journald:** this package won't build or won't be useful (no journald). Use a fallback handler (stdout/syslog) in dev/containers, or base your image on a systemd-enabled distro if you truly need journald.
* **Name shadowing:** ensure you don't have a local file/folder named `systemd` that hijacks the import.
* **Right interpreter:** run `python3 -m pip ...` with the same Python you use to run the app.

---

## Verify after install

```bash
python3 -c "import systemd, pkgutil; print(systemd.__file__); print([m.name for m in pkgutil.iter_modules(systemd.__path__)])"
# Expect to see .../site-packages/systemd/__init__.py and 'journal' in the list
```

Then a minimal test:

```python
from systemd.journal import JournalHandler
import logging

h = JournalHandler(SYSLOG_IDENTIFIER="myapp")
logging.getLogger().addHandler(h)
logging.warning("it works!")
```

And check:

```bash
journalctl -t myapp -o short-iso
```

That test drives the bindings' own `JournalHandler`, which sets `SYSLOG_IDENTIFIER`. lib_log_rich's
journald adapter does not set one, so `journalctl -t` finds nothing from it. Check lib_log_rich
itself by its structured fields instead:

```python
import lib_log_rich as log

log.init(log.RuntimeConfig(service="myapp", environment="prod", enable_journald=True))
log.getLogger("smoke").warning("journald smoke test")
log.shutdown()
```

```bash
journalctl SERVICE=myapp ENVIRONMENT=prod -n 5 -o verbose
```

The adapter does not strictly need the bindings: without them it writes the journal's native
datagram format to `/run/systemd/journal/socket` (then `/dev/log`) itself. The bindings remain the
supported path.
