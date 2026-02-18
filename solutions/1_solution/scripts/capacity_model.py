#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SECONDS_PER_MONTH = 2_592_000


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate monthly capacity projections from load harness output."
    )
    parser.add_argument(
        "--input",
        default="worklog/evidence/load/latest-load-report.json",
    )
    parser.add_argument(
        "--output-markdown",
        default="worklog/evidence/load/latest-capacity-model.md",
    )
    parser.add_argument(
        "--output-json",
        default="worklog/evidence/load/latest-capacity-model.json",
    )
    parser.add_argument(
        "--compare-input",
        default="",
        help="Optional baseline load report to compare against --input (treated as tuned).",
    )
    parser.add_argument("--polls-per-task", type=float, default=3.0)
    parser.add_argument("--utilization", type=float, default=0.7)
    return parser.parse_args()


def _resolve_path(repo_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (repo_root / path).resolve()


def _bytes_to_mib(value: int | float) -> float:
    return round(float(value) / 1_048_576.0, 3)


def _profile_stream_maxima(profile: dict[str, Any]) -> tuple[int, int, int]:
    stream = profile.get("stream_observability")
    if not isinstance(stream, dict):
        return (0, 0, 0)

    stream_length = stream.get("stream_length")
    pel_pending = stream.get("pel_pending")
    redis_memory = stream.get("redis_used_memory_bytes")

    stream_max = int(stream_length.get("max", 0)) if isinstance(stream_length, dict) else 0
    pel_max = int(pel_pending.get("max", 0)) if isinstance(pel_pending, dict) else 0
    memory_max = int(redis_memory.get("max", 0)) if isinstance(redis_memory, dict) else 0
    return (stream_max, pel_max, memory_max)


def _build_profile_rows(
    load_report: dict[str, Any],
    *,
    utilization: float,
    polls_per_task: float,
) -> list[dict[str, float | str | int]]:
    rows: list[dict[str, float | str | int]] = []
    for profile in load_report.get("profiles", []):
        if not isinstance(profile, dict):
            continue

        throughput_rps = float(profile.get("throughput_rps", 0.0))
        sustained_rps = throughput_rps * utilization
        monthly_tasks = sustained_rps * SECONDS_PER_MONTH
        monthly_polls = monthly_tasks * polls_per_task
        stream_max, pel_max, redis_memory_max = _profile_stream_maxima(profile)
        latencies = profile.get("latency_ms")
        p95_ms = float(latencies.get("p95", 0.0)) if isinstance(latencies, dict) else 0.0
        rows.append(
            {
                "profile": str(profile.get("profile", "unknown")),
                "accepted": int(profile.get("accepted", 0)),
                "throughput_rps_raw": round(throughput_rps, 4),
                "throughput_rps_sustained": round(sustained_rps, 4),
                "monthly_tasks": round(monthly_tasks, 2),
                "monthly_polls": round(monthly_polls, 2),
                "p95_ms": p95_ms,
                "stream_max": stream_max,
                "pel_max": pel_max,
                "redis_memory_max_bytes": redis_memory_max,
                "redis_memory_max_mib": _bytes_to_mib(redis_memory_max),
            }
        )
    return rows


def _build_compare_rows(
    baseline_report: dict[str, Any],
    tuned_report: dict[str, Any],
) -> list[dict[str, float | str | int]]:
    baseline_by_profile = {
        str(profile.get("profile", "unknown")): profile
        for profile in baseline_report.get("profiles", [])
        if isinstance(profile, dict)
    }
    tuned_by_profile = {
        str(profile.get("profile", "unknown")): profile
        for profile in tuned_report.get("profiles", [])
        if isinstance(profile, dict)
    }

    rows: list[dict[str, float | str | int]] = []
    for profile_name in sorted(set(baseline_by_profile) | set(tuned_by_profile)):
        baseline_profile = baseline_by_profile.get(profile_name)
        tuned_profile = tuned_by_profile.get(profile_name)
        if baseline_profile is None or tuned_profile is None:
            continue

        baseline_rps = float(baseline_profile.get("throughput_rps", 0.0))
        tuned_rps = float(tuned_profile.get("throughput_rps", 0.0))

        baseline_latency = baseline_profile.get("latency_ms")
        tuned_latency = tuned_profile.get("latency_ms")
        baseline_p95 = (
            float(baseline_latency.get("p95", 0.0)) if isinstance(baseline_latency, dict) else 0.0
        )
        tuned_p95 = float(tuned_latency.get("p95", 0.0)) if isinstance(tuned_latency, dict) else 0.0

        baseline_stream_max, baseline_pel_max, baseline_mem_max = _profile_stream_maxima(
            baseline_profile
        )
        tuned_stream_max, tuned_pel_max, tuned_mem_max = _profile_stream_maxima(tuned_profile)

        rows.append(
            {
                "profile": profile_name,
                "throughput_rps_baseline": round(baseline_rps, 4),
                "throughput_rps_tuned": round(tuned_rps, 4),
                "throughput_rps_delta": round(tuned_rps - baseline_rps, 4),
                "p95_ms_baseline": round(baseline_p95, 3),
                "p95_ms_tuned": round(tuned_p95, 3),
                "p95_ms_delta": round(tuned_p95 - baseline_p95, 3),
                "stream_max_baseline": baseline_stream_max,
                "stream_max_tuned": tuned_stream_max,
                "stream_max_delta": tuned_stream_max - baseline_stream_max,
                "pel_max_baseline": baseline_pel_max,
                "pel_max_tuned": tuned_pel_max,
                "pel_max_delta": tuned_pel_max - baseline_pel_max,
                "redis_memory_max_mib_baseline": _bytes_to_mib(baseline_mem_max),
                "redis_memory_max_mib_tuned": _bytes_to_mib(tuned_mem_max),
                "redis_memory_max_mib_delta": round(
                    _bytes_to_mib(tuned_mem_max) - _bytes_to_mib(baseline_mem_max), 3
                ),
            }
        )
    return rows


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    input_path = _resolve_path(repo_root, args.input)
    output_md = _resolve_path(repo_root, args.output_markdown)
    output_json = _resolve_path(repo_root, args.output_json)

    load_report = json.loads(input_path.read_text())
    rows = _build_profile_rows(
        load_report,
        utilization=float(args.utilization),
        polls_per_task=float(args.polls_per_task),
    )

    model = {
        "input": str(input_path),
        "assumptions": {
            "utilization": args.utilization,
            "polls_per_task": args.polls_per_task,
            "seconds_per_month": SECONDS_PER_MONTH,
        },
        "profiles": rows,
    }

    compare_rows: list[dict[str, float | str | int]] = []
    compare_path: Path | None = None
    if args.compare_input:
        compare_path = _resolve_path(repo_root, args.compare_input)
        baseline_report = json.loads(compare_path.read_text())
        compare_rows = _build_compare_rows(baseline_report, load_report)
        model["compare"] = {
            "baseline_input": str(compare_path),
            "tuned_input": str(input_path),
            "rows": compare_rows,
        }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(model, indent=2))

    md_lines = [
        "# Capacity Model (Measured Input)",
        "",
        f"- Input report: `{input_path}`",
        f"- Utilization factor: `{args.utilization}`",
        f"- Polls per task: `{args.polls_per_task}`",
        "",
        (
            "| Profile | Raw rps | Sustained rps | Monthly tasks | Monthly polls | "
            "p95 submit latency (ms) | Stream max | PEL max | Redis max MiB |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        md_lines.append(
            "| "
            f"{row['profile']} | {row['throughput_rps_raw']} | {row['throughput_rps_sustained']} "
            f"| {row['monthly_tasks']} | {row['monthly_polls']} | {row['p95_ms']} "
            f"| {row['stream_max']} | {row['pel_max']} | {row['redis_memory_max_mib']} |"
        )

    if compare_path is not None:
        md_lines.extend(
            [
                "",
                "## Baseline vs Tuned Comparison",
                "",
                f"- Baseline report: `{compare_path}`",
                f"- Tuned report: `{input_path}`",
                "",
                (
                    "| Profile | Throughput baseline | Throughput tuned | Delta | "
                    "p95 baseline (ms) | p95 tuned (ms) | Delta | Stream max delta | "
                    "PEL max delta | Redis max MiB delta |"
                ),
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in compare_rows:
            md_lines.append(
                "| "
                f"{row['profile']} | "
                f"{row['throughput_rps_baseline']} | {row['throughput_rps_tuned']} "
                f"| {row['throughput_rps_delta']} "
                f"| {row['p95_ms_baseline']} | {row['p95_ms_tuned']} "
                f"| {row['p95_ms_delta']} "
                f"| {row['stream_max_delta']} | {row['pel_max_delta']} "
                f"| {row['redis_memory_max_mib_delta']} |"
            )

    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text("\n".join(md_lines) + "\n")

    print(json.dumps(model, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
