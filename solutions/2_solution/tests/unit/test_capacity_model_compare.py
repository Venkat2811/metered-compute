from __future__ import annotations

from scripts import capacity_model


def test_build_compare_rows_calculates_delta_fields() -> None:
    baseline = {
        "profiles": [
            {
                "profile": "high",
                "throughput_rps": 10.0,
                "latency_ms": {"p95": 200.0},
                "stream_observability": {
                    "stream_length": {"max": 1000},
                    "pel_pending": {"max": 40},
                    "redis_used_memory_bytes": {"max": 2_000_000},
                },
            }
        ]
    }
    tuned = {
        "profiles": [
            {
                "profile": "high",
                "throughput_rps": 12.0,
                "latency_ms": {"p95": 180.0},
                "stream_observability": {
                    "stream_length": {"max": 700},
                    "pel_pending": {"max": 25},
                    "redis_used_memory_bytes": {"max": 1_500_000},
                },
            }
        ]
    }

    rows = capacity_model._build_compare_rows(baseline, tuned)

    assert len(rows) == 1
    row = rows[0]
    assert row["profile"] == "high"
    assert row["throughput_rps_delta"] == 2.0
    assert row["p95_ms_delta"] == -20.0
    assert row["stream_max_delta"] == -300
    assert row["pel_max_delta"] == -15


def test_bytes_to_mib_rounding() -> None:
    assert capacity_model._bytes_to_mib(1_048_576) == 1.0
