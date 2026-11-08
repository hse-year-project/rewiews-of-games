from src.db.connection import connect


def upsert(conn, table: str, rows: list[dict], key: list[str], newer: str | None = None) -> int:
    """Insert rows, overwriting existing ones with the same key.

    newer: a timestamp column; an existing row is only overwritten by a row at least as new
    (so parsing an older raw page after a newer one keeps the newer data).
    """
    if not rows:
        return 0
    cols = list(rows[0])
    placeholders = ", ".join(f"%({c})s" for c in cols)
    query = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        + _on_conflict_update(cols, key)
    )
    if newer:
        query += f" WHERE {table}.{newer} <= EXCLUDED.{newer}"
    conn.cursor().executemany(query, rows)
    return len(rows)


def mark_for_cleaning(conn, ids):
    """Reviews just parsed (new or changed): the next cleaning flags them."""
    conn.cursor().executemany(
        "INSERT INTO clean.pending VALUES (%s) ON CONFLICT DO NOTHING", [(i,) for i in ids]
    )


def save_review_flags(flags):
    """Insert or overwrite flags of these reviews; they are no longer pending."""
    with connect() as conn:
        cur = conn.cursor()
        _bulk_upsert(cur, "clean.review_flags", flags, ["recommendation_id"])
        cur.execute(
            "DELETE FROM clean.pending WHERE recommendation_id = ANY(%s)",
            (list(flags["recommendation_id"]),),
        )


def set_watermark(name: str, value):
    with connect() as conn:
        conn.execute(
            "INSERT INTO meta.watermarks VALUES (%s, %s) "
            "ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value",
            (name, value),
        )


def add_cleaning_report(report):
    with connect() as conn:
        _copy(conn.cursor(), "meta.cleaning_report", report)


def replace_topics(topics, review_topics):
    """Replace all topics and review -> topic assignments (after a new fit)."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("TRUNCATE analysis.topics, analysis.review_topics")
        _copy(cur, "analysis.topics", topics)
        _copy(cur, "analysis.review_topics", review_topics)


def add_review_topics(review_topics):
    """Assign topics to more reviews (overwriting old assignments) and recount topic sizes."""
    with connect() as conn:
        cur = conn.cursor()
        _bulk_upsert(cur, "analysis.review_topics", review_topics, ["recommendation_id"])
        cur.execute(
            "UPDATE analysis.topics t SET n_reviews = "
            "(SELECT count(*) FROM analysis.review_topics rt WHERE rt.topic_id = t.topic_id)"
        )


def set_topic_labels(labels: dict[int, str]):
    with connect() as conn:
        conn.cursor().executemany(
            "UPDATE analysis.topics SET label = %s WHERE topic_id = %s",
            [(label, topic_id) for topic_id, label in labels.items()],
        )


def _bulk_upsert(cur, table, df, key):
    """upsert() for big DataFrames: COPY into a temporary table, then one INSERT ... ON CONFLICT."""
    cur.execute(f"CREATE TEMP TABLE staging (LIKE {table}) ON COMMIT DROP")
    _copy(cur, "staging", df)
    cols = ", ".join(df.columns)
    cur.execute(
        f"INSERT INTO {table} ({cols}) SELECT {cols} FROM staging "
        + _on_conflict_update(df.columns, key)
    )


def _on_conflict_update(cols, key) -> str:
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in key)
    return f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}"


def _copy(cur, table, df):
    """Bulk insert a DataFrame with COPY (missing values -> NULL)."""
    df = df.astype(object).where(df.notna(), None)
    with cur.copy(f"COPY {table} ({', '.join(df.columns)}) FROM STDIN") as copy:
        for row in df.itertuples(index=False):
            copy.write_row(row)
