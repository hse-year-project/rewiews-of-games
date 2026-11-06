from src.db.connection import connect


def upsert(conn, table: str, rows: list[dict], key: list[str]) -> int:
    """Insert rows, overwriting existing ones with the same key."""
    if not rows:
        return 0
    cols = list(rows[0])
    placeholders = ", ".join(f"%({c})s" for c in cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in key)
    query = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}"
    )
    conn.cursor().executemany(query, rows)
    return len(rows)


def replace_review_flags(conn, flags, report):
    """Replace all of clean.review_flags and append this run to meta.cleaning_report."""
    cur = conn.cursor()
    cur.execute("TRUNCATE clean.review_flags")
    _copy(cur, "clean.review_flags", flags)
    _copy(cur, "meta.cleaning_report", report)
    conn.commit()


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
