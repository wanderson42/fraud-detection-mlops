"""Causal event-time features; labels are usable only after the feedback delay."""

import duckdb

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


def feature_query() -> str:
    """An inclusive left boundary and exclusive right boundary at nanosecond precision."""
    histories, windows, predictors = (
        [],
        [],
        [
            "TX_AMOUNT",
            "CAST(date_part('hour', TX_DATETIME) AS TINYINT) AS TX_HOUR",
            "CAST(date_part('isodow', TX_DATETIME) AS TINYINT) AS TX_WEEKDAY",
        ],
    )
    for days in WINDOW_DAYS:
        customer = f"c_{days}d"
        windows.append(
            f"{customer} AS (PARTITION BY CUSTOMER_ID ORDER BY epoch_ns(TX_DATETIME) "
            f"RANGE BETWEEN {days * DAY_NS} PRECEDING AND 1 PRECEDING)"
        )
        histories += [
            f"count(*) OVER {customer} AS CUSTOMER_TX_COUNT_{days}D",
            f"coalesce(avg(TX_AMOUNT) OVER {customer}, 0.0) AS CUSTOMER_AVG_AMOUNT_{days}D",
        ]
        predictors += [
            f"CUSTOMER_TX_COUNT_{days}D",
            f"CUSTOMER_AVG_AMOUNT_{days}D",
            (
                f"coalesce(TX_AMOUNT / nullif(CUSTOMER_AVG_AMOUNT_{days}D, 0), 0.0) "
                f"AS CUSTOMER_AMOUNT_RATIO_{days}D"
            ),
            (
                f"CAST(CUSTOMER_AVG_AMOUNT_{days}D > 0 AS TINYINT) "
                f"AS CUSTOMER_AMOUNT_RATIO_VALID_{days}D"
            ),
        ]
    for days in WINDOW_DAYS:
        terminal, known = f"t_{days}d", f"k_{days}d"
        windows += [
            (
                f"{terminal} AS (PARTITION BY TERMINAL_ID ORDER BY epoch_ns(TX_DATETIME) "
                f"RANGE BETWEEN {days * DAY_NS} PRECEDING AND 1 PRECEDING)"
            ),
            (
                f"{known} AS (PARTITION BY TERMINAL_ID ORDER BY epoch_ns(TX_DATETIME) "
                f"RANGE BETWEEN {(days + LABEL_DELAY_DAYS) * DAY_NS} PRECEDING "
                f"AND {LABEL_DELAY_DAYS * DAY_NS + 1} PRECEDING)"
            ),
        ]
        histories += [
            f"count(*) OVER {terminal} AS TERMINAL_TX_COUNT_{days}D",
            f"count(*) OVER {known} AS TERMINAL_KNOWN_LABEL_COUNT_{days}D",
            (
                f"CAST(coalesce(sum(TX_FRAUD) OVER {known}, 0) AS BIGINT) "
                f"AS TERMINAL_KNOWN_FRAUD_COUNT_{days}D"
            ),
        ]
        predictors += [
            f"TERMINAL_TX_COUNT_{days}D",
            f"TERMINAL_KNOWN_LABEL_COUNT_{days}D",
            f"TERMINAL_KNOWN_FRAUD_COUNT_{days}D",
            (
                f"coalesce(TERMINAL_KNOWN_FRAUD_COUNT_{days}D::DOUBLE / "
                f"nullif(TERMINAL_KNOWN_LABEL_COUNT_{days}D, 0), 0.0) "
                f"AS TERMINAL_KNOWN_FRAUD_RATE_{days}D"
            ),
        ]
    metadata = ", ".join(METADATA_DTYPES)
    return f"""
        WITH histories AS (
            SELECT TRANSACTION_ID, TX_DATETIME, CUSTOMER_ID, TERMINAL_ID,
                   make_timestamp_ns(epoch_ns(TX_DATETIME) + {LABEL_DELAY_DAYS * DAY_NS})
                   AS LABEL_AVAILABLE_AT, TX_FRAUD, TX_AMOUNT,
                   {", ".join(histories)}
            FROM transactions WINDOW {", ".join(windows)}
        )
        SELECT {metadata}, {", ".join(predictors)} FROM histories
    """


def compute_features(connection: duckdb.DuckDBPyConnection) -> None:
    """Materialize all context before selecting split rows; caller owns the connection."""
    connection.execute(f"CREATE OR REPLACE TEMP TABLE gold_features AS {feature_query()}")
