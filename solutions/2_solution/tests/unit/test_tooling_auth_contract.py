from __future__ import annotations

import re
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_tooling_does_not_use_api_keys_as_bearer_tokens() -> None:
    bearer_from_key = re.compile(
        r'Authorization"\s*:\s*f"Bearer \{[^}]*(_key|api_key)[^}]*\}"',
        re.IGNORECASE,
    )
    for relative_path in ("scripts/run_scenarios.py", "scripts/load_harness.py"):
        script_text = (_project_root() / relative_path).read_text(encoding="utf-8")
        assert bearer_from_key.search(script_text) is None, (
            f"{relative_path} contains bearer header built from key material"
        )


def test_tooling_uses_oauth_exchange_before_authenticated_calls() -> None:
    for relative_path in ("scripts/run_scenarios.py", "scripts/load_harness.py"):
        script_text = (_project_root() / relative_path).read_text(encoding="utf-8")
        assert "V1_OAUTH_TOKEN_PATH" in script_text
        assert "_oauth_token(" in script_text
