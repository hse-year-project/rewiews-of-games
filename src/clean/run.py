import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from tqdm.auto import tqdm

from src.clean import rules
from src.clean.rules import FLAG_COLUMNS, RULE_NAMES, add_flags
from src.config import CONFIG
from src.db import read, write

REPORT_COLUMNS = ["run_id", "rule", "appid", "language", "n_total", "n_flagged"]
CHUNK = 250_000  # reviews in memory at once


def rules_hash() -> str:
    """Fingerprint of the cleaning rules and thresholds: when it changes, every review is re-checked."""
    source = Path(rules.__file__).read_bytes() + json.dumps(CONFIG.cleaning.model_dump()).encode()
    return hashlib.sha256(source).hexdigest()[:16]


def chunks(todo: pd.DataFrame, size=CHUNK):
    """Review IDs in chunks of about `size`; all reviews of an author land in the same chunk."""
    parts = math.ceil(len(todo) / size)
    return [ids for _, ids in todo.groupby(todo["author_key"] % parts)["recommendation_id"]]


def clean_reviews(full=False) -> pd.DataFrame:
    """Flag reviews that are new, edited or were flagged under other rules (full: all reviews).

    Works in chunks of CHUNK reviews. Returns the report over all reviews, which is also added
    to meta.cleaning_report when anything changed.
    """
    current = rules_hash()
    todo = read.reviews_to_clean(current, full)
    which = "all" if full else "new or changed (with other reviews of their authors)"
    print(f"cleaning {len(todo):,} reviews: {which}")
    if not todo.empty:
        for ids in tqdm(chunks(todo), desc="clean", unit="chunk"):
            df = add_flags(read.reviews(ids=ids, with_flags=False), CONFIG.cleaning)
            flags = df[FLAG_COLUMNS].assign(review_updated_at=df["updated_at"], rules_hash=current)
            write.save_review_flags(flags)

    counts = read.flag_counts(RULE_NAMES)
    report = counts.melt(["appid", "language", "n_total"], var_name="rule", value_name="n_flagged")
    report["rule"] = report["rule"].str.removeprefix("is_")
    report["run_id"] = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if not todo.empty:
        write.add_cleaning_report(report[REPORT_COLUMNS])
    return report
