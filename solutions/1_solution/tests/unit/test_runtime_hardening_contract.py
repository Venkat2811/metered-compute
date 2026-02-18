from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_compose_sets_restart_policy_for_long_running_services() -> None:
    payload = yaml.safe_load((_project_root() / "compose.yaml").read_text(encoding="utf-8"))
    services = payload.get("services", {})

    for name in ("api", "worker", "reaper", "redis", "postgres", "hydra", "prometheus", "grafana"):
        assert services[name]["restart"] == "unless-stopped"


def test_api_dockerfile_runs_as_non_root() -> None:
    dockerfile_text = (_project_root() / "docker/api/Dockerfile").read_text(encoding="utf-8")

    assert "useradd --uid 10001 --gid app --create-home app" in dockerfile_text
    assert "USER app" in dockerfile_text


def test_grafana_assets_use_solution1_naming() -> None:
    dashboard = _project_root() / "monitoring/grafana/dashboards/solution1-overview.json"
    dashboard_provisioning = (
        _project_root() / "monitoring/grafana/provisioning/dashboards/dashboard.yml"
    )
    provisioning_text = dashboard_provisioning.read_text(encoding="utf-8")

    assert dashboard.exists()
    assert "folder: Solution1" in provisioning_text
    assert "Solution0" not in provisioning_text


def test_readme_compat_endpoint_uses_admin_credits_path() -> None:
    readme = (_project_root() / "README.md").read_text(encoding="utf-8")

    assert "(`/task`, `/poll`, `/admin/credits`, `/hit`)." in readme
