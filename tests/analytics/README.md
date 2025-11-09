# Cache Analytics & Profiling Tools

This directory contains tools for analyzing and profiling LRU cache performance in `lib_log_rich`. These are **not** part of the regular test suite and are excluded from normal pytest runs.

## Contents

### Scripts

1. **`profile_lru_cache.py`** - Test Suite Cache Profiler
   - Runs the entire test suite (599 tests)
   - Collects cache statistics from all `@lru_cache` decorated functions
   - Generates detailed performance report
   - Usage: `python3 tests/analytics/profile_lru_cache.py`

2. **`stress_test_cache.py`** - High-Volume Cache Analyzer
   - Generates 1,000 log entries with varying levels
   - Simulates real-world usage patterns
   - Measures throughput and cache effectiveness
   - Usage: `python3 tests/analytics/stress_test_cache.py`

### Reports

1. **`CACHE_PROFILING_REPORT.md`** - Initial profiling analysis
   - Documents PR #4 cache optimizations
   - Test suite and stress test results
   - Performance metrics and recommendations

2. **`FINAL_CACHE_ANALYSIS_SUMMARY.md`** - Comprehensive final report
   - Complete cache inventory (11 functions)
   - Before/after comparisons
   - Production readiness assessment
   - Future optimization recommendations

## Running the Tools

### From Project Root

```bash
# Profile cache performance across test suite
python3 tests/analytics/profile_lru_cache.py

# Run stress test with 1,000 log entries
python3 tests/analytics/stress_test_cache.py
```

### Expected Results

**Test Suite Profiling:**
- Overall hit rate: ~90%
- Total calls: ~1,000
- Functions analyzed: 11
- Execution time: ~15-20 seconds

**Stress Test:**
- Overall hit rate: ~99.6%
- Throughput: ~37,000+ logs/second
- Cache efficiency: ~230x (hits per miss)
- Execution time: <1 second

## Cache Functions Tracked

### Tier 1 - Hot Paths
- `LogLevel.from_name` (maxsize=16)
- `LogLevel.from_numeric` (maxsize=8)

### Tier 2 - Format/Scrub
- `RegexScrubber._normalise_key` (maxsize=32)
- `_resolve_template` (maxsize=16)
- `_resolve_preset` (maxsize=8)
- `_resolve_theme_styles` (maxsize=8)
- `DumpFormat.from_name` (maxsize=8)
- `_load_console_themes` (maxsize=∞)

### Tier 3 - Configuration
- `parse_console_styles` (maxsize=8)
- `parse_scrub_patterns` (maxsize=8)

### Tier 4 - Analytics/Monitoring
- `SeverityMonitor._normalise_reason` (maxsize=16)

## Configuration

These scripts are **automatically excluded** from normal test runs via:

```toml
# pyproject.toml
[tool.pytest.ini_options]
addopts = ["--doctest-modules", "--ignore=examples", "--ignore=tests/analytics"]
```

## Notes

- These tools are for performance analysis and optimization validation
- They are not part of CI/CD test coverage requirements
- Run manually when evaluating cache performance or adding new optimizations
- Results may vary slightly based on system load and Python version
