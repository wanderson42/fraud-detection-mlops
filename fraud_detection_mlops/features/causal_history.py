"""DuckDB feature computation with strict event and delayed-label boundaries."""

import duckdb

from fraud_detection_mlops.features.feature_schema import (
    DAY_NS,
    LABEL_DELAY_DAYS,
    METADATA_DTYPES,
    WINDOW_DAYS,
)


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
