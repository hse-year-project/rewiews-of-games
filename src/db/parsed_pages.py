def get(conn, source) -> set[str]:
    rows = conn.execute("SELECT path FROM meta.parsed_pages WHERE source = %s", (source,))
    return {path for (path,) in rows}


def add(conn, source, paths):
    conn.cursor().executemany(
        "INSERT INTO meta.parsed_pages (source, path) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        [(source, path) for path in paths],
    )
