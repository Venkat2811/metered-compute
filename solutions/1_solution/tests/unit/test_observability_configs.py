from __future__ import annotations

from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_otel_and_tempo_config_artifacts_present() -> None:
    project_root = _project_root()
    otel_config = project_root / "monitoring/otel/otel-collector.yaml"
    tempo_config = project_root / "monitoring/tempo/tempo.yaml"

    otel_text = otel_config.read_text(encoding="utf-8")
    tempo_text = tempo_config.read_text(encoding="utf-8")

    assert "receivers:" in otel_text
    assert "otlp:" in otel_text
    assert "service:" in otel_text

    assert "distributor:" in tempo_text
    assert "storage:" in tempo_text
    assert "compactor:" in tempo_text


def test_compose_declares_tracing_profile_services() -> None:
    compose_text = (_project_root() / "compose.yaml").read_text(encoding="utf-8")

    assert "tempo:" in compose_text
    assert "otel-collector:" in compose_text
    assert "webhook-dispatcher:" in compose_text
    assert 'profiles: ["tracing"]' in compose_text
    assert "OTEL_ENABLED: ${OTEL_ENABLED:-false}" in compose_text


def test_prometheus_scrapes_webhook_dispatcher() -> None:
    prometheus_text = (_project_root() / "monitoring/prometheus/prometheus.yml").read_text(
        encoding="utf-8"
    )
    alerts_text = (_project_root() / "monitoring/prometheus/alerts.yml").read_text(encoding="utf-8")

    assert "job_name: webhook-dispatcher" in prometheus_text
    assert 'targets: ["webhook-dispatcher:9300"]' in prometheus_text
    assert "Solution1WebhookDispatcherUnavailable" in alerts_text
    assert "WebhookDeadLetterGrowing" in alerts_text
