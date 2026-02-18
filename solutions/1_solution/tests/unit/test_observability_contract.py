from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]

from solution1.observability import metrics as metrics_module

_ROOT = Path(__file__).resolve().parents[2]
_ALERTS_PATH = _ROOT / "monitoring" / "prometheus" / "alerts.yml"
_PROMETHEUS_PATH = _ROOT / "monitoring" / "prometheus" / "prometheus.yml"


def test_metrics_module_exports_rfc_series() -> None:
    required_metric_symbols = {
        "STREAM_CONSUMER_LAG",
        "STREAM_PENDING_ENTRIES",
        "JWT_VALIDATION_DURATION_SECONDS",
        "SNAPSHOT_FLUSH_DURATION_SECONDS",
        "TOKEN_ISSUANCE_TOTAL",
        "TOKEN_REVOCATIONS_TOTAL",
        "REVOCATION_PG_FALLBACK_TOTAL",
        "REVOCATION_CHECK_DURATION_SECONDS",
        "PEL_RECOVERY_TOTAL",
        "CREDIT_DRIFT_ABSOLUTE",
        "REAPER_RETENTION_DELETES_TOTAL",
        "SNAPSHOT_LAST_SUCCESS_UNIXTIME",
    }
    for symbol in required_metric_symbols:
        assert hasattr(metrics_module, symbol), symbol


def test_alert_rules_include_rfc_contract_alerts() -> None:
    payload = yaml.safe_load(_ALERTS_PATH.read_text(encoding="utf-8"))
    groups = payload.get("groups", [])
    rules = [
        rule
        for group in groups
        for rule in group.get("rules", [])
        if isinstance(rule, dict) and "alert" in rule
    ]
    alert_to_expr = {str(rule["alert"]): str(rule.get("expr", "")) for rule in rules}

    assert "StreamConsumerLagWarning" in alert_to_expr
    assert "StreamConsumerLagCritical" in alert_to_expr
    assert "DriftThreshold" in alert_to_expr
    assert "SnapshotStale" in alert_to_expr
    assert "PELGrowing" in alert_to_expr

    assert "stream_consumer_lag" in alert_to_expr["StreamConsumerLagWarning"]
    assert "stream_consumer_lag" in alert_to_expr["StreamConsumerLagCritical"]
    assert "credit_drift_absolute" in alert_to_expr["DriftThreshold"]
    assert "snapshot_last_success_unixtime" in alert_to_expr["SnapshotStale"]
    assert "stream_pending_entries" in alert_to_expr["PELGrowing"]


def test_prometheus_scrapes_reaper_metrics_target() -> None:
    payload = yaml.safe_load(_PROMETHEUS_PATH.read_text(encoding="utf-8"))
    scrape_configs = payload.get("scrape_configs", [])
    jobs = {str(cfg.get("job_name")): cfg for cfg in scrape_configs if isinstance(cfg, dict)}

    assert "api" in jobs
    assert "worker" in jobs
    assert "reaper" in jobs

    reaper_targets = jobs["reaper"]["static_configs"][0]["targets"]
    assert "reaper:9201" in reaper_targets
