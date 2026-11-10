import pandas as pd

from src.config import CONFIG
from src.db import read
from src.text.topics import topic_names

DENSE = pd.Timedelta(days=1.5)  # player points closer than this are daily or finer data
WEEK_RULES = {"players": "mean", "patch": "max"}  # weekly(): other columns are counts (summed)
TEXT_LANGUAGES = ["en", "ru", "zh"]  # detected text languages with own columns; the rest: other
COMPLETE = 0.95  # share of Steam's review count from which a language counts as fully collected


def daily_series(appids=None, since=None) -> pd.DataFrame:
    """One row per game and UTC day, from its first kept review to its last fully collected
    day (see _calendar): days after the last collection are unknown, not 0.

    Index (appid, day). Columns: n (kept reviews), n_<Steam language> for the configured
    languages and n_other (the few reviews Steam labels otherwise), text_<en|ru|zh|other> by the
    detected language of the text (a Turkish review in the "english" section is text_other),
    n_positive, positive (👍 share, NaN without reviews), players (mean online, NaN without
    daily data), patch (a patch or update was published that day).
    """
    until = _today()
    counts = read.daily_reviews(appids, since, until)
    by_language = _counts_by(counts, "language", CONFIG.reviews.languages, "n_")
    by_text = _counts_by(counts, "text_language", TEXT_LANGUAGES, "text_")
    totals = counts.groupby(["appid", "day"])[["n", "n_positive"]].sum()
    series = _calendar(totals.join(by_language).join(by_text), until)
    series["positive"] = series["n_positive"] / series["n"].where(series["n"] > 0)
    online = online_players(appids)
    series["players"] = online[online["daily"]].set_index(["appid", "day"])["players"]
    series["patch"] = series.index.isin(patch_days(appids))
    return series


def daily_topics(appids=None, since=None) -> pd.DataFrame:
    """Kept reviews per game and UTC day (rows, as in daily_series) and topic_id (columns)."""
    until = _today()
    counts = read.daily_topics(appids, since, until)
    wide = counts.pivot_table(
        index=["appid", "day"], columns="topic_id", values="n", aggfunc="sum", fill_value=0
    )
    return _calendar(wide.astype("int32"), until)


def label_counts(topic_counts: pd.DataFrame) -> pd.DataFrame:
    """daily_topics with topics that share a label (one theme in several languages, named in
    topics/02_explore_name) summed into one column per label."""
    labels = topic_names(read.topics(), with_id=False)
    return topic_counts.T.groupby(topic_counts.columns.map(labels)).sum().T


def weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """daily_series or daily_topics per week; `day` becomes the week's Monday.

    Counts are summed, players averaged, patch: any day of the week. positive is recomputed.
    Only complete weeks: the first and last week of a game are dropped if they are partial.
    """
    week = daily.index.get_level_values("day").to_period("W").start_time
    grouped = daily.groupby([daily.index.get_level_values("appid"), week])
    rules = {c: WEEK_RULES.get(c, "sum") for c in daily.columns if c != "positive"}
    out = grouped.agg(rules)[grouped.size() == 7]
    if "n_positive" in out:
        out["positive"] = out["n_positive"] / out["n"].where(out["n"] > 0)
    out.index.names = ["appid", "day"]
    return out


def online_players(appids=None) -> pd.DataFrame:
    """Mean online players per game and UTC day: columns appid, day, players, daily.

    SteamCharts has only monthly averages for older months (daily=False, one row per month)
    and daily or finer points for the last months (daily=True).
    """
    points = read.player_counts(appids).sort_values(["appid", "ts"])
    points = points[points["players"] > 0]
    ts = points.groupby("appid")["ts"]
    nearest = pd.concat([ts.diff(), -ts.diff(-1)], axis=1).min(axis=1)
    points = points.assign(
        day=points["ts"].dt.tz_convert(None).dt.floor("D"), daily=nearest <= DENSE
    )
    return points.groupby(["appid", "day", "daily"], as_index=False)["players"].mean()


def patch_days(appids=None) -> pd.MultiIndex:
    """(appid, UTC day) of every official patch or update (see read.patches)."""
    patches = read.patches(appids)
    day = patches["published_at"].dt.tz_convert(None).dt.floor("D")
    return pd.MultiIndex.from_arrays([patches["appid"], day], names=["appid", "day"]).unique()


def launch_window(index: pd.MultiIndex, first_review: pd.Series, days=30, before=7) -> pd.Series:
    """True for (appid, day) rows within `days` of the game's first review (first_review:
    appid -> day) or of its Steam release date (from `before` days earlier: open betas and
    preloads): launch spikes, not anomalies or patch effects. Early access games have both
    (e.g. CS:GO: beta in May 2012, open beta a week before the release in August)."""
    appid, day = index.get_level_values("appid"), index.get_level_values("day")
    release = pd.to_datetime(read.games().set_index("appid")["release_date"])
    window = pd.Timedelta(days=days)
    first = first_review.reindex(appid).to_numpy()
    released = release.reindex(appid).to_numpy()
    after_first = (day >= first) & (day < first + window)
    around_release = (day >= released - pd.Timedelta(days=before)) & (day < released + window)
    return pd.Series(after_first | around_release, index=index)


def collection(appids=None) -> pd.DataFrame:
    """Per game (index appid), from the raw pages, so also for data someone else collected:

    collected_at: start of the oldest of its languages' latest collection runs (a run goes from
    the newest review back, so reviews written later are not in the data); coverage: share of
    Steam's review count we have; complete: every language with reviews on Steam has at least
    COMPLETE of them. A partly collected game has only its newest reviews: its series starts late.
    """
    runs = read.review_totals(appids).sort_values("fetched_at")
    latest = runs.drop_duplicates(["appid", "language"], keep="last")
    c = latest.merge(read.review_counts(appids), on=["appid", "language"], how="left")
    c = c[c["total_reviews"] > 0].fillna({"n": 0})
    c["share"] = c["n"] / c["total_reviews"]
    grouped = c.groupby("appid")
    return pd.DataFrame(
        {
            "collected_at": pd.to_datetime(grouped["fetched_at"].min(), utc=True),
            "coverage": grouped["n"].sum() / grouped["total_reviews"].sum(),
            "complete": grouped["share"].min() >= COMPLETE,
        }
    )


def _counts_by(counts, column, values, prefix) -> pd.DataFrame:
    """Reviews per (appid, day) and `values` of `column` (anything else: "other")."""
    columns = [*values, "other"]
    key = counts[column].where(counts[column].isin(values), "other")
    wide = counts.assign(key=key).pivot_table(
        index=["appid", "day"], columns="key", values="n", aggfunc="sum", fill_value=0
    )
    wide = wide.reindex(columns=columns, fill_value=0).add_prefix(prefix)
    wide.columns.name = None
    return wide


def _today() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").normalize()


def _calendar(counts: pd.DataFrame, today: pd.Timestamp) -> pd.DataFrame:
    """Counts (index appid, day) on every day from each game's first day to its last fully
    collected day; days without rows are 0.

    Last fully collected day: the day before collection(...)["collected_at"] (that day is
    partial), or its last review day if unknown; never today.
    """
    if counts.empty:
        return counts
    days = counts.index.to_frame(index=False).groupby("appid")["day"].agg(["min", "max"])
    collected = collection(days.index)["collected_at"]
    collected = collected.dt.tz_convert(None).dt.floor("D") - pd.Timedelta(days=1)
    last = collected.reindex(days.index).fillna(days["max"])
    last = last.clip(upper=today.tz_localize(None) - pd.Timedelta(days=1))
    index = pd.MultiIndex.from_frame(
        pd.concat(
            pd.DataFrame({"appid": appid, "day": pd.date_range(days.loc[appid, "min"], end)})
            for appid, end in last.items()
        )
    )
    return counts.reindex(index, fill_value=0)
