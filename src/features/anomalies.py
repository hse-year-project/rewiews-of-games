import numpy as np
import pandas as pd

KINDS = ["bombing", "negative", "spike"]


def review_anomalies(
    series: pd.DataFrame, window=28, z=4.0, min_reviews=20, min_scale=0.2, exclude=None
) -> pd.DataFrame:
    """Rows whose review count or 👎 share jumps above the game's previous `window` rows.

    series: daily_series or weekly(...) (index appid, day; columns n, n_positive).
    z_count: log(1 + n) against the window's median, in robust standard deviations
    (IQR / 1.35, at least `min_scale`, i.e. a ~20% change). z_negative: the 👎 share against
    the window's pooled 👎 share, in binomial standard errors, so a day with few reviews
    needs a bigger jump. Only rows with at least `min_reviews` reviews and half a window of
    history are flagged; `exclude` (bool, same index) marks rows never flagged (e.g. launch).
    kind: "bombing" (both jump), "negative" (only the 👎 share), "spike" (only the count),
    None (normal).
    """
    log_n = np.log1p(series["n"])
    negative = series["n"] - series["n_positive"]
    q25, median, q75 = (_past(log_n, window, "quantile", q) for q in (0.25, 0.5, 0.75))
    scale = ((q75 - q25) / 1.35).clip(lower=min_scale)
    past_negative = _past(negative, window, "sum") / _past(series["n"], window, "sum")
    base_negative = past_negative.clip(0.01, 0.99)
    n = series["n"].where(series["n"] > 0)
    out = pd.DataFrame(
        {
            "n": series["n"],
            "baseline_n": np.expm1(median),
            "negative": negative / n,
            "baseline_negative": base_negative,
            "z_count": (log_n - median) / scale,
            "z_negative": (negative / n - base_negative)
            / np.sqrt(base_negative * (1 - base_negative) / n),
        }
    )
    enough = (out["n"] >= min_reviews) & median.notna()
    if exclude is not None:
        enough &= ~exclude.reindex(out.index, fill_value=False)
    high_count, high_negative = enough & (out["z_count"] >= z), enough & (out["z_negative"] >= z)
    out["kind"] = np.select(
        [high_count & high_negative, high_negative, high_count], KINDS, default=None
    )
    return out


def group_events(found: pd.DataFrame, step_days=1, max_gap=3) -> pd.DataFrame:
    """Unusual rows of review_anomalies(...) -> events: a game's rows at most `max_gap` steps
    apart are one event. step_days: the series' row length (1, or 7 for weekly series).

    Columns: appid, start, last, end (first day after), peak (row with the biggest 👎 jump),
    reviews, negative (👎 share), usual_reviews and usual_negative (baseline at the start, per
    row), z_count, z_negative (their maxima), kind (bombing > negative > spike), step.
    """
    step = pd.Timedelta(days=step_days)
    flagged = found[found["kind"].notna()].reset_index().sort_values(["appid", "day"])
    gap = flagged.groupby("appid")["day"].diff()
    flagged["event"] = (gap.isna() | (gap > max_gap * step)).cumsum()
    flagged["n_negative"] = flagged["negative"] * flagged["n"]
    grouped = flagged.groupby("event")
    events = grouped.agg(
        appid=("appid", "first"),
        start=("day", "min"),
        last=("day", "max"),
        reviews=("n", "sum"),
        n_negative=("n_negative", "sum"),
        usual_reviews=("baseline_n", "first"),
        usual_negative=("baseline_negative", "first"),
        z_count=("z_count", "max"),
        z_negative=("z_negative", "max"),
        kind=("kind", lambda k: next(kind for kind in KINDS if kind in set(k))),
    )
    events["end"] = events["last"] + step
    events["peak"] = flagged.loc[grouped["z_negative"].idxmax(), "day"].to_numpy()
    events["negative"] = events.pop("n_negative") / events["reviews"]
    events["step"] = step
    return events.reset_index(drop=True)


def platform_days(found: pd.DataFrame, z=2.0, min_games=5, min_share=0.25) -> pd.DataFrame:
    """Days when many games get more reviews than usual at once: Steam-wide events (sales,
    awards, store events), found from the data instead of a calendar.

    found: review_anomalies(...) of daily series of many games. Per day: games with a baseline,
    how many of them are at least `z` above it (rising), their share; platform: at least
    `min_games` and `min_share` of them.
    """
    comparable = found[found["baseline_n"].notna()]
    rising = (comparable["z_count"] >= z).groupby(level="day")
    out = pd.DataFrame({"games": rising.size(), "rising": rising.sum()})
    out["share"] = out["rising"] / out["games"]
    out["platform"] = (out["rising"] >= min_games) & (out["share"] >= min_share)
    return out


def _past(values: pd.Series, window: int, stat: str, *args) -> pd.Series:
    """Rolling `stat` over each game's previous `window` rows (not the row itself)."""
    return values.groupby(level="appid", group_keys=False).apply(
        lambda g: getattr(g.shift(1).rolling(window, min_periods=window // 2), stat)(*args)
    )
