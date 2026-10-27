"""Shared numeric indicator definitions, independent of price feed or chart range."""

import math
from statistics import mean

WILDER_METHOD = "wilder-sma-seed-v1"


def wilder_smoothing(values, period=14):
    """SMA seed after N consecutive finite values; gaps reset the warmup."""
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise ValueError("period must be a positive integer")
    seed, previous, output = [], None, []
    for value in values:
        try:
            number = float(value) if value is not None and not isinstance(value, bool) else None
        except (TypeError, ValueError):
            number = None
        if number is None or not math.isfinite(number):
            seed, previous = [], None
            output.append(None)
            continue
        if previous is None:
            seed.append(number)
            if len(seed) == period:
                previous = mean(seed)
        else:
            previous += (number - previous) / period
        output.append(previous)
    return output
