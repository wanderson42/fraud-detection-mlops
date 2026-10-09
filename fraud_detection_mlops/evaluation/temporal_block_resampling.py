"""Whole-day circular block sensitivity of frozen scores; not validated future coverage."""

import numpy as np
import pandas as pd

from fraud_detection_mlops.evaluation.temporal_reference_metrics import assessment_measurements

VERSION = "reference_uncertainty_analysis_v1"
BLOCK_LENGTHS = (2, 3, 4, 7)
REPLICATES = 2000
SEED = 42


def circular_day_indices(days: int, length: int, replicates: int, seed: int = SEED) -> np.ndarray:
    if days < 2 or not 1 <= length <= days or replicates < 1:
        raise ValueError(
            "Expected at least two days, a valid block length and positive replicates"
        )
    rng = np.random.default_rng(np.random.SeedSequence([seed, length]))
    starts = rng.integers(days, size=(replicates, (days + length - 1) // length))
    return ((starts[:, :, None] + np.arange(length)) % days).reshape(replicates, -1)[:, :days]


def day_multiplicities(indices: np.ndarray, days: int) -> np.ndarray:
    counts = np.zeros((len(indices), days), dtype="int64")
    np.add.at(counts, (np.arange(len(indices))[:, None], indices), 1)
    return counts


def pooled_ap_cache(
    frame: pd.DataFrame, days: list[str]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Count scores at positive-score thresholds; all tied scores share a threshold."""
    thresholds = np.unique(frame.loc[frame.TX_FRAUD == 1, "SCORE"])[::-1]
    stamps = frame.TX_DATETIME.dt.strftime("%Y-%m-%d")
    total, positive, sizes = [], [], []
    for day in days:
        group = frame.loc[stamps == day]
        scores = np.sort(group.SCORE.to_numpy())
        fraud_scores = np.sort(group.loc[group.TX_FRAUD == 1, "SCORE"].to_numpy())
        total.append(len(scores) - np.searchsorted(scores, thresholds, side="left"))
        positive.append(len(fraud_scores) - np.searchsorted(fraud_scores, thresholds, side="left"))
        sizes.append(len(group))
    return np.asarray(total, dtype=float), np.asarray(positive, dtype=float), np.asarray(sizes)


def weighted_pooled_ap(cache: tuple, weights: np.ndarray) -> np.ndarray:
    total, positive, sizes = cache
    if weights.ndim != 2 or weights.shape[1] != len(sizes):
        raise ValueError("Day weights must align with the cached calendar")
    if not np.isfinite(weights).all() or (weights < 0).any() or (weights.sum(axis=1) <= 0).any():
        raise ValueError("Day weights must be finite, nonnegative and nonempty")
    if positive.shape[1] == 0:
        return np.full(len(weights), np.nan)
    tp, observed = weights @ positive, weights @ total
    positives = tp[:, -1]
    negatives = weights @ sizes - positives
    precision = np.divide(tp, observed, out=np.zeros_like(tp), where=observed > 0)
    increments = np.diff(tp, axis=1, prepend=0)
    numerator = (precision * increments).sum(axis=1)
    result = np.divide(
        numerator, positives, out=np.full(len(weights), np.nan), where=positives > 0
    )
    result[negatives <= 0] = np.nan
    return result


def resampling_summary(values: np.ndarray, point: float | None) -> dict:
    valid = np.isfinite(values)
    count = int(valid.sum())
    # A percentile over a reduced set would silently condition on defined replicates.
    interval = (
        {"lower": float(np.quantile(values, 0.025)), "upper": float(np.quantile(values, 0.975))}
        if count == len(values)
        else None
    )
    return {
        "observed": point,
        "replicates": len(values),
        "defined_replicates": count,
        "undefined_replicates": len(values) - count,
        "replicate_mean": float(values[valid].mean()) if count else None,
        "replicate_standard_deviation": float(values[valid].std(ddof=1)) if count > 1 else None,
        "central_resampling_interval": interval,
        "interval_suppressed_reason": "undefined_replicates" if count != len(values) else None,
        "nominal_mass": 0.95,
        "coverage_validated": False,
    }


def analyze_block_sensitivity(predictions: pd.DataFrame, policy: dict) -> dict:
    metrics, daily = assessment_measurements(predictions, policy)
    days = [d["date"] for d in daily]
    if len(days) != 14:
        raise ValueError("This reviewed sensitivity grid requires the original 14-day assessment")
    cache = pooled_ap_cache(predictions, days)
    cached_point = weighted_pooled_ap(cache, np.ones((1, len(days))))[0]
    point = metrics["average_precision"]
    if (point is None and np.isfinite(cached_point)) or (
        point is not None and not np.isclose(point, cached_point, rtol=1e-12, atol=1e-12)
    ):
        raise ValueError("Cached pooled AP differs from the verified whole-window metric")
    precision = np.asarray([d["precision_at_100"] for d in daily])
    recall = np.asarray([d["customer_recall_at_100"] for d in daily], dtype=float)
    configurations = []
    for length in BLOCK_LENGTHS:
        indices = circular_day_indices(len(days), length, REPLICATES)
        weights = day_multiplicities(indices, len(days))
        ap = np.concatenate(
            [weighted_pooled_ap(cache, chunk) for chunk in np.array_split(weights, 8)]
        )
        sampled_recall = recall[indices]
        denominators = np.isfinite(sampled_recall).sum(axis=1)
        recall_means = np.divide(
            np.nansum(sampled_recall, axis=1),
            denominators,
            out=np.full(REPLICATES, np.nan),
            where=denominators > 0,
        )
        configurations.append(
            {
                "block_length_days": length,
                "blocks_drawn_per_replicate": (len(days) + length - 1) // length,
                "truncated_last_block": len(days) % length != 0,
                "metrics": {
                    "pooled_average_precision": resampling_summary(
                        ap, metrics["average_precision"]
                    ),
                    "mean_daily_customer_precision_at_100": resampling_summary(
                        precision[indices].mean(axis=1), metrics["daily_customer_precision_at_100"]
                    ),
                    "mean_daily_customer_recall_at_100": {
                        **resampling_summary(
                            recall_means, metrics["daily_mean_customer_recall_at_100"]
                        ),
                        "defined_day_counts_min": int(denominators.min()),
                        "defined_day_counts_max": int(denominators.max()),
                    },
                },
            }
        )
    return {
        "version": VERSION,
        "analysis_scope": "post_assessment_exploratory_resampling_sensitivity",
        "assessment_window": policy["windows"]["assessment"],
        "method": "whole_day_circular_block_bootstrap",
        "calendar_days": len(days),
        "rows": len(predictions),
        "replicates_per_configuration": REPLICATES,
        "seed": SEED,
        "rng": "numpy_PCG64_SeedSequence_seed_and_block_length",
        "block_lengths_days": list(BLOCK_LENGTHS),
        "selected_optimal_block_length": None,
        "resampling_end_to_start_wrap": True,
        "resampling_rebuilds_features_or_refits": False,
        "targets": {
            "pooled_average_precision": "transaction-weighted AP in a hypothetical 14-day local-regime window",
            "mean_daily_customer_precision_at_100": "equal-day-weighted mean daily queue precision in that local regime",
            "mean_daily_customer_recall_at_100": "equal-day-weighted mean customer recall over defined days",
        },
        "assumptions_not_established_by_diagnostics": [
            "local_stationarity_of_the_joint_daily_score_label_process",
            "dependence_relevant_to_each_statistic_is_adequately_represented_by_the_block_grid",
            "same_recurrent_entity_population_and_fixed_model_context",
            "circular_end_to_start_adjacency_is_an_acceptable_resampling_approximation",
        ],
        "configurations": configurations,
        "interpretation": {
            "nominal_mass_is_not_demonstrated_coverage": True,
            "bootstrap_replicates_do_not_add_observed_days": True,
            "whole_days_preserve_within_day_cross_entity_structure": True,
            "dependence_between_blocks_is_not_fully_preserved": True,
            "generalization_confidence_interval": None,
            "formal_temporal_hypothesis_test": False,
            "formal_superiority_claim": False,
            "production_promotion": False,
        },
    }
