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
SAVED_COLUMNS = [*FLAG_COLUMNS, "review_updated_at", "rules_hash", "flagged_at"]
CHUNK = 250_000  # reviews in memory at once


def rules_hash() -> str:
    """Fingerprint of the cleaning rules and thresholds: a change re-checks all reviews."""
    source = Path(rules.__file__).read_bytes() + json.dumps(CONFIG.cleaning.model_dump()).encode()
    return hashlib.sha256(source).hexdigest()[:16]


def chunks(todo: pd.DataFrame, size=CHUNK):
    """Review IDs in chunks of about `size`; all reviews of an author land in the same chunk."""
    parts = math.ceil(len(todo) / size)
    return [ids for _, ids in todo.groupby(todo["author_key"] % parts)["recommendation_id"]]


def clean_reviews(full=False, report=True) -> pd.DataFrame | None:
    """Flag reviews parsed since they were last flagged; all reviews when full or after a
    change to the rules or thresholds. Works in chunks of CHUNK reviews.

    report: return the report over all reviews (added to meta.cleaning_report when anything
    changed); False skips it (e.g. between pipeline passes) and returns None.
    """
    current = rules_hash()
    full = full or read.rules_changed(current)
    todo = read.reviews_to_clean(full)
    print(f"cleaning {len(todo):,} reviews: {'all' if full else 'new or changed'}")
    if not todo.empty:
        for ids in tqdm(chunks(todo), desc="clean", unit="chunk"):
            new = add_flags(read.reviews(ids=ids, with_flags=False), CONFIG.cleaning)
            new = new.assign(review_updated_at=new["updated_at"], rules_hash=current)
            others = pd.DataFrame() if full else other_reviews(new)
            new, changed = recheck_duplicates(new, others)
            now = pd.Timestamp.now(tz="UTC")
            write.save_review_flags(pd.concat([new, changed]).assign(flagged_at=now)[SAVED_COLUMNS])
    if not report:
        return None
    if todo.empty:
        stored = read.cleaning_report()
        if not stored.empty:
            return stored[stored["run_id"] == stored["run_id"].max()]

    counts = read.flag_counts(RULE_NAMES)
    result = counts.melt(["appid", "language", "n_total"], var_name="rule", value_name="n_flagged")
    result["rule"] = result["rule"].str.removeprefix("is_")
    result["run_id"] = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    write.add_cleaning_report(result[REPORT_COLUMNS])
    return result


def other_reviews(new: pd.DataFrame) -> pd.DataFrame:
    """Already flagged reviews by the authors of `new`, with their texts and flags."""
    authors = new["author_steamid"].dropna().unique()
    others = read.reviews(
        authors=authors,
        columns=["recommendation_id", "author_steamid", "review", "created_at"],
        flag_columns=[c for c in SAVED_COLUMNS if c != "recommendation_id"],
    )
    return others[~others["recommendation_id"].isin(new["recommendation_id"])].dropna(
        subset=["keep"]
    )


def recheck_duplicates(new: pd.DataFrame, others: pd.DataFrame):
    """Run the duplicate rule over new reviews together with their authors' other reviews.

    A new review is a duplicate if the same author wrote the same text earlier, and an older
    review becomes one if the new review is an earlier copy (a newly collected game). Only the
    duplicate check runs again on older reviews, not the other rules.
    Returns (new, older reviews whose flags changed).
    """
    if others.empty:
        return new, others
    columns = ["recommendation_id", "review", "author_steamid", "created_at"]
    both = pd.concat([new[columns], others[columns]]).set_index("recommendation_id")
    duplicate = rules.is_duplicate_text(both, CONFIG.cleaning)
    new = new.assign(is_duplicate_text=new["recommendation_id"].map(duplicate).to_numpy())
    new["keep"] = rules.keep(new)
    rechecked = others["recommendation_id"].map(duplicate).astype(bool)
    flipped = rechecked != others["is_duplicate_text"].astype(bool)
    changed = others[flipped].assign(is_duplicate_text=rechecked[flipped])
    changed["keep"] = rules.keep(changed)
    return new, changed
