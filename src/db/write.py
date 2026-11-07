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
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in key)
    query = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}"
    )
    if newer:
        query += f" WHERE {table}.{newer} <= EXCLUDED.{newer}"
    conn.cursor().executemany(query, rows)
    return len(rows)


def save_review_flags(flags):
    """Insert or overwrite flags of these reviews."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("CREATE TEMP TABLE new_flags (LIKE clean.review_flags) ON COMMIT DROP")
        _copy(cur, "new_flags", flags)
        cols = ", ".join(flags.columns)
        updates = ", ".join(
            f"{c} = EXCLUDED.{c}" for c in flags.columns if c != "recommendation_id"
        )
        cur.execute(
            f"INSERT INTO clean.review_flags ({cols}) SELECT {cols} FROM new_flags "
            f"ON CONFLICT (recommendation_id) DO UPDATE SET {updates}"
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
        cur.execute("CREATE TEMP TABLE new_topics (LIKE analysis.review_topics) ON COMMIT DROP")
        _copy(cur, "new_topics", review_topics)
        cur.execute(
            "INSERT INTO analysis.review_topics SELECT * FROM new_topics "
            "ON CONFLICT (recommendation_id) DO UPDATE SET topic_id = EXCLUDED.topic_id"
        )
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


def _copy(cur, table, df):
    """Bulk insert a DataFrame with COPY (missing values -> NULL)."""
    df = df.astype(object).where(df.notna(), None)
    with cur.copy(f"COPY {table} ({', '.join(df.columns)}) FROM STDIN") as copy:
        for row in df.itertuples(index=False):
            copy.write_row(row)
