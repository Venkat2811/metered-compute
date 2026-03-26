from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.mark.e2e
def test_python_demo_script(project_root: Path) -> None:
    completed = subprocess.run(
        ["python", "utils/demo.py"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "health:" in completed.stdout
    assert "ready:" in completed.stdout


@pytest.mark.e2e
def test_shell_demo_script(project_root: Path) -> None:
    completed = subprocess.run(
        ["bash", "utils/demo.sh"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "health:" in completed.stdout
    assert "ready:" in completed.stdout
