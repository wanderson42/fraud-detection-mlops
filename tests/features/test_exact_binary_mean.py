"""Numerical regressions at observed HGB cuts and the complete binary64 range."""

from fractions import Fraction
from itertools import permutations
import math
import sqlite3
import sys

import numpy as np
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier

from fraud_detection_mlops.features.exact_binary_mean import ExactBinaryMean, exact_binary_mean


@pytest.mark.parametrize(
    "values",
    [
        [],
        [0.0],
        [33.77] * 3,
        [79.545] * 3,
        [0.1, 0.2, 0.3],
        [sys.float_info.max] * 2,
        [math.ulp(0.0), 0.0, math.ulp(0.0)],
        [sys.float_info.max, math.ulp(0.0), 1.0],
    ],
)
def test_single_rounding_matches_independent_rational_specification(values):
    expected = (
        float(sum((Fraction.from_float(v) for v in values), Fraction()) / len(values))
        if values
        else 0.0
    )
    assert exact_binary_mean(values) == expected
    assert math.isfinite(expected)


def test_order_and_sqlite_insertion_order_cannot_change_result():
    values = [1e20, 33.77, 0.1, math.ulp(0.0)]
    expected = exact_binary_mean(values)
    for ordered in permutations(values):
        with sqlite3.connect(":memory:") as connection:
            connection.create_aggregate("exact_binary_mean", 1, ExactBinaryMean)
            connection.execute("CREATE TABLE values_table (amount REAL)")
            connection.executemany("INSERT INTO values_table VALUES (?)", [(v,) for v in ordered])
            actual = connection.execute(
                "SELECT exact_binary_mean(amount) FROM values_table"
            ).fetchone()[0]
        assert actual.hex() == expected.hex() == exact_binary_mean(ordered).hex()


@pytest.mark.parametrize("value", [-1.0, float("inf"), float("nan")])
def test_invalid_amounts_are_rejected(value):
    with pytest.raises(ValueError, match="finite and nonnegative"):
        exact_binary_mean([value])


@pytest.mark.parametrize("boundary", [33.77, 79.545])
def test_one_ulp_at_a_histogram_tree_cut_changes_score_but_exact_mean_is_shared(boundary):
    # A generated classifier only: no refit of the frozen project model.
    below = np.nextafter(boundary, -np.inf)
    above = np.nextafter(boundary, np.inf)
    training = np.array([[boundary - 0.01]] * 20 + [[boundary + 0.01]] * 20)
    model = HistGradientBoostingClassifier(
        max_iter=1, min_samples_leaf=1, max_leaf_nodes=2, early_stopping=False, learning_rate=1.0
    ).fit(training, [0] * 20 + [1] * 20)
    model._predictors[0][0].nodes[0]["num_threshold"] = below
    assert (
        model.predict_proba([[below], [boundary]])[0, 1]
        != model.predict_proba([[below], [boundary]])[1, 1]
    )
    canonical = exact_binary_mean([boundary] * 3)
    assert canonical == boundary
    assert below < canonical < above
    with sqlite3.connect(":memory:") as connection:
        connection.create_aggregate("exact_binary_mean", 1, ExactBinaryMean)
        connection.execute("CREATE TABLE history(amount REAL)")
        connection.executemany("INSERT INTO history VALUES (?)", [(boundary,)] * 3)
        online = connection.execute("SELECT exact_binary_mean(amount) FROM history").fetchone()[0]
    assert np.array_equal(model.predict_proba([[online]]), model.predict_proba([[canonical]]))
