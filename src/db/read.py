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


def review_totals(appids=None) -> pd.DataFrame:
    """Steam's own review counts, one row per collection run."""
    conditions, params = _filters(appids=appids)
    return _select(f"SELECT * FROM core.review_summaries {_where(conditions)}", params)


def news(appids=None, since=None, until=None) -> pd.DataFrame:
    conditions, params = _filters(appids, since=since, until=until, time_column="published_at")
    return _select(f"SELECT * FROM core.news {_where(conditions)}", params)


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
