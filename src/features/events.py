import re

import numpy as np
import pandas as pd

from src.db import read
from src.features.daily import launch_window

HOTFIX = re.compile(r"hot\s?fix|bug\s?fix|\bfixes\b|small patch|minor", re.I)
MAJOR = re.compile(
    r"season|expansion|major|chapter|\bact\b|\b\d+\.0\b|\bdlc\b|anniversary|overhaul|rework|"
    r"big update|free update|launch|out now|available now|\bv?1\.0",
    re.I,
)
SIZES = ["hotfix", "update", "major"]


def patches(appids=None) -> pd.DataFrame:
    """Official patches per game and UTC day (several notes on one day are merged):
    appid, day, title, length (characters of the notes), size.

    size: "major" if the title says so (season, expansion, x.0, DLC...) or the notes are among
    the game's longest 10%; "hotfix" if the title says so or the notes are among its shortest 25%;
    otherwise "update". Per game, because some studios write a page where others write a line.
    """
    p = read.patches(appids)
    p["day"] = p["published_at"].dt.tz_convert(None).dt.floor("D")
    p = (
        p.sort_values("published_at")
        .groupby(["appid", "day"], as_index=False)
        .agg(title=("title", " | ".join), length=("length", "sum"))
    )
    by_game = p.groupby("appid")["length"]
    long = p["length"] >= by_game.transform(lambda s: s.quantile(0.9))
    short = p["length"] <= by_game.transform(lambda s: s.quantile(0.25))
    major = p["title"].str.contains(MAJOR) | long
    hotfix = ~major & (p["title"].str.contains(HOTFIX) | short)
    p["size"] = np.select([major, hotfix], ["major", "hotfix"], default="update")
    return p


def isolated(events: pd.DataFrame, days=14) -> pd.DataFrame:
    """Events (appid, day) with no other event of the same game in the `days` before: their
    baseline is not moved by an earlier one."""
    events = events.sort_values(["appid", "day"])
    gap = events.groupby("appid")["day"].diff()
    return events[gap.isna() | (gap > pd.Timedelta(days=days))]


def study_patches(
    series: pd.DataFrame,
    quiet_days=14,
    launch_days=45,
    after=14,
    baseline=(-14, -1),
    min_reviews=70,
) -> pd.DataFrame:
    """Patches fit for an event study on `series` (daily_series of the games): isolated (no
    other patch of the game in the `quiet_days` before), outside the game's launch period
    (launch_window of `launch_days`), with `after` days of data after them and at least
    `min_reviews` reviews in the `baseline` days (so the 👍 share there is stable)."""
    days = series.index.to_frame(index=False).groupby("appid")["day"].agg(["min", "max"])
    p = isolated(patches(days.index), quiet_days).reset_index(drop=True)
    in_launch = launch_window(
        pd.MultiIndex.from_frame(p[["appid", "day"]]), days["min"], launch_days
    )
    has_after = p["day"] + pd.Timedelta(days=after) <= p["appid"].map(days["max"])
    p = p[~in_launch.to_numpy() & has_after.to_numpy()].reset_index(drop=True)
    around = event_study(series["n"], p, -baseline[0], 0, baseline)
    reviews = around[around["offset"].between(*baseline)].groupby("event")["value"].sum()
    return p[reviews.reindex(p.index, fill_value=0).to_numpy() >= min_reviews].reset_index(
        drop=True
    )


def event_study(values: pd.Series, events: pd.DataFrame, before=14, after=14, baseline=(-14, -1)):
    """`values` (index appid, day) from `before` days before to `after` days after each event
    (columns appid, day): one row per event and offset with the value and its change from the
    event's baseline (mean over the offsets in `baseline`). Days without data are NaN.
    """
    events = events.reset_index(drop=True)
    offsets = np.arange(-before, after + 1)
    k = len(offsets)
    days = np.repeat(events["day"].to_numpy(), k) + np.tile(
        pd.to_timedelta(offsets, unit="D"), len(events)
    )
    index = pd.MultiIndex.from_arrays([np.repeat(events["appid"].to_numpy(), k), days])
    out = pd.DataFrame(
        {
            "event": np.repeat(events.index.to_numpy(), k),
            "offset": np.tile(offsets, len(events)),
            "value": values.reindex(index).to_numpy(),
        }
    )
    base = out[out["offset"].between(*baseline)].groupby("event")["value"].mean()
    out["change"] = out["value"] - out["event"].map(base)
    return out


def days_since(events: pd.DataFrame, index: pd.MultiIndex) -> pd.Series:
    """For each (appid, day) in `index`: days since the game's latest event (that day = 0),
    NaN before its first. A model feature, e.g. days since the last patch."""
    rows = index.to_frame(index=False).reset_index().sort_values("day")
    last = events[["appid", "day"]].rename(columns={"day": "event_day"}).sort_values("event_day")
    merged = pd.merge_asof(rows, last, left_on="day", right_on="event_day", by="appid")
    since = (merged["day"] - merged["event_day"]).dt.days
    return (
        pd.Series(since.to_numpy(), index=merged["index"].to_numpy()).sort_index().set_axis(index)
    )
