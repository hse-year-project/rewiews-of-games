import numpy as np
import pandas as pd


def monthly_panel(series: pd.DataFrame, online: pd.DataFrame, counts=None) -> pd.DataFrame:
    """Per game and calendar month (index appid, month): n, n_positive, positive, players
    (mean online: SteamCharts' monthly averages before ~Aug 2026, mean of daily points after),
    plus the columns of `counts` (e.g. label_counts) summed. A game's first month and its last
    one (usually partial) are left out; months without online data have players NaN.

    series: daily_series; online: online_players(); counts: (appid, day) counts like series.
    """
    month = series.index.get_level_values("day").to_period("M").to_timestamp()
    appid = series.index.get_level_values("appid")
    panel = series[["n", "n_positive"]].groupby([appid, month]).sum()
    if counts is not None:
        counts_month = counts.index.get_level_values("day").to_period("M").to_timestamp()
        counts_appid = counts.index.get_level_values("appid")
        panel = panel.join(counts.groupby([counts_appid, counts_month]).sum())
    panel.index.names = ["appid", "month"]
    players = online.groupby(["appid", online["day"].dt.to_period("M").dt.to_timestamp()])
    panel = panel.join(players["players"].mean().rename_axis(["appid", "month"]))
    panel["positive"] = panel["n_positive"] / panel["n"].where(panel["n"] > 0)
    months = panel.index.get_level_values("month")
    by_game = pd.Series(months, index=panel.index).groupby(level="appid")
    inner = (months != by_game.transform("min")) & (months != by_game.transform("max"))
    return panel[inner.to_numpy()]


def month_changes(levels: pd.DataFrame) -> pd.DataFrame:
    """Change of each column from the game's previous month (index appid, month); NaN when the
    previous calendar month is missing. Use logs for counts, shares as they are."""
    months = levels.index.get_level_values("month")
    number = pd.Series(months.year * 12 + months.month, index=levels.index)
    consecutive = number.groupby(level="appid").diff() == 1
    return (levels - levels.groupby(level="appid").shift(1)).where(consecutive, np.nan)
