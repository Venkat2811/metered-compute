"""Unit tests for Restate workflow logic."""

from __future__ import annotations

from solution5.workflows import _compute


class TestCompute:
    def test_compute_basic(self) -> None:
        result = _compute(3, 4)
        assert result == {"sum": 7, "product": 12}

    def test_compute_zeros(self) -> None:
        result = _compute(0, 0)
        assert result == {"sum": 0, "product": 0}

    def test_compute_negative(self) -> None:
        result = _compute(-5, 3)
        assert result == {"sum": -2, "product": -15}

    def test_compute_large(self) -> None:
        result = _compute(1_000_000, 2_000_000)
        assert result == {"sum": 3_000_000, "product": 2_000_000_000_000}
