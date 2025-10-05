from __future__ import annotations

import pytest

from lib_log_rich import runtime
from lib_log_rich.runtime import RuntimeConfig


@pytest.mark.parametrize(
    "env_value, error_match",
    [
        ("localhost", "HOST:PORT"),
        ("host:notaport", "must be an integer"),
        ("host:0", "must be positive"),
    ],
)
def test_invalid_graylog_endpoint_values(monkeypatch: pytest.MonkeyPatch, env_value: str, error_match: str) -> None:
    monkeypatch.setenv("LOG_GRAYLOG_ENDPOINT", env_value)
    with pytest.raises(ValueError, match=error_match):
        runtime.init(
            RuntimeConfig(
                service="svc",
                environment="env",
                queue_enabled=False,
                enable_graylog=True,
            )
        )


@pytest.mark.parametrize(
    "env_value, error_match",
    [
        ("bad-format", "MAX:WINDOW_SECONDS"),
        ("0:1.0", "must be positive"),
        ("5:0", "must be positive"),
    ],
)
def test_invalid_rate_limit_values(monkeypatch: pytest.MonkeyPatch, env_value: str, error_match: str) -> None:
    monkeypatch.setenv("LOG_RATE_LIMIT", env_value)
    with pytest.raises(ValueError, match=error_match):
        runtime.init(
            RuntimeConfig(
                service="svc",
                environment="env",
                queue_enabled=False,
                enable_graylog=False,
            )
        )
