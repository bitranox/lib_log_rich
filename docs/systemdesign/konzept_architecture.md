# Architekturleitfaden: lib_log_rich Logging-Backbone

## 1. Zweck & Kontext
lib_log_rich stellt eine High-Level-Logging-Bibliothek bereit, die Konsolen-Ausgabe, Plattform-Backends (journald, Windows Event Log) und optionale Zentralisierung über Graylog harmonisiert. Dieses Dokument destilliert die Architekturabschnitte aus `konzept.md` und beschreibt, wie das System entlang der Clean-Architecture-Grenzen aufgebaut wird. Ziel ist ein erweiterbares Fundament, das farbiges, strukturiertes Logging liefert, JOB-IDs propagiert und Mehrprozess-Szenarien zuverlässig unterstützt.

## 2. Zielarchitektur & Leitprinzipien
- **Schichtenmodell:** Domain (Log-Events & Kontextinvarianten) → Application (Use-Cases, Filter, Fan-out) → Adapter (Console, journald, Windows Event Log, GELF, HTML-Dump).
- **Doppelte Loglevel:** Ein Handler darf andere Loglevel als der Rest verwenden, um Konsole und Backend separat zu steuern.
- **Keine dauerhaften Logfiles:** Dauerbetrieb nutzt ausschließlich Streaming- oder Backend-Handler; Dumps entstehen on-demand über den Ringpuffer.
- **Multiprozessfähigkeit:** Queue-basierter Kern, der Worker, Cronjobs und Subprozesse über denselben Listener bedient.
- **Beobachtbarkeit:** Strukturierte Felder inklusive `JOB_ID` / `_job_id` stehen in allen Backends zur Verfügung und bleiben ASCII-konform.

## 3. High-Level-Datenfluss
1. **Producer (Anwendungscode):** Ruft `lib_log_rich.get()` auf, erhält Logger-Proxy.
2. **Kontextbindung:** `bind()` setzt `request_id`, `user_id`, `job_id` auf einem `contextvars`-Scope.
3. **QueueHandler (alle Prozesse):** Serialisiert LogRecord + Kontext in eine multiprocessing-fähige Queue.
4. **QueueListener (Hauptprozess):** Konsumiert Events, reichert sie um Standardfelder (ts, service, env) an.
5. **Fan-out:** Listener schickt Event an Console-Formatter, Backend-Formatter und Ringpuffer.
6. **Backends:**
   - **Console:** Rich-Renderer mit Theme/Markup (TTY-aware, Farben optional).
   - **journald:** `JournalHandler` mit UPPERCASE-Feldern.
   - **Windows Event Log:** `win32evtlogutil.ReportEvent` mit Event-ID-Map (1000/2000/3000/4000 standardmäßig).
   - **Graylog (GELF):** TCP+TLS-Kanal, `_job_id`, `_trace` etc. in Zusatzfeldern.
7. **Dump-Pfad (on-demand):** Ringpuffer speist `dump(format=...)` und generiert Text, JSON oder HTML.

## 4. Adapter & Ports
| Port | Verantwortung | Primäre Adapter | Besondere Anforderungen |
| --- | --- | --- | --- |
| `ConsolePort` | Menschlich lesbare Ausgabe | RichHandler | Auto-Detection TTY, `force_color`/`no_color`, Unicode-Icons nur hier |
| `StructuredBackendPort` | Plattform-Log | JournaldAdapter, WindowsEventAdapter | Keine Farben/Icons, Mapping Python-Level → Zielpriorität |
| `GraylogPort` (optional) | Zentrale Aggregation | GELFAdapter | TCP+TLS Standard, Backoff+Retry, Dropping bei Dauerfehler |
| `DumpPort` | Export kompletter Historie | HTMLDumpAdapter, JsonDumpAdapter, TextDumpAdapter | Eigenes Format pro Medium, nutzt Ringpuffer |

**Hinweis:** In Umgebungen mit bestehender Log-Aggregation (z.B. Winlogbeat/Sysmon → Graylog) bleibt `GraylogPort` deaktiviert. Die portbasierte Anbindung dient als Option für direkte TCP/TLS-Feeds, wenn kein externer Collector vorhanden ist oder zusätzliche strukturierte Felder benötigt werden.

Alle Adapter implementieren die Ports per Dependency Injection über die Composition Root (`lib_log_rich.init`).

Konfiguration kann einzelne Adapter deaktivieren. Für rein konsolenbasierte Szenarien setzt die Composition Root 
`enable_ring_buffer=False`, `enable_journald=False` und `enable_eventlog=False`, sodass nur der Rich-Console-Handler aktiv bleibt.

## 5. Formatierung & Layout
- **Console-Format:** Standard-Template `{ts} {level:>5} {name} {pid}:{tid} — {message} {context}` mit Rich-Markup-Unterstützung.
- **HTML-Format:** Separates Template, das Badges/Icons nutzt und im Dump gerendert wird.
- **Backend-Format:** Plain-Formatter ohne ANSI Codes; Exceptions werden mehrzeilig angehängt, Stacktrace separat in strukturierte Felder gelegt (`STACKTRACE`, `_stacktrace`).
- **Konfigurierbarkeit:** `init` akzeptiert `console_format`, `html_format`, `colors=(auto|true|false)`, `html_theme`, `force_color`.

## 6. Kontext- und Feldmanagement
- **JOB-ID:** Pflichtfeld im Kontext, Name pro Zielsystem (`JOB_ID`, `job_id`, `_job_id`).
- **Weitere Pflichtfelder:** `service`, `env`, `request_id`, `trace_id` (wenn vorhanden), `pid`, `tid`.
- **Feld-Naming-Regeln:** Backendkeys uppercase ASCII (journald), CamelCase für Windows Event Data, Unterstrich-Prefix für GELF.
- **Scrubber:** Sensible Inhalte (JWT, Passwörter) werden vor Ausgabe maskiert.

## 7. Multiprocessing & Thread-Safety
- **Thread Safety:** Python-`logging` Basisschicht bleibt erhalten; Formatierung findet ausschließlich im Listener statt.
- **Subprozess-Kopplung:** `init` spawnt Queue und Listener; Child-Prozesse initialisieren nur `QueueHandler`.
- **Contextvars-Propagation:** `bind()` repliziert Kontext in Subprozesse mithilfe einer Serialisierung, die beim Start injiziert wird.
- **Ratelimiting:** Optional aktivierter `RateLimitingFilter` verhindert Log-Fluten bei Fehler-Loops.

## 8. Fehlerbehandlung & Resilienz
- **Backoff Strategien:** GELFAdapter nutzt exponentielles Backoff + Jitter; bei 5 Fehlversuchen wird gedropped.
- **Fallback:** Schlägt ein Backend fehl, bleibt die Konsole aktiv; Fehler werden als Warnung ausgegeben.
- **Dump-API:** `dump(format="text|json|html", path=None)` holt Daten aus dem Ringpuffer (Standard 25k Events, konfigurierbar).
- **Shutdown:** `lib_log_rich.shutdown()` entleert Queue, wartet Listener-Lauf und schließt Adapter sauber.

## 9. Konfiguration & Deployment
- **API-Parameter:** `service`, `env`, `backend`, `console_level`, `backend_level`, `gelf_endpoint`, `force_color`,
  `enable_ring_buffer`, `enable_journald`, `enable_eventlog`.
- **Konsole-only Modus:** Zusammensetzung kann Ringpuffer und Plattform-Backends gezielt deaktivieren (`enable_ring_buffer=False`, `enable_journald=False`, `enable_eventlog=False`).
- **ENV-Variablen:** Spiegeln Kernoptionen (`LOG_CONSOLE_FORMAT`, `LOG_HTML_FORMAT`, `LOG_JOB_ID`, `LOG_BACKEND_LEVEL`).
- **Plattform-Spezifika:**
  - Linux benötigt `systemd-python`.
  - Windows benötigt `pywin32` (Event Log Rechte prüfen).
  - GELF-Feature als optionales Extra `[gelf]`.
- **No File Logging:** Persistente Dateien entstehen nur durch explizite Dumps.

## 10. Teststrategie & Qualitätsnachweise
- **Unit Tests:** Formatierung, Kontextbindung, Job-ID Pflicht, Ringpuffer.
- **Integration:** journald/Windows/GELF Adapter via Contract Tests (Fake Server / Event Log Mock).
- **Concurrency Tests:** Sicherstellen, dass QueueHandler in Prozessen/Threads keine Events verliert.
- **CLI/HTML Snapshot Tests:** Prüfen, dass HTML-Dump Themes konsistent bleiben.

## 11. Offene Entscheidungen & Risiken
- **Ringpuffer-Größe:** Zielwert 25k Events, Feintuning für Speicherverbrauch TBD.
- **Windows Event IDs:** Default Mapping (INFO=1000, WARNING=2000, ERROR=3000, CRITICAL=4000) – override-Mechanismus definieren.
- **journald Field-Set:** Endgültige Liste strukturierter Felder finalisieren.
- **GELF Default:** TCP+TLS gesetzt; Fallback auf UDP? Entscheidung abhängig von Betriebsvorgaben. Alternativ kann das bestehende Winlogbeat/Sysmon → Graylog Routing exklusiv genutzt werden; in diesem Fall bleibt der native GELF-Adapter deaktiviert.
- **Deployment:** Service-Wrapper/CLI zur Initialisierung in Worker-Umgebungen noch festzulegen.

## 12. Anhang: API-Stichproben
```python
import lib_log_rich as log

log.init(
    service="meinservice",
    env="prod",
    backend="auto",
    console_format="{ts} {level_icon} {name} — {message} {context}",
    html_format="{ts} {level_icon} {message} {context}",
    html_theme="dark",
)

logger = log.get("app.http")
# Konsolen-Stubs bleiben erreichbar:
#   python -m lib_log_rich
#   lib_log_rich --hello
#   lib_log_rich --version
# geben Metadatenbanner bzw. Version aus.


with log.bind(request_id="abc123", user_id="42", job_id="job-2025-09-21-001"):
    logger.info("Login ok")
    logger.error("Fehlgeschlagen", extra={"error_code": "AUTH_401"})

log.dump(format="html")
```

*Stand: 23. September 2025*
