import pandas as pd

from src.clean.rules import FLAG_COLUMNS
from src.db.connection import connect


def games() -> pd.DataFrame:
    return _select("SELECT * FROM core.games")


def prices(appids=None) -> pd.DataFrame:
    where, params = _filters(appids=appids)
    return _select(f"SELECT * FROM core.game_prices {where}", params)


def reviews(
    appids=None,
    languages=None,
    since=None,
    until=None,
    columns: list[str] | None = None,
    only_kept=False,
    with_flags=True,
) -> pd.DataFrame:
    """Reviews with their cleaning flags. since/until filter by created_at."""
    where, params = _filters(appids, languages, since, until, time_column="created_at")
    review_cols = ", ".join(f"r.{c}" for c in columns) if columns else "r.*"
    if not with_flags:
        return _select(f"SELECT {review_cols} FROM core.reviews r {where}", params)
    if only_kept:
        where += (" AND " if where else "WHERE ") + "f.keep"
    flag_cols = ", ".join(f"f.{c}" for c in FLAG_COLUMNS if c != "recommendation_id")
    return _select(
        f"SELECT {review_cols}, {flag_cols} FROM core.reviews r "
        f"LEFT JOIN clean.review_flags f USING (recommendation_id) {where}",
        params,
    )


def review_totals(appids=None) -> pd.DataFrame:
    """Steam's own review counts, one row per collection run."""
    where, params = _filters(appids=appids)
    return _select(f"SELECT * FROM core.review_summaries {where}", params)


def news(appids=None, since=None, until=None) -> pd.DataFrame:
    where, params = _filters(appids, since=since, until=until, time_column="published_at")
    return _select(f"SELECT * FROM core.news {where}", params)


def player_counts(appids=None, since=None, until=None) -> pd.DataFrame:
    where, params = _filters(appids, since=since, until=until, time_column="ts")
    return _select(f"SELECT * FROM core.player_counts {where}", params)


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
    return ("WHERE " + " AND ".join(conditions)) if conditions else "", params


def _utc(when):
    t = pd.Timestamp(when)
    return t.tz_localize("UTC") if t.tz is None else t


TIMESTAMPTZ = 1184  # Postgres type id


def _select(query, params=None) -> pd.DataFrame:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        columns = cur.description
        df = pd.DataFrame(cur.fetchall(), columns=[c.name for c in columns])
    for c in columns:
        if c.type_code == TIMESTAMPTZ:
            df[c.name] = pd.to_datetime(df[c.name], utc=True)
    return df
