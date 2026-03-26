from __future__ import annotations

from pathlib import Path

import pytest

from solution3.core.settings import load_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    load_settings.cache_clear()


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]
