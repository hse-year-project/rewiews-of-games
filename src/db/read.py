import pandas as pd

from src.clean.rules import FLAG_COLUMNS
from src.db.connection import connect

FETCH_ROWS = 200_000  # rows turned into a DataFrame at a time (keeps big reads small in memory)
TIMESTAMPTZ = 1184  # Postgres type id


def games() -> pd.DataFrame:
    return _select("SELECT * FROM core.games")


def prices(appids=None) -> pd.DataFrame:
    conditions, params = _filters(appids=appids)
    return _select(f"SELECT * FROM core.game_prices {_where(conditions)}", params)


def reviews(
    appids=None,
    languages=None,
    since=None,
    until=None,
    columns: list[str] | None = None,
    ids=None,
    authors=None,
    only_kept=False,
    skip_short=False,
    flagged_since=None,
    with_flags=True,
    flag_columns: list[str] | None = None,
    with_topics=False,
    text_length=False,
    _rows=None,
) -> pd.DataFrame:
    """Reviews with their cleaning flags (and topic). since/until filter by created_at.

    ids / authors: only these reviews / these authors' (author_steamid) reviews.
    skip_short: without too-short reviews; flagged_since: only reviews flagged after this time.
    For millions of reviews load only what you need: `columns` (e.g. without `review`),
    `flag_columns` (None = all, [] = none), `text_length=True` adds text_len instead of the text.
    Topic names: read.topics().
    """
    conditions, params = _filters(appids, languages, since, until, time_column="created_at")
    if ids is not None:
        conditions.append("r.recommendation_id = ANY(%(ids)s)")
        params["ids"] = list(ids)
    if authors is not None:
        conditions.append("r.author_steamid = ANY(%(authors)s)")
        params["authors"] = list(authors)
    select = [", ".join(f"r.{c}" for c in columns) if columns else "r.*"]
    if text_length:
        select.append("length(r.review) AS text_len")
    joins = ""
    if with_flags:
        if flag_columns is None:
            flag_columns = [c for c in FLAG_COLUMNS if c != "recommendation_id"]
        select += [f"f.{c}" for c in flag_columns]
        joins += " LEFT JOIN clean.review_flags f USING (recommendation_id)"
        if only_kept:
            conditions.append("f.keep")
        if skip_short:
            conditions.append("NOT f.is_short")
        if flagged_since is not None:
            conditions.append("f.flagged_at > %(flagged_since)s")
            params["flagged_since"] = _utc(flagged_since)
    if with_topics:
        select.append("rt.topic_id")
        joins += " LEFT JOIN analysis.review_topics rt USING (recommendation_id)"
    query = f"SELECT {', '.join(select)} FROM core.reviews r{joins} {_where(conditions)}"
    return _chunks(query, params, _rows) if _rows else _select(query, params)


def iter_reviews(rows=FETCH_ROWS, **kwargs):
    """Same arguments as reviews(), but yields DataFrames of `rows` reviews from one query:
    for passes over all reviews (e.g. all texts) without holding them in memory at once."""
    return reviews(**kwargs, _rows=rows)


def reviews_to_clean(full=False) -> pd.DataFrame:
    """IDs of reviews to flag: parsed (new or changed) since they were last flagged; full: all.
    author_key = author's Steam ID (the review's own ID when unknown), to keep authors together."""
    pending = "" if full else "JOIN clean.pending USING (recommendation_id)"
    return _select(
        "SELECT recommendation_id, coalesce(author_steamid, recommendation_id) AS author_key "
        f"FROM core.reviews {pending}"
    )


def rules_changed(rules_hash: str) -> bool:
    """Were any reviews flagged under other rules or thresholds than the current ones?"""
    query = "SELECT EXISTS (SELECT 1 FROM clean.review_flags WHERE rules_hash IS DISTINCT FROM %s)"
    return bool(_select(query, (rules_hash,)).iloc[0, 0])


def watermark(name: str):
    """Last stored time for `name` (write.set_watermark), None if never set."""
    found = _select("SELECT value FROM meta.watermarks WHERE name = %s", (name,))
    return None if found.empty else found["value"].iloc[0]


def flag_counts(rules: list[str]) -> pd.DataFrame:
    """Per game and language: flagged reviews, hits of each rule, dropped reviews."""
    hits = ", ".join(f"count(*) FILTER (WHERE f.{rule}) AS {rule}" for rule in rules)
    return _select(
        f"SELECT r.appid, r.language, count(*) AS n_total, {hits}, "
        "count(*) FILTER (WHERE NOT f.keep) AS dropped "
        "FROM core.reviews r JOIN clean.review_flags f USING (recommendation_id) "
        "GROUP BY r.appid, r.language"
    )


def flagged_examples(rule: str, n=8) -> pd.DataFrame:
    """n random reviews that a cleaning rule (e.g. "is_junk") hit."""
    return _select(
        "SELECT r.language, f.lang_detected, f.lang_conf, r.review "
        f"FROM core.reviews r JOIN clean.review_flags f USING (recommendation_id) WHERE f.{rule} "
        "ORDER BY random() LIMIT %(n)s",
        {"n": n},
    )


def daily_reviews(appids=None, since=None, until=None) -> pd.DataFrame:
    """Kept reviews per game, UTC day (of created_at), Steam language and detected text
    language: n, n_positive."""
    return _daily(
        ["r.language", "f.text_language"],
        "count(*) AS n, count(*) FILTER (WHERE r.voted_up) AS n_positive",
        "",
        appids,
        since,
        until,
    )


def daily_topics(appids=None, since=None, until=None) -> pd.DataFrame:
    """Kept reviews per game, UTC day (of created_at) and topic: n."""
    return _daily(
        ["rt.topic_id"],
        "count(*) AS n",
        "JOIN analysis.review_topics rt USING (recommendation_id)",
        appids,
        since,
        until,
    )


def _daily(keys, values, join, appids, since, until) -> pd.DataFrame:
    conditions, params = _filters(appids, since=since, until=until, time_column="r.created_at")
    conditions.append("f.keep")
    group = ", ".join(str(i) for i in range(1, len(keys) + 3))
    return _select(
        f"SELECT r.appid, date_trunc('day', r.created_at AT TIME ZONE 'UTC') AS day, "
        f"{', '.join(keys)}, {values} "
        f"FROM core.reviews r JOIN clean.review_flags f USING (recommendation_id) {join} "
        f"{_where(conditions)} GROUP BY {group}",
        params,
    )


def language_profile(appids=None) -> pd.DataFrame:
    """Per game and Steam language, over kept reviews: n, positive (👍 share), medians of hours
    played at review and in total, the author's review count and the text length, and the
    shares written under 2 hours (Steam's refund window), bought on Steam, and got for free."""
    conditions, params = _filters(appids=appids)
    conditions.append("f.keep")
    median = "percentile_cont(0.5) WITHIN GROUP (ORDER BY {})"
    return _select(
        "SELECT r.appid, r.language, count(*) AS n, avg(r.voted_up::int)::float AS positive, "
        f"{median.format('r.author_playtime_at_review')} / 60 AS hours_at_review, "
        f"{median.format('r.author_playtime_forever')} / 60 AS hours_total, "
        f"{median.format('r.author_num_reviews')} AS author_reviews, "
        f"{median.format('length(r.review)')} AS text_length, "
        "avg((r.author_playtime_at_review < 120)::int)::float AS under_2h, "
        "avg(r.steam_purchase::int)::float AS steam_purchase, "
        "avg(r.received_for_free::int)::float AS free "
        "FROM core.reviews r JOIN clean.review_flags f USING (recommendation_id) "
        f"{_where(conditions)} GROUP BY 1, 2",
        params,
    )


def topics_by_language(appids=None) -> pd.DataFrame:
    """Kept reviews per game, Steam language, 👍/👎 and topic: n."""
    conditions, params = _filters(appids=appids)
    conditions.append("f.keep")
    return _select(
        "SELECT r.appid, r.language, r.voted_up, rt.topic_id, count(*) AS n "
        "FROM core.reviews r JOIN clean.review_flags f USING (recommendation_id) "
        "JOIN analysis.review_topics rt USING (recommendation_id) "
        f"{_where(conditions)} GROUP BY 1, 2, 3, 4",
        params,
    )


def shared_reviewers() -> pd.DataFrame:
    """Audience overlap: for every pair of games (a < b), how many Steam accounts reviewed both;
    pairs with a = b give each game's number of reviewers."""
    return _select(
        "WITH a AS (SELECT DISTINCT appid, author_steamid FROM core.reviews "
        "WHERE author_steamid IS NOT NULL) "
        "SELECT x.appid AS a, y.appid AS b, count(*) AS n FROM a x JOIN a y "
        "ON x.author_steamid = y.author_steamid AND x.appid <= y.appid GROUP BY 1, 2"
    )


def review_totals(appids=None) -> pd.DataFrame:
    """Steam's own review counts, one row per collection run."""
    conditions, params = _filters(appids=appids)
    return _select(f"SELECT * FROM core.review_summaries {_where(conditions)}", params)


def review_counts(appids=None) -> pd.DataFrame:
    """Reviews we have per game and Steam language (before cleaning): n."""
    conditions, params = _filters(appids=appids)
    return _select(
        f"SELECT appid, language, count(*) AS n FROM core.reviews {_where(conditions)} "
        "GROUP BY 1, 2",
        params,
    )


def news(appids=None, since=None, until=None, columns: list[str] | None = None) -> pd.DataFrame:
    """columns: e.g. without `contents` (long patch notes) when only dates and titles are needed."""
    conditions, params = _filters(appids, since=since, until=until, time_column="published_at")
    select = ", ".join(columns) if columns else "*"
    return _select(f"SELECT {select} FROM core.news {_where(conditions)}", params)


def patches(appids=None) -> pd.DataFrame:
    """Official patch and update notes (the game's own Steam posts, not press articles about
    them): appid, published_at, title, length (characters of the notes)."""
    conditions, params = _filters(appids=appids)
    conditions += ["is_patch", "left(feedname, 6) = 'steam_'"]
    return _select(
        "SELECT appid, published_at, title, length(contents) AS length "
        f"FROM core.news {_where(conditions)}",
        params,
    )


def player_counts(appids=None, since=None, until=None) -> pd.DataFrame:
    conditions, params = _filters(appids, since=since, until=until, time_column="ts")
    return _select(f"SELECT * FROM core.player_counts {_where(conditions)}", params)


def topics() -> pd.DataFrame:
    return _select("SELECT * FROM analysis.topics ORDER BY topic_id")


def review_collection() -> pd.DataFrame:
    """Review download state per game and language."""
    return _select("SELECT * FROM meta.review_collection ORDER BY appid, language")


def cleaning_report() -> pd.DataFrame:
    """Counts per rule, game and language for every cleaning run."""
    return _select("SELECT * FROM meta.cleaning_report")


def _filters(appids=None, languages=None, since=None, until=None, time_column=None):
    conditions, params = [], {}
    if appids is not None:
        conditions.append("appid = ANY(%(appids)s)")
        params["appids"] = list(appids)
    if languages is not None:
        conditions.append("language = ANY(%(languages)s)")
        params["languages"] = list(languages)
    if since is not None:
        conditions.append(f"{time_column} >= %(since)s")
        params["since"] = _utc(since)
    if until is not None:
        conditions.append(f"{time_column} < %(until)s")
        params["until"] = _utc(until)
    return conditions, params


def _where(conditions) -> str:
    return ("WHERE " + " AND ".join(conditions)) if conditions else ""


def _utc(when):
    t = pd.Timestamp(when)
    return t.tz_localize("UTC") if t.tz is None else t


def _select(query, params=None) -> pd.DataFrame:
    parts = list(_chunks(query, params, FETCH_ROWS))
    return pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]


def _chunks(query, params, rows):
    """DataFrames of `rows` rows (at least one, maybe empty) from a server-side cursor."""
    with connect() as conn, conn.cursor(name="select") as cur:
        cur.execute(query, params)
        first = True
        while (batch := cur.fetchmany(rows)) or first:
            yield _frame(batch, cur.description)
            first = False


def _frame(rows, columns) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=[c.name for c in columns])
    for c in columns:
        if c.type_code == TIMESTAMPTZ:
            df[c.name] = pd.to_datetime(df[c.name], utc=True)
    return df
