"""Compatibility layer for runtime settings helpers.

This module re-exports data models and resolver utilities from
``lib_log_rich.runtime.settings`` so existing imports continue to work
while the implementation remains modular.
"""

from __future__ import annotations

from .settings.models import (
    DEFAULT_SCRUB_PATTERNS,
    ConsoleAppearance,
    DiagnosticHook,
    DumpDefaults,
    FeatureFlags,
    GraylogSettings,
    PayloadLimits,
    RuntimeConfig,
    RuntimeSettings,
    coerce_console_styles_input,
)
from .settings.resolvers import (
    build_runtime_settings,
    coerce_graylog_endpoint,
    coerce_rate_limit,
    env_bool,
    parse_console_styles,
    parse_scrub_patterns,
    resolve_console,
    resolve_console_palette,
    resolve_dump_defaults,
    resolve_feature_flags,
    resolve_graylog,
    resolve_levels,
    resolve_queue_maxsize,
    resolve_queue_policy,
    resolve_queue_stop_timeout,
    resolve_queue_timeout,
    resolve_rate_limit,
    resolve_scrub_patterns,
    service_and_environment,
)

__all__ = [
    "DEFAULT_SCRUB_PATTERNS",
    "ConsoleAppearance",
    "DiagnosticHook",
    "DumpDefaults",
    "FeatureFlags",
    "GraylogSettings",
    "PayloadLimits",
    "RuntimeConfig",
    "RuntimeSettings",
    "build_runtime_settings",
    "coerce_console_styles_input",
    "coerce_graylog_endpoint",
    "coerce_rate_limit",
    "env_bool",
    "parse_console_styles",
    "parse_scrub_patterns",
    "resolve_console",
    "resolve_console_palette",
    "resolve_dump_defaults",
    "resolve_feature_flags",
    "resolve_graylog",
    "resolve_levels",
    "resolve_queue_maxsize",
    "resolve_queue_policy",
    "resolve_queue_stop_timeout",
    "resolve_queue_timeout",
    "resolve_rate_limit",
    "resolve_scrub_patterns",
    "service_and_environment",
]
