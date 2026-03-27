from __future__ import annotations

from scripts import capacity_model


def test_build_profile_rows_projects_monthly_volume() -> None:
    rows = capacity_model._build_profile_rows(
        {
            "profiles": [
                {
                    "profile": "steady",
                    "throughput_rps": 12.5,
                    "latency_ms": {"p95": 210.0},
                    "accepted": 100,
                }
            ]
        },
        utilization=0.5,
        polls_per_task=2.0,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["profile"] == "steady"
    assert row["throughput_rps_raw"] == 12.5
    assert row["throughput_rps_sustained"] == 6.25
    assert row["monthly_tasks"] == 16_200_000.0
    assert row["monthly_polls"] == 32_400_000.0
    assert row["p95_ms"] == 210.0


def test_bytes_to_mib_rounding() -> None:
    assert capacity_model._bytes_to_mib(1_048_576) == 1.0
