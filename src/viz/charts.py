import textwrap

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.ticker import EngFormatter, PercentFormatter

from src.config import game_names

CJK_FONTS = ("Noto Sans CJK SC", "WenQuanYi Zen Hei", "Microsoft YaHei")
# fixed color per language (identity follows the entity, not the rank)
LANG_COLORS = {
    "english": "#2a78d6",
    "russian": "#eb6834",
    "schinese": "#1baf7a",
    "tchinese": "#eda100",
}
SERIES_COLOR = "#2a78d6"
PATCH_COLOR = "#3c3c3c"
STYLE = {
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": "#e5e5e5",
    "grid.linewidth": 0.8,
    "lines.linewidth": 2,
    "figure.figsize": (11, 4),
}


def use_style():
    """Chart style of the EDA notebooks, with Chinese text support."""
    plt.rcParams.update(STYLE)
    use_cjk_font()


def use_cjk_font():
    """Let matplotlib draw Chinese text (otherwise it shows boxes).

    Needs a CJK font: Arch noto-fonts-cjk, Ubuntu/WSL fonts-noto-cjk. Installed it after
    matplotlib? Delete ~/.cache/matplotlib so matplotlib finds it.
    """
    installed = {f.name for f in font_manager.fontManager.ttflist}
    plt.rcParams["font.family"] = ["DejaVu Sans", *[f for f in CJK_FONTS if f in installed][:1]]


def date_axis(ax):
    locator = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))


def per_game_panels(
    data, title: str, ylabel: str, patches=None, ylim=None, percent=False, log=False
):
    """One panel per game. data: Series, or DataFrame for several lines, with index (appid, day);
    NaN breaks the line. patches: (appid, day) index of patch days, drawn as vertical lines."""
    names = game_names()
    appids = list(dict.fromkeys(data.index.get_level_values(0)))
    if not appids:
        print(f"{title}: no data yet")
        return
    rows = (len(appids) + 1) // 2
    fig, axes = plt.subplots(rows, 2, figsize=(12, 2.4 * rows), squeeze=False)
    for ax, appid in zip(axes.flat, appids, strict=False):
        d = data.loc[appid]
        if patches is not None:
            for day in patches[patches.get_level_values(0) == appid].get_level_values(1):
                if d.index.min() <= day <= d.index.max():
                    ax.axvline(day, color=PATCH_COLOR, linewidth=0.8, alpha=0.5, zorder=0)
        if isinstance(d, pd.Series):
            ax.plot(d.index, d.values, color=SERIES_COLOR)
        else:
            for column in d.columns[d.notna().any()]:
                ax.plot(d.index, d[column], label=textwrap.shorten(str(column), 40))
            ax.legend(fontsize=7, frameon=False, loc="upper left")
        ax.set_title(names.get(appid, appid), loc="left", fontsize=10)
        ax.set_ylabel(ylabel)
        if log:
            ax.set_yscale("log")
        ax.yaxis.set_major_formatter(PercentFormatter(1.0) if percent else EngFormatter(sep=""))
        date_axis(ax)
        if ylim:
            ax.set_ylim(*ylim)
    for ax in axes.flat[len(appids) :]:
        ax.set_visible(False)
    if patches is not None:
        patch_handle = Line2D([], [], color=PATCH_COLOR, linewidth=0.8, label="patch / update")
        fig.legend(handles=[patch_handle], loc="upper right", frameon=False)
    fig.suptitle(title, x=0.01, ha="left")
    fig.tight_layout()


def with_gaps(s, freq="W-MON"):
    """(appid, day) series -> NaN for missing periods, so lines break instead of bridging gaps."""
    return s.groupby(level=0, group_keys=True).apply(lambda g: g.droplevel(0).asfreq(freq))
