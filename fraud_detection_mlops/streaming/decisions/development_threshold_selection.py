"""Select the prespecified capacity cutoff from label-free development scores."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

WINDOWS = (
    ("2018-07-15", "2018-07-22"),
    ("2018-07-29", "2018-08-05"),
    ("2018-08-12", "2018-08-19"),
)
SCORING_COLUMNS = ["TRANSACTION_ID", "CUSTOMER_ID", "TX_DATETIME", "score"]


def selection_days() -> list[str]:
    return [
        str(date.fromisoformat(start) + timedelta(days=i))
        for start, end in WINDOWS
        for i in range((date.fromisoformat(end) - date.fromisoformat(start)).days)
    ]


def select_development_threshold(scores: pd.DataFrame) -> dict:
    """Maximum per customer/day, 100th ranked customer per day, median of 21 cuts."""
    if list(scores.columns) != SCORING_COLUMNS or scores.empty or scores.isna().any().any():
        raise ValueError(
            "Expected only aligned transaction/customer/time/score columns, no labels"
        )
    for column in ("TRANSACTION_ID", "CUSTOMER_ID"):
        if (
            not pd.api.types.is_integer_dtype(scores[column])
            or pd.api.types.is_bool_dtype(scores[column])
            or (scores[column] < 0).any()
        ):
            raise ValueError("Expected nonnegative integer transaction and customer IDs")
    if scores.TRANSACTION_ID.duplicated().any():
        raise ValueError("Duplicate development transaction identity")
    if (
        not pd.api.types.is_datetime64_dtype(scores.TX_DATETIME)
        or scores.TX_DATETIME.dt.tz is not None
        or not pd.api.types.is_float_dtype(scores.score)
        or not np.isfinite(scores.score).all()
        or not scores.score.between(0, 1).all()
    ):
        raise ValueError("Expected source timestamps and finite ranking scores in [0, 1]")
    frame = scores.assign(day=scores.TX_DATETIME.dt.strftime("%Y-%m-%d"))
    if sorted(frame.day.unique()) != selection_days():
        raise ValueError("Expected exactly the 21 development days; reserved dates are forbidden")
    customers = frame.groupby(["day", "CUSTOMER_ID"], as_index=False).score.max()
    cuts = []
    for day, group in customers.groupby("day", sort=True):
        if len(group) < 100:
            raise ValueError("Every development day requires at least 100 distinct customers")
        ranked = group.sort_values(["score", "CUSTOMER_ID"], ascending=[False, True])
        cutoff = ranked.iloc[99]
        cuts.append(
            {
                "date": day,
                "customers": len(group),
                "cutoff_customer_id": int(cutoff.CUSTOMER_ID),
                "cutoff_score": float(cutoff.score),
            }
        )
    return {
        "score_threshold": float(np.median([r["cutoff_score"] for r in cuts])),
        "days": 21,
        "rows": len(scores),
        "daily_customer_budget": 100,
        "daily_cutoffs": cuts,
    }
