from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    solution: str
    timestamp: str


class ReadyResponse(BaseModel):
    ready: bool
    dependencies: list[str] = Field(default_factory=list)
    checked_at: str

    @classmethod
    def with_defaults(cls, *, ready: bool, deps: list[str] | None = None) -> ReadyResponse:
        return cls(ready=ready, dependencies=deps or [], checked_at=datetime.now(UTC).isoformat())
