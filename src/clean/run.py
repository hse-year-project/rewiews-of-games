from datetime import UTC, datetime

import pandas as pd

from src.clean.rules import FLAG_COLUMNS, RULE_NAMES, add_flags
from src.config import CONFIG
from src.db import read, write
from src.db.connection import connect

REPORT_COLUMNS = ["run_id", "rule", "appid", "language", "n_total", "n_flagged"]


def summarize(df):
    """Flagged counts per rule, game and language; "dropped" counts keep = False."""
    flags = df[RULE_NAMES].rename(columns=lambda c: c.removeprefix("is_"))
    flags["dropped"] = ~df["keep"]
    groups = flags.groupby([df["appid"], df["language"]])
    report = groups.sum().melt(ignore_index=False, var_name="rule", value_name="n_flagged")
    return report.join(groups.size().rename("n_total")).reset_index()


def clean_reviews() -> pd.DataFrame:
    """Flag every review in core.reviews; returns this run's report."""
    df = read.reviews(with_flags=False)
    if df.empty:
        return pd.DataFrame()

    df = add_flags(df, CONFIG.cleaning)
    report = summarize(df)
    report["run_id"] = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    flags = df[FLAG_COLUMNS].astype(object)
    flags = flags.where(flags.notna(), None)
    with connect() as conn:
        write.replace_review_flags(conn, flags, report[REPORT_COLUMNS].astype(object))
    return report
