"""Conditional uniform-queue reference, not uncertainty about future model quality."""

from datetime import date
import math
from numbers import Integral

import numpy as np
import pandas as pd
from scipy.special import logsumexp
from scipy.stats import hypergeom


def uniform_queue_reference(daily: pd.DataFrame, *, alert_budget: int = 100) -> dict:
    """Compare observed captures with fresh uniform customer draws on each fixed day.

    Input uses columns from ``paired_comparison.daily_metrics``. Labels and
    populations are conditioned on, not resampled or assumed independent.
    Independence refers only to the hypothetical policy's draws between days.
    """
    fields = {
        "date",
        "customers",
        "alerts",
        "fraudulent_customers",
        "fraudulent_customers_in_alerts",
    }
    if (
        not isinstance(alert_budget, Integral)
        or isinstance(alert_budget, (bool, np.bool_))
        or not 1 <= alert_budget <= 100
        or not isinstance(daily, pd.DataFrame)
        or daily.empty
        or not fields.issubset(daily.columns)
    ):
        raise ValueError("Expected nonempty daily counts and an integer alert budget in [1, 100]")
    rows = daily[sorted(fields)].to_dict("records")
    seen = set()
    log_mass = np.array([0.0])
    offset = observed = total_alerts = 0
    expected = 0.0
    for row in sorted(rows, key=lambda r: str(r["date"])):
        day = row["date"]
        try:
            valid_day = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
        except ValueError:
            valid_day = False
        if not valid_day or day in seen:
            raise ValueError("Expected unique canonical ISO dates")
        seen.add(day)
        counts = [row[k] for k in sorted(fields - {"date"})]
        if any(not isinstance(x, Integral) or isinstance(x, (bool, np.bool_)) for x in counts):
            raise ValueError("Customer and alert counts must be integers, not booleans or floats")
        population, positives, draws, found = (
            int(row[k])
            for k in (
                "customers",
                "fraudulent_customers",
                "alerts",
                "fraudulent_customers_in_alerts",
            )
        )
        lower, upper = max(0, draws - population + positives), min(draws, positives)
        if (
            population <= 0
            or not 0 <= positives <= population
            or draws != min(alert_budget, population)
            or not lower <= found <= upper
        ):
            raise ValueError("Daily counts, capture support or budget do not reconcile")
        component = hypergeom.logpmf(np.arange(lower, upper + 1), population, positives, draws)
        component -= logsumexp(component)
        combined = np.full(len(log_mass) + len(component) - 1, -np.inf)
        for shift, log_probability in enumerate(component):
            target = slice(shift, shift + len(log_mass))
            combined[target] = np.logaddexp(combined[target], log_mass + log_probability)
        log_mass = combined - logsumexp(combined)
        offset += lower
        observed += found
        total_alerts += draws
        expected += draws * positives / population

    log_tail = min(0.0, float(logsumexp(log_mass[observed - offset :])))
    tail = math.exp(log_tail)
    log_cdf = np.logaddexp.accumulate(log_mass)
    interval = [
        offset + min(int(np.searchsorted(log_cdf, math.log(q))), len(log_mass) - 1)
        for q in (0.025, 0.975)
    ]
    return {
        "version": "uniform_queue_reference_v1",
        "conditioning": "fixed_daily_populations_labels_and_budgets",
        "random_policy": "fresh_uniform_draws_without_replacement_independently_each_day",
        "days": len(rows),
        "total_alerts": total_alerts,
        "observed_captured_customer_days": observed,
        "expected_uniform_captured_customer_days": expected,
        "excess_captured_customer_days": observed - expected,
        "uniform_capture_reference_interval": {
            "mass": 0.95,
            "lower": interval[0],
            "upper": interval[1],
        },
        "tail_probability_at_least_observed": tail if tail else None,
        "log10_tail_probability_at_least_observed": log_tail / math.log(10),
        "linear_probability_underflow": tail == 0.0,
        "generalization_confidence_interval": None,
        "formal_superiority_claim": False,
        "production_promotion": False,
    }
