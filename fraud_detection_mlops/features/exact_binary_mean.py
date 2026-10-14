"""Order-independent mean of observed binary64 values, rounded once to binary64."""

from fractions import Fraction
import math

NUMERICAL_VERSION = "exact_binary64_mean_v1"
SCALE_BITS = 1074


class ExactBinaryMean:
    """SQLite aggregate and exact accumulator; no decimal quantization or float sum."""

    def __init__(self):
        self.total = 0
        self.count = 0

    def step(self, value):
        if value is None:
            return
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("Amounts must be finite and nonnegative")
        numerator, denominator = value.as_integer_ratio()
        self.total += numerator << (SCALE_BITS - (denominator.bit_length() - 1))
        self.count += 1

    def finalize(self):
        if not self.count:
            return 0.0
        return float(Fraction(self.total, self.count << SCALE_BITS))


def exact_binary_mean(values) -> float:
    accumulator = ExactBinaryMean()
    for value in values or ():
        accumulator.step(value)
    return accumulator.finalize()
