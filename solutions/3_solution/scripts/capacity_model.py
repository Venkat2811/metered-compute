#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SECONDS_PER_MONTH = 2_592_000


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate monthly capacity projections from Solution 3 load output."
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
        latency = profile.get("latency_ms")
        rows.append(
            {
                "profile": str(profile.get("profile", "unknown")),
                "accepted": int(profile.get("accepted", 0)),
                "throughput_rps_raw": round(throughput_rps, 4),
                "throughput_rps_sustained": round(sustained_rps, 4),
                "monthly_tasks": round(monthly_tasks, 2),
                "monthly_polls": round(monthly_polls, 2),
                "p95_ms": (float(latency.get("p95", 0.0)) if isinstance(latency, dict) else 0.0),
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

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(model, indent=2) + "\n")

    md_lines = [
        "# Capacity Model (Measured Input)",
        "",
        f"- Input report: `{input_path}`",
        f"- Utilization factor: `{args.utilization}`",
        f"- Polls per task: `{args.polls_per_task}`",
        "",
        "| Profile | Raw rps | Sustained rps | Monthly tasks | Monthly polls | p95 (ms) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        md_lines.append(
            "| "
            f"{row['profile']} | {row['throughput_rps_raw']} | {row['throughput_rps_sustained']} "
            f"| {row['monthly_tasks']} | {row['monthly_polls']} | {row['p95_ms']} |"
        )

    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text("\n".join(md_lines) + "\n")

    print(json.dumps(model, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
