"""Ordered feature/metadata types and causal-window constants for Gold v1."""

import pyarrow as pa

DAY_NS = 86_400_000_000_000
LABEL_DELAY_DAYS = 7
WINDOW_DAYS = (1, 7)
METADATA_DTYPES = {
    "TRANSACTION_ID": "int64",
    "TX_DATETIME": "datetime64[ns]",
    "CUSTOMER_ID": "int64",
    "TERMINAL_ID": "int64",
    "LABEL_AVAILABLE_AT": "datetime64[ns]",
    "TX_FRAUD": "int8",
}
FEATURE_DTYPES = {"TX_AMOUNT": "float64", "TX_HOUR": "int8", "TX_WEEKDAY": "int8"}
for days in WINDOW_DAYS:
    FEATURE_DTYPES.update(
        {
            f"CUSTOMER_TX_COUNT_{days}D": "int64",
            f"CUSTOMER_AVG_AMOUNT_{days}D": "float64",
            f"CUSTOMER_AMOUNT_RATIO_{days}D": "float64",
            f"CUSTOMER_AMOUNT_RATIO_VALID_{days}D": "int8",
        }
    )
for days in WINDOW_DAYS:
    FEATURE_DTYPES.update(
        {
            f"TERMINAL_TX_COUNT_{days}D": "int64",
            f"TERMINAL_KNOWN_LABEL_COUNT_{days}D": "int64",
            f"TERMINAL_KNOWN_FRAUD_COUNT_{days}D": "int64",
            f"TERMINAL_KNOWN_FRAUD_RATE_{days}D": "float64",
        }
    )
DTYPES = {**METADATA_DTYPES, **FEATURE_DTYPES}
FEATURE_COLUMNS = list(FEATURE_DTYPES)


FEATURE_ARROW_SCHEMA = pa.schema(
    [
        (name, pa.timestamp("ns") if dtype == "datetime64[ns]" else pa.type_for_alias(dtype))
        for name, dtype in DTYPES.items()
    ]
)
