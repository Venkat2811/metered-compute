from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.mark.fault
def test_wait_ready_times_out_for_unreachable_endpoint(project_root: Path) -> None:
    completed = subprocess.run(
        ["bash", "scripts/wait_ready.sh", "http://127.0.0.1:9", "1", "1"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "timed out waiting" in completed.stderr
