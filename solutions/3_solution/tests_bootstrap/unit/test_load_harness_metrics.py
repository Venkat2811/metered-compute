from __future__ import annotations

from scripts import load_harness


def test_percentile_uses_sorted_position() -> None:
    assert load_harness._percentile([10.0, 50.0, 20.0, 40.0], 0.95) == 50.0


def test_build_summary_reports_status_counts_and_latency_percentiles() -> None:
    results = [
        load_harness.RequestResult(
            status_code=201,
            latency_ms=120.0,
            terminal_status="COMPLETED",
            task_id="task-1",
        ),
        load_harness.RequestResult(
            status_code=201,
            latency_ms=180.0,
            terminal_status="COMPLETED",
            task_id="task-2",
        ),
        load_harness.RequestResult(
            status_code=429,
            latency_ms=30.0,
            terminal_status=None,
            task_id=None,
        ),
    ]

    summary = load_harness.build_summary(
        profile_name="burst",
        results=results,
        total_duration_seconds=4.0,
    )

    assert summary["profile"] == "burst"
    assert summary["total_requests"] == 3
    assert summary["accepted"] == 2
    assert summary["rejected"] == 1
    assert summary["terminal_status_counts"] == {"COMPLETED": 2}
    assert summary["throughput_rps"] == 0.75
    assert summary["latency_ms"]["max"] == 180.0
    assert summary["latency_ms"]["p95"] == 180.0
