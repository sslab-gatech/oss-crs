#!/usr/bin/env python3
"""
Parse incremental build test logs and extract benchmark metrics to CSV.

Usage:
    python parse_inc_build_logs.py <log_directory>
    python parse_inc_build_logs.py /path/to/logs/c_inc_build_test_20260107_033444

Output:
    Creates a CSV file in the same directory as the log directory with the name:
    <log_directory_name>_summary.csv
"""

import argparse
import csv
import re
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


@dataclass
class BenchmarkMetrics:
    """Holds parsed metrics for a single benchmark log file."""
    benchmark_name: str
    # Build time metrics
    build_time_without_inc: Optional[float] = None
    build_time_with_inc: Optional[float] = None
    build_time_saved: Optional[float] = None
    build_time_reduction_pct: Optional[float] = None
    build_speedup: Optional[float] = None
    avg_rebuild_time_with_patch: Optional[float] = None
    # Test time metrics
    num_povs: Optional[int] = None
    baseline_test_time: Optional[float] = None
    rts_avg_test_time: Optional[float] = None
    test_time_saved: Optional[float] = None
    test_time_reduction_pct: Optional[float] = None
    test_speedup: Optional[float] = None
    # Test count metrics
    baseline_tests_run: Optional[float] = None
    rts_tests_run_avg: Optional[float] = None
    baseline_test_cases: Optional[int] = None
    rts_test_cases_unique: Optional[int] = None


def parse_float(s: str) -> Optional[float]:
    """Parse a float from string, handling various formats."""
    try:
        # Remove 's' suffix if present (for time values like "12.08s")
        s = s.strip().rstrip('s')
        return float(s)
    except (ValueError, AttributeError):
        return None


def parse_int(s: str) -> Optional[int]:
    """Parse an int from string."""
    try:
        return int(float(s.strip()))
    except (ValueError, AttributeError):
        return None


def parse_log_file(log_path: Path) -> Optional[BenchmarkMetrics]:
    """Parse a single log file and extract metrics."""
    benchmark_name = log_path.stem  # filename without extension

    metrics = BenchmarkMetrics(benchmark_name=benchmark_name)

    try:
        content = log_path.read_text(errors='ignore')
    except Exception as e:
        print(f"Warning: Could not read {log_path}: {e}", file=sys.stderr)
        return None

    # Find the final benchmark results section
    # Look for "[Build Time Comparison]" followed by metrics

    # Pattern for build time comparison
    build_section_pattern = re.compile(
        r'\[Build Time Comparison\].*?'
        r'Build time \(w/o inc build\):\s*([\d.]+)s.*?'
        r'(?:Build time|Rebuild time) \(w/ inc build,.*?\):\s*([\d.]+)s.*?'
        r'Time saved:\s*([-\d.]+)s\s*\(([-\d.]+)%\s*reduction,\s*([\d.]+)x\)',
        re.DOTALL
    )

    # Pattern for avg rebuild time with patch
    avg_rebuild_with_patch_pattern = re.compile(
        r'Avg rebuild time \(w/ inc build, w/ patch\):\s*([\d.]+)s',
        re.DOTALL
    )

    # Pattern for test time comparison
    test_section_pattern = re.compile(
        r'\[Test Time Comparison\]\s*\(avg over (\d+) POV\(s\)\).*?'
        r'Baseline \(before snapshot\):\s*([\d.]+)s.*?'
        r'with RTS \(avg after snapshot\):\s*([\d.]+)s.*?'
        r'Avg time saved:\s*([-\d.]+)s\s*\(([-\d.]+)%\s*reduction\).*?'
        r'Avg speedup:\s*([\d.]+)x',
        re.DOTALL
    )

    # Pattern for test count metrics
    test_count_pattern = re.compile(
        r'Baseline tests run:\s*([\d.]+).*?'
        r'with RTS tests run \(avg\):\s*([\d.]+).*?'
        r'Baseline test cases:\s*(\d+).*?'
        r'with RTS test cases \(total unique\):\s*(\d+)',
        re.DOTALL
    )

    # Find all matches and use the last one (final results)
    build_matches = list(build_section_pattern.finditer(content))
    avg_rebuild_matches = list(avg_rebuild_with_patch_pattern.finditer(content))
    test_matches = list(test_section_pattern.finditer(content))
    test_count_matches = list(test_count_pattern.finditer(content))

    if build_matches:
        m = build_matches[-1]  # Use last match
        metrics.build_time_without_inc = parse_float(m.group(1))
        metrics.build_time_with_inc = parse_float(m.group(2))
        metrics.build_time_saved = parse_float(m.group(3))
        metrics.build_time_reduction_pct = parse_float(m.group(4))
        metrics.build_speedup = parse_float(m.group(5))

    if avg_rebuild_matches:
        m = avg_rebuild_matches[-1]  # Use last match
        metrics.avg_rebuild_time_with_patch = parse_float(m.group(1))

    if test_matches:
        m = test_matches[-1]  # Use last match
        metrics.num_povs = parse_int(m.group(1))
        metrics.baseline_test_time = parse_float(m.group(2))
        metrics.rts_avg_test_time = parse_float(m.group(3))
        metrics.test_time_saved = parse_float(m.group(4))
        metrics.test_time_reduction_pct = parse_float(m.group(5))
        metrics.test_speedup = parse_float(m.group(6))

    if test_count_matches:
        m = test_count_matches[-1]  # Use last match
        metrics.baseline_tests_run = parse_float(m.group(1))
        metrics.rts_tests_run_avg = parse_float(m.group(2))
        metrics.baseline_test_cases = parse_int(m.group(3))
        metrics.rts_test_cases_unique = parse_int(m.group(4))

    # Only return metrics if we found at least some data
    if metrics.build_time_without_inc is not None or metrics.baseline_test_time is not None:
        return metrics

    return None


def write_csv(metrics_list: list[BenchmarkMetrics], output_path: Path):
    """Write metrics to a CSV file."""
    fieldnames = [
        'benchmark_name',
        'build_time_without_inc_s',
        'build_time_with_inc_s',
        'build_time_saved_s',
        'build_time_reduction_pct',
        'build_speedup',
        'avg_rebuild_time_with_patch_s',
        'num_povs',
        'baseline_test_time_s',
        'rts_avg_test_time_s',
        'test_time_saved_s',
        'test_time_reduction_pct',
        'test_speedup',
        'baseline_tests_run',
        'rts_tests_run_avg',
        'baseline_test_cases',
        'rts_test_cases_unique',
    ]

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for m in metrics_list:
            writer.writerow({
                'benchmark_name': m.benchmark_name,
                'build_time_without_inc_s': m.build_time_without_inc,
                'build_time_with_inc_s': m.build_time_with_inc,
                'build_time_saved_s': m.build_time_saved,
                'build_time_reduction_pct': m.build_time_reduction_pct,
                'build_speedup': m.build_speedup,
                'avg_rebuild_time_with_patch_s': m.avg_rebuild_time_with_patch,
                'num_povs': m.num_povs,
                'baseline_test_time_s': m.baseline_test_time,
                'rts_avg_test_time_s': m.rts_avg_test_time,
                'test_time_saved_s': m.test_time_saved,
                'test_time_reduction_pct': m.test_time_reduction_pct,
                'test_speedup': m.test_speedup,
                'baseline_tests_run': m.baseline_tests_run,
                'rts_tests_run_avg': m.rts_tests_run_avg,
                'baseline_test_cases': m.baseline_test_cases,
                'rts_test_cases_unique': m.rts_test_cases_unique,
            })


def main():
    parser = argparse.ArgumentParser(
        description='Parse incremental build test logs and extract benchmark metrics to CSV.'
    )
    parser.add_argument(
        'log_directory',
        type=Path,
        help='Path to the directory containing .log files'
    )
    parser.add_argument(
        '-o', '--output',
        type=Path,
        default=None,
        help='Output CSV file path (default: <log_directory>_summary.csv)'
    )

    args = parser.parse_args()

    log_dir = args.log_directory
    if not log_dir.is_dir():
        print(f"Error: {log_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Find all .log files, excluding sanity- prefix
    all_log_files = sorted(log_dir.glob('*.log'))
    log_files = [f for f in all_log_files if not f.name.startswith('sanity-')]
    skipped = len(all_log_files) - len(log_files)

    if not log_files:
        print(f"Error: No .log files found in {log_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(log_files)} log files in {log_dir} (skipped {skipped} sanity-* files)")

    # Parse all log files
    metrics_list = []
    success_count = 0
    for log_file in log_files:
        metrics = parse_log_file(log_file)
        if metrics:
            metrics_list.append(metrics)
            success_count += 1
        else:
            print(f"  Warning: No metrics found in {log_file.name}", file=sys.stderr)

    print(f"Successfully parsed {success_count} / {len(log_files)} log files")

    # Sort by benchmark name
    metrics_list.sort(key=lambda m: m.benchmark_name)

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        output_path = log_dir.parent / f"{log_dir.name}_summary.csv"

    # Write CSV
    write_csv(metrics_list, output_path)
    print(f"Wrote summary to: {output_path}")

    # Print summary statistics
    if metrics_list:
        print("\n--- Summary Statistics ---")

        # Build time stats
        build_speedups = [m.build_speedup for m in metrics_list if m.build_speedup is not None]
        if build_speedups:
            avg_build_speedup = sum(build_speedups) / len(build_speedups)
            print(f"Build Speedup: avg={avg_build_speedup:.2f}x, "
                  f"min={min(build_speedups):.2f}x, max={max(build_speedups):.2f}x")

        # Test time stats
        test_speedups = [m.test_speedup for m in metrics_list if m.test_speedup is not None]
        if test_speedups:
            avg_test_speedup = sum(test_speedups) / len(test_speedups)
            print(f"Test Speedup (RTS): avg={avg_test_speedup:.2f}x, "
                  f"min={min(test_speedups):.2f}x, max={max(test_speedups):.2f}x")


if __name__ == '__main__':
    main()
