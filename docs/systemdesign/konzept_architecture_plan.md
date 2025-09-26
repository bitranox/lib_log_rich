# Umsetzungsplan (TDD) — lib_log_rich Logging-Backbone

## 0. Ziele & Referenzen
- Grundlage: `docs/systemdesign/konzept_architecture.md` (Architekturleitfaden) und `docs/systemdesign/konzept.md` (Produktidee).
- Ziel: Vollständige Implementierung der beschriebenen Logging-Backbone-Architektur im TDD-Modus (rot → grün → refactor) mit prüfbarer Dokumentation, Tests und Adapter-Stubs/Fakes.
- Ergebnis: Produktionsreifer Kern für Konsole, journald, Windows Event Log, optionales GELF-Routing, Ringpuffer-Dumps, Konfigurations-API, Kontextbindung und Multiprozessfähigkeit.

## 1. Arbeitsprinzipien
1. **TDD-Zyklus je Arbeitspaket**
   - Schreibe zunächst einen fehlenden oder fehlschlagenden Test (Unit/Contract/Integration).
   - Implementiere nur so viel Code, dass der Test grün wird.
   - Refaktorisiere mit Fokus auf Clean Architecture/SOLID, halte Tests grün.
2. **Test-Typen**
   - *Unit*: Domain, Application, Hilfsfunktionen (pytest + hypothesis für Property-Checks).
   - *Contract*: Adapter-Implementierungen erhalten ein gemeinsames Test-Interface (`pytest.mark.parametrize` über Adapter-Factories).
   - *Integration*: QueueHandler/QueueListener, Multiprocessing, Dump-API (Pytest + multiprocessing helpers).
   - *Snapshot/Golden*: HTML/Console-Rendering (pytest + approvals oder snapshot fixture).
3. **Definition of Done (DoD) Pflichtpunkte**
   - Alle neuen/angepassten Tests grün (`make test`).
   - Linter/Typer: `ruff`, `pyright` ohne Fehler.
   - Dokumentation aktualisiert (`docs/systemdesign/module_reference.md`, README-Ausschnitt falls nötig).
   - Keine TODOs, keine toten Codepfade, Einhaltung aller Repository-Guidelines.

## 2. Schritt-für-Schritt-Plan

### Phase A — Testfundament & Infrastruktur
1. **A1: Test-Skelett & Fixtures**
   - *Beschreibung*: Einrichten von pytest-Factories, Fixtures für Queue, Context, Rich-Snapshot, Fake-Observer.
   - *TDD*: Beginne mit fehlenden Tests für bestehende Platzhalter (`tests/` erweitern).
   - *DoD*: 
     - [x] Neue Fixtures dokumentiert (docstring, doctest).
     - [x] Tests schlagen vor Implementierung fehl (rot) und laufen nach Minimalimplementierung grün.
     - [x] `tests/conftest.py` enthält Kontexte für Fake Clock/ID Provider.

2. **A2: Import-Linter & Architektur-Gates**
   - *Beschreibung*: Ergänze `pyproject.toml` um `import-linter`-Regeln (Domain → Application → Adapter) + CI Hook.
   - *DoD*: 
     - [x] `import-linter` Regeln abgebildet und dokumentiert.
     - [x] CI/`make test` bricht bei Verletzung ab.
     - [x] Tests für Regelwerk: `python -m importlinter` Teil von `make test` (rot/grün überprüft).

### Phase B — Domain Layer
3. **B1: Domain Value Objects & Events**
   - *Beschreibung*: Implementiere `LogEvent`, `LogContext`, `LogLevel`, `DumpFormat` als dataclasses (frozen, slots) inkl. Validierung.
   - *TDD*: 
     - Erstelle Tests für Invarianten (kein negativer Timestamp, Pflichtfelder, Normierung von Leveln).
   - *DoD*: 
     - [x] Domain-Tests decken Validierungen & Serialisierung (dict/JSON) ab.
     - [x] 100 % Branch-Coverage für Domain-Module.

4. **B2: Kontextbinding (contextvars)**
   - *Beschreibung*: `ContextBinder` API, `bind()/unbind()` Semantik, Propagation in Threads/Subprozessen (Serialisierung).
   - *TDD*: 
     - Tests für Kontextstapel, Fallback-Werte, Multiprozess-Sharing (pytest + multiprocessing).
   - *DoD*: 
     - [x] Kontext-API dokumentiert (`module_reference`).
     - [x] Tests zeigen, dass JOB-ID & Request-ID übernommen werden.

### Phase C — Application Layer
5. **C1: Ports & Protocols**
   - *Beschreibung*: Definiere `ConsolePort`, `StructuredBackendPort`, `GraylogPort`, `DumpPort`, `QueuePort`, `ClockPort`, `IdProvider`, `RateLimiterPort`, `ScrubberPort`, `UnitOfWork`.
   - *TDD*: 
     - Schreibe Contract-Tests als ABC/Protocol-Tests (pytest parametrisiert).
   - *DoD*: 
     - [x] Alle Ports mit docstrings (Warum/Was).
     - [x] Contract-Tests existieren, schlagen ohne Implementierung fehl und laufen mit Fakes.

6. **C2: Use Cases (Fan-out, Dump, Shutdown)**
   - *Beschreibung*: Implementiere Use-Cases `process_log_event`, `capture_dump`, `shutdown` mit Abhängigkeiten via Ports.
   - *TDD*: 
     - Tests für Loglevel-Filtern, Multi-Handler-Fan-out, HTML-Dump mit separatem Template.
   - *DoD*: 
     - [x] Use-Case Tests (Unit) + Integration (Queue + Ports) grün.
     - [x] Timestamps & IDs via injizierbaren Ports.

7. **C3: Configuration Use Case (`init` orchestrator)**
   - *Beschreibung*: Build-Funktion, die Ports/Adapter nach Konfiguration erstellt, QueueListener startet, Handler registriert; Flags erlauben das Deaktivieren von Ringpuffer, journald und Event Log für Konsole-only Deployments.
   - *TDD*: 
     - Tests für Konfigurationspfade (Konsole-only mit `enable_ring_buffer=False`, `enable_journald=False`, `enable_eventlog=False`; journald-only; auto; Graylog deaktiviert).
   - *DoD*: 
     - [x] ENV-Overrides getestet (`monkeypatch`).
     - [x] Clean shutdown sichert, dass Listener stoppt.

### Phase D — Adapter Layer (konkrete Implementierungen)
8. **D1: Console (Rich) Adapter**
   - *Beschreibung*: RichHandler mit Theme, Level-Icons, TTY-Erkennung, fallback zu plain.
   - *TDD*: 
     - Snapshot-Test (pytest approvals) + Unit Tests für Format.
   - *DoD*: 
     - [x] Unterstützt `force_color`, `no_color`, `isatty` False.
     - [x] Snapshot aktualisiert und reviewt.

9. **D2: StructuredBackend (journald)**
   - *Beschreibung*: Adapter, der strukturierte Felder (uppercase) liefert, fallback Mock wenn systemd nicht verfügbar.
   - *TDD*: 
     - Contract-Tests mit Fake-Journal-API.
   - *DoD*: 
     - [x] Linux-spezifische Teile gekapselt, Tests laufen cross-platform.
     - [x] Feldmapping (JOB_ID etc.) geprüft.

10. **D3: StructuredBackend (Windows Event Log)**
    - *Beschreibung*: Adapter auf Basis `pywin32`; Fallback Simulation.
    - *TDD*: 
      - Contract-Test mit Fake `win32evtlogutil` (monkeypatch).
    - *DoD*: 
      - [x] EventID-Mapping konfigurierbar + getestet.
      - [x] Tests dokumentieren Behavior, falls `pywin32` fehlt.

11. **D4: Graylog Adapter (optional)**
    - *Beschreibung*: TCP+TLS Client, Backoff, optional deaktiviert.
    - *TDD*: 
      - Contract-Tests mit Fake Server (`asyncio`-Stream) + Retry Tests.
    - *DoD*: 
      - [x] Deaktivierungs-Szenario (bestehende Winlogbeat-Pipeline) getestet (Adapterstub, no-op).
      - [x] Konfigurierbare Backoff-Parameter.

12. **D5: Dump Adapter (Text/JSON/HTML)**
    - *Beschreibung*: Ringpuffer → Exporter.
    - *TDD*: 
      - Tests für Rotationslimit (25k → konfigurierbar), HTML-Theme, JSON Schema.
    - *DoD*: 
      - [x] Ringpuffer property-tests (Hypothesis) für FIFO-Semantik.
      - [x] Dump Tests decken Path=none (return str) + Pfad-Schreiben.

13. **D6: Queue Infrastruktur**
    - *Beschreibung*: `QueueHandler`, `QueueListener`, Worker-Thread/Process.
    - *TDD*: 
      - Integrationstest mit Multiprocessing + Stress-Test (RateLimit, Backpressure).
    - *DoD*: 
      - [x] Kein Eventverlust (Test sendet N Events, erwartet N).
      - [x] Shutdown drain test.

14. **D7: Scrubber & RateLimiter**
    - *Beschreibung*: Sensible Felder maskieren, Rate-Limit Filter.
    - *TDD*: 
      - Unit Tests für Regex-Patterns, Rate-Limit per sliding window.
    - *DoD*: 
      - [x] Konfigurierbar via Ports.
      - [x] Dokumentierte Defaults.

### Phase E — CLI & Observability
15. **E1: CLI/Config Surface**
    - *Beschreibung*: CLI-Befehle (optional) oder API-Hooks für Dump-Trigger.
    - *TDD*: 
      - Tests mit Click `CliRunner`.
    - *DoD*: 
      - [x] CLI dokumentiert (README, Module Reference Update).

16. **E2: Telemetrie & Logging über Logging**
    - *Beschreibung*: Self-observability (diagnostic logger), Metrics Hook.
    - *TDD*: 
      - Tests stellen sicher, dass Diagnose-Events nicht rekurrieren.
    - *DoD*: 
      - [x] Optionaler Hook mit Dummy-Test.

### Phase F — Abschluss & Doku
17. **F1: Dokumentation & Beispiele**
    - *Beschreibung*: Aktualisierung `docs/systemdesign/module_reference.md`, README Usage, Code-Beispiele.
    - *DoD*: 
      - [x] Beispiele lauffähig (`python -c "import lib_log_rich; print(lib_log_rich.summary_info())"`).
      - [x] Doctests grün.

18. **F2: Qualitätssicherung & Release-Vorbereitung**
    - *Beschreibung*: Gesamt `make test`, Coverage ≥ 90 %, `make build` Smoke.
    - *DoD*: 
      - [x] Coverage-Report dokumentiert.
      - [x] CHANGELOG-Eintrag.

## 3. Zusätzliche Anweisungen
- **Branch-Strategie**: Feature Branch pro Phase (A, B, …) → PR → Review → Merge.
- **Mocks/Fakes**: Nutze Fakes für Systemabhängigkeiten (journald, Windows) und aktiviere echte Adapter nur in optionalen manuellen Tests.
- **CI-Erweiterung**: Ergänze Matrix (Linux, Windows) sobald Adapter implementiert sind; zunächst Fakes.
- **Rollback**: Jede Phase endet mit gesundem `main`; im Fehlerfall `git revert` der Phase.

## 4. Definition of Done (gesamt)
- Alle Phase-DoDs erfüllt.
- `konzept_architecture.md` bleibt Quelle der Wahrheit; Abweichungen erfordern Update + Review.
- Dokumentierter TDD-Nachweis: Commit-Verlauf zeigt Test-first (Tests rot), Implementation, Refactor.
- Keine offenen TODO/FIXME.
- Sicherheits-/Scrubbing-Regeln aktiv und getestet.

*Letzte Aktualisierung: 23. September 2025*
