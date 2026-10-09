"""Physical transaction representation and scalar validity shared across layers."""

import math

import pandas as pd
import pyarrow as pa

INTEGER_COLUMNS = (
    "TRANSACTION_ID",
    "CUSTOMER_ID",
    "TERMINAL_ID",
    "TX_TIME_SECONDS",
    "TX_TIME_DAYS",
    "TX_FRAUD",
    "TX_FRAUD_SCENARIO",
)
EXPECTED_COLUMNS = {*INTEGER_COLUMNS, "TX_DATETIME", "TX_AMOUNT"}

DTYPES = {
    "TRANSACTION_ID": "int64",
    "TX_DATETIME": "datetime64[ns]",
    "CUSTOMER_ID": "int64",
    "TERMINAL_ID": "int64",
    "TX_AMOUNT": "float64",
    "TX_TIME_SECONDS": "int64",
    "TX_TIME_DAYS": "int64",
    "TX_FRAUD": "int8",
    "TX_FRAUD_SCENARIO": "int8",
}
ARROW_SCHEMA = pa.schema(
    [
        (name, pa.timestamp("ns") if name == "TX_DATETIME" else pa.type_for_alias(dtype))
        for name, dtype in DTYPES.items()
    ]
)
INFORMATIONAL = {"zero_amounts", "timestamp_order_decreases"}


def is_int64(value) -> bool:
    return (
        pd.notna(value)
        and math.isfinite(value)
        and value == int(value)
        and -(2**63) <= int(value) < 2**63
    )
