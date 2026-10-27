"""Known arithmetic seed and missing-data restart, rather than an EWMA approximation."""

import pytest

from tradingagents.web.domain.technical_methods import wilder_smoothing


def test_wilder_uses_arithmetic_seed_before_recursive_smoothing():
    assert wilder_smoothing([1, 2, 9, 12], 3) == [None, None, 4, pytest.approx(20 / 3)]


def test_missing_values_restart_warmup_instead_of_carrying_old_state():
    assert wilder_smoothing([1, 2, 9, float("nan"), 4, 5, 6], 3) == [None, None, 4, None, None, None, 5]
    assert wilder_smoothing([None, 2, 2, 2], 3) == [None, None, None, 2]
