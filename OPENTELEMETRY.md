# OpenTelemetry Logging Integration – Design & Test Plan

## 1. Introduction

This document provides a comprehensive overview of **OpenTelemetry (OTel)**, why it is relevant for a Python logging library, and how it can be integrated alongside existing Graylog support. It also outlines a testing setup to validate the implementation before production rollout.

---

## 2. What is OpenTelemetry?

OpenTelemetry is an open-source observability framework maintained under the **CNCF**.  
It provides **standardized APIs, SDKs, and exporters** for collecting **logs, metrics, and traces**, and sending them to various backends (e.g., Graylog, Loki, Elastic, Datadog, Splunk).

### Key Benefits
- **Vendor-neutral**: Support many observability backends through one unified protocol (OTLP).
- **Correlation**: Automatic inclusion of `trace_id` and `span_id` in logs when tracing is active.
- **Future-proof**: Widely adopted standard in modern observability.
- **Flexibility**: Centralized configuration via the OTel Collector.

---

## 3. Current State

- Our logging library **already supports Graylog** (e.g., via GELF).
- Many users rely on Graylog for centralized log aggregation.
- However, observability stacks are evolving, and many organizations want **logs + traces + metrics** in a single pipeline.

---

## 4. Why Add OpenTelemetry Support?

1. **Broader ecosystem**: Supporting OTLP unlocks immediate compatibility with many systems (Grafana, Datadog, Splunk, Tempo, Elastic, etc.).
2. **Direct Graylog integration**: Graylog exposes an **OpenTelemetry (gRPC) Input** on port 4317, allowing OTLP traffic to reach Graylog without additional translation layers.
3. **Correlation**: Logs enriched with trace/span context improve root-cause analysis.
4. **Optional adoption**: OTel support can be implemented as an optional backend while retaining GELF for straightforward deployments.

---

## 5. Proposed Implementation

### 5.1 Strategy
- Add an **OTLP Exporter Handler** to the library.
- Make it **opt-in** and **optional dependency** (import only if `opentelemetry` packages are installed).
- Maintain backward compatibility with Graylog-native (GELF) integration.

### 5.2 Core Features
- **Log record mapping**:
  - `level` → OTel `severity_text` and `severity_number`.
  - Resource attributes: `service.name`, `service.version`, `deployment.environment`.
  - Custom log fields → OTel attributes.
- **Context propagation**:
  - If OTel tracing is active, automatically attach `trace_id` and `span_id`.
- **Performance & reliability**:
  - Use batching processors.
  - Configurable timeouts, retries, and backpressure strategies.

### 5.3 Example Usage

```python
from mylogger import enable_otlp_logging

# Enable OTel logging (via Collector)
enable_otlp_logging(
    service_name="checkout-service",
    endpoint="http://localhost:4318",   # OTel Collector
    insecure=True,
    resource_attrs={"deployment.environment": "dev"}
)

logger = get_logger("checkout")
logger.info("Order created", extra={"attributes": {"user.id": "42"}})
```

---

## 6. Testing Setup

### 6.1 Components

* **Option A (Recommended for development)**:

  * Local **OpenTelemetry Collector** (Docker container).
  * Collector receives OTLP logs and exports them to **stdout** for validation.
* **Option B (Direct Graylog test)**:

  * Enable Graylog **OpenTelemetry (gRPC) Input** on port 4317.
  * Send logs directly via OTLP/gRPC.

### 6.2 Minimal Collector Config (`otel-config.yaml`)

```yaml
receivers:
  otlp:
    protocols:
      http:  # Default port 4318
      grpc:  # Default port 4317

exporters:
  logging:    # Debug: print to stdout
    loglevel: debug
  otlp/graylog:   # Optional: forward to Graylog OTel input
    endpoint: "GRAYLOG_HOST:4317"
    tls:
      insecure: true   # Only for testing

service:
  pipelines:
    logs:
      receivers: [otlp]
      exporters: [logging, otlp/graylog]
```

Run the collector:

```bash
docker run --rm -p 4317:4317 -p 4318:4318 \
  -v "$PWD/otel-config.yaml:/etc/otelcol/config.yaml" \
  otel/opentelemetry-collector:latest
```

### 6.3 Python Test Snippet

```python
import logging
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler, BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

# Define resource
resource = Resource.create({
    "service.name": "otel-test",
    "deployment.environment": "dev"
})

# Setup OTel logging
provider = LoggerProvider(resource=resource)
exporter = OTLPLogExporter(endpoint="http://localhost:4318/v1/logs", timeout=10000)
provider.add_log_record_processor(BatchLogRecordProcessor(exporter))

handler = LoggingHandler(level=logging.NOTSET, logger_provider=provider)
log = logging.getLogger("demo")
log.setLevel(logging.INFO)
log.addHandler(handler)

# Test log
log.info("Hello from OpenTelemetry", extra={"attributes": {"feature": "checkout", "user.id": "42"}})
```

Expected outcome: log record visible in Collector console output, with attributes and severity.

---

## 7. Roadmap

1. **Phase 1 – Prototype**

   * Implement OTLP exporter handler.
   * Verify mapping of levels, attributes, and resource fields.
   * Test with local Collector (`logging` exporter).

2. **Phase 2 – Graylog Integration**

   * Connect to Graylog OpenTelemetry Input (gRPC).
   * Validate ingestion and searchability inside Graylog.

3. **Phase 3 – Production-Ready**

   * Add configuration via environment variables (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`).
   * Add batching/queueing options.
   * Document trace correlation.
   * Publish library update.

---

## 8. Conclusion

Supporting OpenTelemetry in the logging library will:

* Keep Graylog compatibility (via GELF or OTLP).
* Unlock interoperability with other observability backends.
* Enable full correlation between **logs, metrics, and traces**.
* Future-proof the library for modern observability ecosystems.

The recommended next step is to implement an **OTLP Handler** as an optional backend, validate using the **OpenTelemetry Collector**, and then connect directly to **Graylog's OpenTelemetry Input**.
