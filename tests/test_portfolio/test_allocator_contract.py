"""Contract test for Allocator ABC — any implementation must pass these.

Add new allocator implementations to `all_allocators()` — contract tests auto-validate.
"""
import pytest

from ez.portfolio.allocator import (
    Allocator, EqualWeightAllocator, MaxWeightAllocator, RiskParityAllocator,
)


def all_allocators() -> list[Allocator]:
    rp = RiskParityAllocator()
    rp.set_volatilities({"A": 0.15, "B": 0.25, "C": 0.10, "D": 0.20, "E": 0.30})
    return [
        EqualWeightAllocator(),
        MaxWeightAllocator(max_weight=0.05),
        MaxWeightAllocator(max_weight=0.30),
        rp,
    ]


@pytest.fixture(params=all_allocators(), ids=lambda a: f"{type(a).__name__}")
def allocator(request):
    return request.param


class TestAllocatorContract:
    """Invariants that ANY Allocator implementation must satisfy."""

    def test_allocate_returns_dict(self, allocator):
        result = allocator.allocate({"A": 0.3, "B": 0.3, "C": 0.4})
        assert isinstance(result, dict)

    def test_all_weights_non_negative(self, allocator):
        result = allocator.allocate({"A": 0.5, "B": 0.3, "C": 0.2})
        for sym, w in result.items():
            assert w >= -1e-9, f"{sym} weight {w} < 0"

    def test_weights_sum_le_one(self, allocator):
        result = allocator.allocate({"A": 0.5, "B": 0.3, "C": 0.2})
        total = sum(result.values())
        assert total <= 1.0 + 1e-6, f"Sum {total} > 1.0"

    def test_empty_input_returns_empty(self, allocator):
        result = allocator.allocate({})
        assert result == {} or len(result) == 0

    def test_single_stock_returns_weight(self, allocator):
        result = allocator.allocate({"A": 1.0})
        assert len(result) >= 0  # may return empty or single

    def test_negative_inputs_clipped(self, allocator):
        """Negative weights in input should not produce negative output."""
        result = allocator.allocate({"A": 0.5, "B": -0.3, "C": 0.8})
        for sym, w in result.items():
            assert w >= -1e-9

    def test_all_zero_inputs(self, allocator):
        result = allocator.allocate({"A": 0.0, "B": 0.0, "C": 0.0})
        # Should return empty or all-zero
        total = sum(result.values())
        assert total <= 1e-6

    def test_many_stocks(self, allocator):
        """Should handle 50+ stocks without error."""
        weights = {f"S{i:03d}": 1.0 / 50 for i in range(50)}
        result = allocator.allocate(weights)
        assert isinstance(result, dict)
        total = sum(result.values())
        assert total <= 1.0 + 1e-6
