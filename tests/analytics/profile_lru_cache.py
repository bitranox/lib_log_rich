#!/usr/bin/env python3
"""Profile LRU cache performance across the test suite.

This script analyzes cache statistics by inspecting cache_info() on
lru_cached functions after running the test suite.
"""

import sys
from pathlib import Path

# Add src to path (from tests/analytics to project root)
project_root = Path(__file__).parent.parent.parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))


def get_cache_info_from_function(func, func_path: str) -> dict:
    """Extract cache info from an lru_cached function."""
    try:
        if hasattr(func, "cache_info"):
            info = func.cache_info()
            return {
                "path": func_path,
                "hits": info.hits,
                "misses": info.misses,
                "maxsize": info.maxsize,
                "currsize": info.currsize,
            }
    except Exception:
        pass
    return None


def collect_cache_stats():
    """Collect statistics from all cached functions."""
    stats = {}

    # Import modules (after tests have run)
    try:
        from lib_log_rich.adapters import dump, scrubber
        from lib_log_rich.adapters.console import rich_console
        from lib_log_rich.domain import analytics, levels
        from lib_log_rich.domain import dump as domain_dump
        from lib_log_rich.runtime.settings import resolvers

        # Tier 1 functions
        cache_functions = {
            "Tier 1 - Hot Paths": {
                "LogLevel.from_name": (levels.LogLevel.from_name, 16),
                "LogLevel.from_numeric": (levels.LogLevel.from_numeric, 8),
            },
            "Tier 2 - Format/Scrub": {
                "RegexScrubber._normalise_key": (scrubber.RegexScrubber._normalise_key, 32),
                "_resolve_template": (rich_console._resolve_template, 16),
                "_resolve_preset": (dump._resolve_preset, 8),
                "_resolve_theme_styles": (dump._resolve_theme_styles, 8),
                "DumpFormat.from_name": (domain_dump.DumpFormat.from_name, 8),
                "_load_console_themes": (dump._load_console_themes, None),
            },
            "Tier 3 - Configuration": {
                "parse_console_styles": (resolvers.parse_console_styles, 8),
                "parse_scrub_patterns": (resolvers.parse_scrub_patterns, 8),
            },
            "Tier 4 - Analytics/Monitoring": {
                "SeverityMonitor._normalise_reason": (analytics.SeverityMonitor._normalise_reason, 16),
            },
        }

        for tier, functions in cache_functions.items():
            tier_stats = {}
            for func_name, (func, expected_maxsize) in functions.items():
                info = get_cache_info_from_function(func, func_name)
                if info:
                    info["expected_maxsize"] = expected_maxsize
                    tier_stats[func_name] = info
            if tier_stats:
                stats[tier] = tier_stats

    except Exception as e:
        print(f"Error collecting cache stats: {e}")
        import traceback

        traceback.print_exc()

    return stats


def print_cache_report(stats: dict):
    """Print a formatted cache statistics report."""
    print("\n" + "=" * 110)
    print("LRU CACHE PROFILING REPORT")
    print("=" * 110)

    overall_calls = 0
    overall_hits = 0
    overall_misses = 0

    for tier_name, tier_stats in stats.items():
        print(f"\n{tier_name}")
        print("-" * 110)
        print(f"{'Function':<35} {'Calls':>10} {'Hits':>10} {'Misses':>10} {'Hit Rate':>10} {'Size':>8} {'MaxSize':>8} {'Effectiveness':<15}")
        print("-" * 110)

        tier_calls = 0
        tier_hits = 0
        tier_misses = 0

        for func_name, info in sorted(tier_stats.items()):
            hits = info["hits"]
            misses = info["misses"]
            total = hits + misses
            currsize = info["currsize"]
            maxsize = info["maxsize"]

            tier_calls += total
            tier_hits += hits
            tier_misses += misses

            hit_rate = (hits / total * 100) if total > 0 else 0

            # Categorize effectiveness
            if hit_rate >= 90:
                effectiveness = "Excellent ⭐⭐⭐"
            elif hit_rate >= 70:
                effectiveness = "Good ⭐⭐"
            elif hit_rate >= 50:
                effectiveness = "Moderate ⭐"
            elif hit_rate >= 25:
                effectiveness = "Poor"
            else:
                effectiveness = "Very Poor"

            maxsize_str = str(maxsize) if maxsize is not None else "∞"

            print(f"{func_name:<35} {total:>10,} {hits:>10,} {misses:>10,} {hit_rate:>9.1f}% {currsize:>8} {maxsize_str:>8} {effectiveness:<15}")

        # Tier summary
        if tier_calls > 0:
            tier_hit_rate = tier_hits / tier_calls * 100
            print("-" * 110)
            print(f"{'TIER TOTAL':<35} {tier_calls:>10,} {tier_hits:>10,} {tier_misses:>10,} {tier_hit_rate:>9.1f}%")

        overall_calls += tier_calls
        overall_hits += tier_hits
        overall_misses += tier_misses

    # Overall summary
    if overall_calls > 0:
        overall_hit_rate = overall_hits / overall_calls * 100
        print("\n" + "=" * 110)
        print("OVERALL SUMMARY")
        print("-" * 110)
        print(f"Total Functions Analyzed: {sum(len(t) for t in stats.values())}")
        print(f"Total Calls:   {overall_calls:>15,}")
        print(f"Total Hits:    {overall_hits:>15,}")
        print(f"Total Misses:  {overall_misses:>15,}")
        print(f"Overall Hit Rate: {overall_hit_rate:>10.1f}%")

        # Calculate potential performance gain
        print("\nCache Effectiveness:")
        print(f"  - {overall_hits:,} cache hits avoided recomputation")
        print(f"  - {overall_misses:,} cache misses required computation")

        # Calculate memory usage estimate
        total_cache_entries = sum(info["currsize"] for tier_stats in stats.values() for info in tier_stats.values())
        print("\nMemory Usage:")
        print(f"  - Total cached entries: {total_cache_entries}")
        print("  - Estimated memory: <1KB (lightweight string/enum caching)")

        print("=" * 110 + "\n")


def main():
    """Run tests and collect cache statistics."""
    import pytest

    print("Running test suite to collect cache statistics...")
    print("=" * 110 + "\n")

    # Run pytest from project root, excluding analytics folder
    tests_dir = str(project_root / "tests")
    exit_code = pytest.main(
        [
            "-v",
            "--tb=short",
            "-x",  # Stop on first failure for faster iteration
            "--ignore=" + str(project_root / "tests" / "analytics"),
            tests_dir,
        ]
    )

    print(f"\nTest suite completed with exit code: {exit_code}")

    # Collect and display cache statistics
    stats = collect_cache_stats()

    if stats:
        print_cache_report(stats)
    else:
        print("\nNo cache statistics collected. Functions may not have been called during tests.")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
