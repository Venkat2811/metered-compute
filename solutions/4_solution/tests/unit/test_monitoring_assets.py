from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_prometheus_scrapes_api_and_compute_targets() -> None:
    prometheus_config = (ROOT / "monitoring" / "prometheus" / "prometheus.yml").read_text()

    assert 'job_name: "solution4-api"' in prometheus_config
    assert 'job_name: "solution4-compute"' in prometheus_config
    assert "api:8000" in prometheus_config
    assert "compute:8001" in prometheus_config


def test_grafana_provisioning_and_dashboard_exist() -> None:
    datasource_path = ROOT / "monitoring" / "grafana" / "provisioning" / "datasources" / "datasource.yml"
    dashboard_provider_path = ROOT / "monitoring" / "grafana" / "provisioning" / "dashboards" / "dashboard.yml"
    dashboard_path = ROOT / "monitoring" / "grafana" / "dashboards" / "solution4-overview.json"

    assert datasource_path.exists()
    assert dashboard_provider_path.exists()
    assert dashboard_path.exists()

    dashboard = json.loads(dashboard_path.read_text())
    titles = {panel["title"] for panel in dashboard["panels"]}
    assert "HTTP Request Rate" in titles
    assert "Compute Request Rate" in titles
    assert "Credit Lifecycle" in titles
