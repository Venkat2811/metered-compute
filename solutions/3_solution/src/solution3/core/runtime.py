from __future__ import annotations

from dataclasses import dataclass

from solution3.core.settings import AppSettings


@dataclass(slots=True)
class RuntimeState:
    """Shared state objects for solution3 runtime services."""

    settings: AppSettings
    started: bool = False
