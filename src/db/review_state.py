from psycopg.rows import dict_row


def get(conn, appid, language):
    cur = conn.cursor(row_factory=dict_row)
    cur.execute(
        "SELECT * FROM meta.review_collection WHERE appid = %s AND language = %s",
        (appid, language),
    )
    return cur.fetchone()


def start(conn, appid, language):
    conn.execute(
        """
        INSERT INTO meta.review_collection (appid, language, status, started_at)
        VALUES (%s, %s, 'running', now())
        ON CONFLICT (appid, language) DO UPDATE
        SET status = 'running', started_at = now(), finished_at = NULL,
            last_cursor = NULL, n_items = 0, run_max_updated_at = NULL
        """,
        (appid, language),
    )
    conn.commit()


def update(conn, appid, language, **fields):
    sets = ", ".join(f"{k} = %({k})s" for k in fields)
    conn.execute(
        f"UPDATE meta.review_collection SET {sets} "
        "WHERE appid = %(appid)s AND language = %(language)s",
        {"appid": appid, "language": language, **fields},
    )
    conn.commit()
