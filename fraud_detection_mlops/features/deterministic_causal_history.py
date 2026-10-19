"""Independent DuckDB window membership with a versioned exact binary64 mean."""

import duckdb
import pandas as pd

from fraud_detection_mlops.features.causal_history import feature_query
from fraud_detection_mlops.features.exact_binary_mean import exact_binary_mean
from fraud_detection_mlops.features.feature_schema import DTYPES, WINDOW_DAYS


def deterministic_features(transactions: pd.DataFrame) -> pd.DataFrame:
    """Compute every predictor independently of the replay's SQLite state/requests."""
    query = feature_query()
    for days in WINDOW_DAYS:
        old = f"coalesce(avg(TX_AMOUNT) OVER c_{days}d, 0.0)"
        new = f"exact_binary_mean(list(TX_AMOUNT) OVER c_{days}d)"
        if query.count(old) != 1:
            raise ValueError("Reviewed Gold window implementation changed")
        query = query.replace(old, new)
    with duckdb.connect(config={"threads": 4, "memory_limit": "2GB"}) as connection:
        connection.create_function(
            "exact_binary_mean",
            exact_binary_mean,
            [duckdb.list_type(duckdb.sqltypes.DOUBLE)],
            duckdb.sqltypes.DOUBLE,
            null_handling="special",
        )
        connection.register("transactions", transactions.reset_index(drop=True).copy())
        frame = connection.execute(query).fetchdf().astype(DTYPES)
    # Both serving and offline divide the rounded mean using binary64 division.
    for days in WINDOW_DAYS:
        averages = frame[f"CUSTOMER_AVG_AMOUNT_{days}D"]
        frame[f"CUSTOMER_AMOUNT_RATIO_{days}D"] = [
            float(amount) / float(mean) if mean > 0 else 0.0
            for amount, mean in zip(frame.TX_AMOUNT, averages, strict=True)
        ]
    return frame.sort_values(["TX_DATETIME", "TRANSACTION_ID"]).reset_index(drop=True)
