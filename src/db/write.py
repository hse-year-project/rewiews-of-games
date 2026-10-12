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


def _copy(cur, table, df):
    """Bulk insert a DataFrame with COPY."""
    with cur.copy(f"COPY {table} ({', '.join(df.columns)}) FROM STDIN") as copy:
        for row in df.itertuples(index=False):
            copy.write_row(row)
