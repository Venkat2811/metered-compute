from __future__ import annotations

from scripts import load_harness


def test_parse_xpending_summary_count_uses_first_line() -> None:
    output = "12\n1699876543210-0\n1699876543321-0\nconsumer-a\n12\n"

    assert load_harness._parse_xpending_summary_count(output) == 12


def test_parse_xpending_summary_handles_empty() -> None:
    assert load_harness._parse_xpending_summary_count("") == 0


def test_parse_info_memory_used_bytes() -> None:
    info = "# Memory\nused_memory:1048576\nused_memory_human:1.00M\n"

    assert load_harness._parse_info_memory_used_bytes(info) == 1_048_576


def test_parse_info_memory_used_bytes_defaults_zero_when_missing() -> None:
    assert load_harness._parse_info_memory_used_bytes("# Memory\nfoo:bar\n") == 0


def test_summarize_series_reports_growth_and_percentiles() -> None:
    summary = load_harness._summarize_series([100, 120, 110, 200])

    assert summary["start"] == 100
    assert summary["end"] == 200
    assert summary["max"] == 200
    assert summary["growth"] == 100
    assert summary["p95"] >= 120


def test_summarize_stream_samples_includes_stream_pel_and_memory() -> None:
    samples = [
        load_harness.StreamSample(
            epoch_seconds=1.0,
            stream_length=10,
            pel_pending=0,
            redis_used_memory_bytes=1_000,
        ),
        load_harness.StreamSample(
            epoch_seconds=2.0,
            stream_length=50,
            pel_pending=7,
            redis_used_memory_bytes=1_500,
        ),
    ]

    summary = load_harness._summarize_stream_samples(samples)

    assert summary["sample_count"] == 2
    assert summary["stream_length"]["max"] == 50
    assert summary["pel_pending"]["max"] == 7
    assert summary["redis_used_memory_bytes"]["growth"] == 500
