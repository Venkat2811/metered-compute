from __future__ import annotations

import pytest

from scripts import run_scenarios


def test_resolve_selected_scenarios_preserves_requested_order() -> None:
    registry = {
        "health_ready": object(),
        "submit_poll": object(),
        "cancel_pending": object(),
    }

    selected = run_scenarios._resolve_selected_scenarios(
        ["submit_poll", "health_ready"],
        registry=registry,
    )

    assert selected == ["submit_poll", "health_ready"]


def test_resolve_selected_scenarios_rejects_unknown_names() -> None:
    with pytest.raises(ValueError, match="unknown scenarios: missing"):
        run_scenarios._resolve_selected_scenarios(
            ["missing"],
            registry={"health_ready": object()},
        )


def test_build_report_counts_passed_and_failed_results() -> None:
    report = run_scenarios.build_report(
        [
            run_scenarios.ScenarioResult(
                name="health_ready",
                passed=True,
                duration_seconds=0.12,
                details={"ready": True},
            ),
            run_scenarios.ScenarioResult(
                name="cancel_pending",
                passed=False,
                duration_seconds=0.55,
                details={"error": "timed out"},
            ),
        ],
        base_url="http://localhost:8000",
    )

    assert report["base_url"] == "http://localhost:8000"
    assert report["total"] == 2
    assert report["passed"] == 1
    assert report["failed"] == 1
    assert report["scenarios"][0]["name"] == "health_ready"
    assert report["scenarios"][1]["details"] == {"error": "timed out"}
