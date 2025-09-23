
---

# IDEE

ich möchte eine Logging Biliothek erzeugen

* diese soll farbige Logs ausgeben
* das format der Logzeile soll einstellbar sein
* das Logging soll auf Linux und Windows funktionieren

  * Windows : **Windows Event Log**
  * Linux : journal
  * **optional/alternativ: Graylog (GELF, bevorzugt TCP+TLS)**
  * kein Logging in Textfiles, es soll thread-safe sein und Subprozesse unterstützen
* es soll einen Loglevel unterstützen
* bei Fehlern soll es möglich sein das gesamte Log als Textfile zu erhalten
* um die Logs auszuweten sollen im syslog/journal entsprechenden Felder vorhanden sein (Vorschläge ?)
* der Loglevel für die Konsole und für syslog/journal kann unterschiedlich sein
* im syslog/journal/GELF werden **keine** Farben/Unicode-Icons verwendet (nur Konsole & Ringpuffer/HTML)
* die Binliothek soll in Python leicht importierbar sein, und ein minimales Interface und methoden haben
* wie handelt man das in Subprozessen ?
* **NEU:** Eine **JOB-ID** soll mitgeloggt werden (z. B. Batch-/Worker-Job, Cron-Run, Pipeline-Step)

---

## A) Ziele & Scope

1. **Primärziele**

* Farbiges, gut lesbares Console-Logging
* Konfigurierbares Zeilenformat (Konsole, HTML-Dump)
* Backend-Logging: **Linux → journald**, **Windows → Windows Event Log**
* **Optional/alternativ:** Versand an **Graylog (GELF, TCP+TLS)**
* **Kein** File-Logging im Dauerbetrieb
* Thread-safe und **multiprozessfähig**
* Loglevel steuerbar (Konsole ≠ Backend möglich)
* Bei Fehlern: **Export des gesamten Logs** als Text/JSON/**HTML** (on-demand)
* Strukturierte Felder in journald/Windows Event Log/**GELF** für Auswertung
* **JOB-ID** als konsistentes Kontextfeld

2. **Nichtziele / Klarstellungen**

* Keine dauerhaften Rotations-Files
* Keine Farben/Icons im Backend (nur Konsole & HTML-Dump)
* Kein vendor-lock-in; minimalistische Abhängigkeiten

---

## B) Ausgabekanäle & Plattformdetails

1. **Konsole**

   * Farben ja/nein pro Umgebung (TTY-Erkennung, `force_color`, `no_color`)
   * Unicode-Icons nur in der Konsole (optional)
   * Bei `isatty == False`: keine Farben/Icons, optional JSON
   * **Bibliothek:** **Rich** als optionales Extra für Konsole & HTML-Export

2. **Linux Backend (journald)**

   * `systemd.journal.JournalHandler`
   * Strukturierte Felder (nur **UPPERCASE** ASCII Keys)

3. **Windows Backend (Windows Event Log)**

   * `pywin32` / `win32evtlogutil`-basierter Handler
   * **Log:** `Application` (Default) / Custom möglich
   * **Provider:** Default = `service`-Name
   * **EventID-Map:** INFO=1000, WARNING=2000, ERROR=3000, CRITICAL=4000 (overridebar)
   * Fallback: bei Fehlern nur Konsole + lokales Gegenstück weiter

4. **Zentrales Backend (Graylog via GELF) — optional**

   * **Transport:** Default **TCP + TLS**, alternativ UDP/HTTP
   * Zusatzfelder mit `_`-Prefix, Stacktraces in `full_message`
   * Fallback: Backoff & Retry; bei dauerhaften Fehlern droppen (Konsole+lokal laufen weiter)

**Adapter-Deaktivierung:** Für reine Konsole-Szenarien kann die Composition Root den Ringpuffer sowie journald- und Event-Log-Adapter deaktivieren (`enable_ring_buffer=False`, `enable_journald=False`, `enable_eventlog=False`).

---

## C) Formatierung (Konsole & HTML) & Farben

1. **Format-Konfiguration**

   * **Console-Format:** z. B.
     `{ts} {level:>5} {name} {pid}:{tid} — {message} {context}`
   * **HTML-Dump-Format:** separat konfigurierbar, z. B. mit zusätzlichen Icons, Farben, Badges
   * Timestamp: ISO-8601 `%Y-%m-%dT%H:%M:%S.%f%z`
   * Exceptions: mehrzeilig (Trace separat angehängt)

2. **Farben / Rich-Integration**

   * **Konsole:** `RichHandler` (optional), Theme konfigurierbar; TTY-Auto-Erkennung
   * **HTML-Dump:** Rich-basiertes Rendern mit separatem Format; unterstützt Farben, Symbole, Styles
   * **Backends:** Plain-Formatter (keine ANSI/Icons)
   * Optionen: `colors=True|False|auto`, `markup_console=True|False`, `force_color=None|True|False`

---

## D) Loglevel-Strategie & Filter

* Unterschiedliche Loglevel pro Handler
* Mapping Python → journald/Windows/GELF:

  * CRITICAL→2, ERROR→3, WARNING→4, INFO→6, DEBUG→7
* Defaults:

  * Konsole: `INFO`
  * journald/Event Log: `INFO`
  * GELF: `INFO`
* Ratenbegrenzung: `RateLimitingFilter` aktiv

---

## E) Strukturierte Felder für Auswertung

1. **journald**
   `...`, **`JOB_ID`**

2. **Windows Event Log**
   `...`, **`job_id`**

3. **GELF**
   `_...`, **`_job_id`**

---

## F) Multiprocessing & Thread-Safety

* `logging` ist thread-safe
* Architektur: `QueueHandler` in allen Prozessen → zentraler `QueueListener`
* Kontext inkl. JOB-ID via `contextvars`; in Subprozessen per `bind()`

---

## G) Fehlerstrategie & Dumps

* **Ringpuffer:** speichert **alle Felder roh** (ts, level, logger, msg, context, job\_id, extras …)
* **Dump-API:** `lib_log_rich.dump(format="text"|"json"|"html", path=None)`

  * `text`: plain ohne ANSI
  * `json`: Liste der Roh-Events
  * `html`: Rich gerendert, **eigener Format-String/Theme**, farbig, Symbole möglich
* Im Falle des HTML-Dumps wird **das HTML-spezifische Logformat** angewendet (nicht das Console-Format)

---

## H) API-Design

```python
import lib_log_rich as log

log.init(
    service="meinservice",
    env="prod",
    backend="auto",
    console_format="{ts} {level:>5} {name} — {message} {context}",
    html_format="{ts} {level_icon} {message} {context}",  # separat konfigurierbar
    html_theme="dark",
    # Für reine Konsole können Backends deaktiviert werden:
    # enable_ring_buffer=False,
    # enable_journald=False,
    # enable_eventlog=False,
    # ...
)

logger = log.get("app.http")

with log.bind(request_id="abc123", user_id="42", job_id="job-2025-09-21-001"):
    logger.info("Login ok")
    logger.error("Fehlgeschlagen", extra={"error_code": "AUTH_401"})

log.dump(format="html")
```

Konsolen-Stubs verbleiben verfügbar:
```
python -m lib_log_rich
lib_log_rich --hello
lib_log_rich --version
```
Beide Befehle geben den gleichen Metadatenbanner aus wie `summary_info()`.


Methoden: `init`, `get`, `bind` / `unbind`, `set_levels`, `dump`, `shutdown`

---

## I) Konfiguration

* API + ENV
* Beispiel: `LOG_CONSOLE_FORMAT`, `LOG_HTML_FORMAT`, `LOG_JOB_ID`
* Flags für Backends: `enable_ring_buffer`, `enable_journald`, `enable_eventlog` (Standard = aktiv; Konsole-only ⇒ `False`)
* ENV-Entsprechungen: `LOG_ENABLE_RING_BUFFER`, `LOG_ENABLE_JOURNALD`, `LOG_ENABLE_EVENTLOG`

---

## J) Performance

* Async via `QueueHandler`
* Lazy-Formatting
* GELF mit Backoff/Retry

---

## K) Sicherheit

* Scrubber aktiv (JWT, Passwörter, Tokens etc.)
* `job_id` gilt als nicht-sensitiv, wird aber ebenfalls geprüft

---

## L) Tests & DX

* pytest + caplog
* Tests zur Propagation von `job_id` in Threads/Subprozessen
* Tests zu getrennten Formaten (Konsole vs. HTML-Dump)
* Contract-Tests für GELF, journald, EventLog

---

## M) Abhängigkeiten

* Basis: Stdlib
* journald: `systemd-python`
* Event Log: `pywin32`
* GELF: `graypy`
* Rich: `rich`, `colorama`

---

## N) Farben & Unicode im Backend

* Nur Konsole & HTML
* Backends rein strukturiert

---

## O) Offene Entscheidungen

* Ringpuffer: 25k
* Windows EventID Mapping fixiert
* journald Feld-Set mit `JOB_ID`
* GELF: TCP+TLS Default
* **Console-Format ≠ HTML-Dump-Format** konfigurierbar

---

## P) Baseline-Design (kompakt)

* `QueueHandler` → `QueueListener` → Console + journald/Event Log + GELF
* Kontext: `contextvars` inkl. JOB-ID
* Export: Ringpuffer + Dumps mit getrennten Formaten

---
