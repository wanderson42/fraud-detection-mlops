import numpy as np
import pandas as pd
import pytest

from fraud_detection_mlops.modeling.metrics import MetricError, evaluate_ranking


def metadata(customers, days=None):
    return pd.DataFrame(
        {
            "TRANSACTION_ID": range(len(customers)),
            "CUSTOMER_ID": customers,
            "TX_DATETIME": pd.to_datetime(days or ["2018-05-06"] * len(customers)),
        }
    )


def test_known_transaction_ranking_and_customer_aggregation():
    # The customer's fraud can occur on a lower-scored transaction.
    m = evaluate_ranking([0, 1, 1, 0], [0.9, 0.1, 0.8, 0.2], metadata([1, 1, 2, 3]))
    assert m["average_precision"] == pytest.approx((1 / 2 + 2 / 4) / 2)
    assert m["roc_auc"] == 0.25
    assert m["daily_customer_precision_at_100"] == 2 / 3
    assert m["daily_customer_metrics"][0]["alerts"] == 3


def test_top100_ties_use_customer_id_and_macro_average_days():
    frame = metadata(list(range(101)) + [101], ["2018-05-06"] * 101 + ["2018-05-07"])
    labels = [0] * 100 + [1, 1]
    measured = evaluate_ranking(labels, np.ones(102) * 0.5, frame)
    assert measured["average_precision"] == 2 / 102
    assert measured["roc_auc"] == 0.5
    assert measured["daily_customer_metrics"][0]["alerts"] == 100
    # Customer 100 is excluded by the deterministic tie-break.
    assert measured["daily_customer_metrics"][0]["fraudulent_customers_in_alerts"] == 0
    assert measured["daily_customer_metrics"][1]["precision_at_100"] == 1
    assert measured["daily_customer_precision_at_100"] == 0.5


@pytest.mark.parametrize("label, ap", [(0, 0.0), (1, 1.0)])
def test_one_class_has_explicit_roc_auc_null(label, ap):
    measured = evaluate_ranking([label, label], [0.1, 0.9], metadata([1, 2]))
    assert measured["roc_auc"] is None
    assert measured["average_precision"] == ap


@pytest.mark.parametrize("scores", [[np.nan, 0.5], [np.inf, 0.5], [-0.1, 0.5], [0.5, 1.1]])
def test_invalid_scores_are_rejected(scores):
    with pytest.raises(MetricError):
        evaluate_ranking([0, 1], scores, metadata([1, 2]))


def test_duplicate_ids_and_unaligned_arrays_are_rejected():
    meta = metadata([1, 2])
    meta["TRANSACTION_ID"] = [1, 1]
    with pytest.raises(MetricError, match="Duplicate"):
        evaluate_ranking([0, 1], [0.1, 0.9], meta)
    with pytest.raises(MetricError):
        evaluate_ranking([0], [0.1, 0.9], metadata([1, 2]))


def test_metric_results_ignore_dataframe_index_alignment():
    meta = metadata([1, 2])
    original = evaluate_ranking([0, 1], [0.1, 0.9], meta)
    meta.index = [50, 40]
    assert evaluate_ranking(pd.Series([0, 1], index=[4, 3]), [0.1, 0.9], meta) == original
