import duckdb
import pandas as pd


def test_transactions_round_trip_through_parquet_and_sql(tmp_path):
    transactions = pd.DataFrame(
        {
            "TRANSACTION_ID": [101, 102, 103],
            "TX_DATETIME": pd.to_datetime(
                [
                    "2018-04-01 10:00:00",
                    "2018-04-01 10:05:00",
                    "2018-04-01 10:10:00",
                ]
            ),
            "CUSTOMER_ID": [1, 1, 2],
            "TERMINAL_ID": [10, 20, 10],
            "TX_AMOUNT": [50.0, 250.0, 75.5],
            "TX_FRAUD": [0, 1, 0],
        }
    )
    parquet_path = tmp_path / "transactions.parquet"

    transactions.to_parquet(parquet_path, engine="pyarrow", index=False)
    restored = pd.read_parquet(parquet_path, engine="pyarrow")

    pd.testing.assert_frame_equal(transactions, restored, check_exact=True)

    with duckdb.connect() as connection:
        result = connection.execute(
            "SELECT COUNT(*), SUM(TX_FRAUD) FROM read_parquet(?)",
            [str(parquet_path)],
        ).fetchone()

    assert result == (3, 1)
