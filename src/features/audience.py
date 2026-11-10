import pandas as pd

from src.db import read


def audience_overlap() -> pd.DataFrame:
    """Every pair of games (a < b) with Steam accounts that reviewed both: shared,
    reviewers_a, reviewers_b, jaccard (shared / reviewed either), share_a (of a's reviewers,
    how many also reviewed b), share_b."""
    pairs = read.shared_reviewers()
    own = pairs[pairs["a"] == pairs["b"]].set_index("a")["n"]
    pairs = pairs[pairs["a"] != pairs["b"]].rename(columns={"n": "shared"}).reset_index(drop=True)
    pairs["reviewers_a"] = pairs["a"].map(own)
    pairs["reviewers_b"] = pairs["b"].map(own)
    pairs["jaccard"] = pairs["shared"] / (
        pairs["reviewers_a"] + pairs["reviewers_b"] - pairs["shared"]
    )
    pairs["share_a"] = pairs["shared"] / pairs["reviewers_a"]
    pairs["share_b"] = pairs["shared"] / pairs["reviewers_b"]
    return pairs


def neighbours(overlap: pd.DataFrame, top=3) -> pd.DataFrame:
    """Each game's `top` closest games by Jaccard: appid, other, jaccard, share (of appid's
    reviewers, how many also reviewed other)."""
    both = pd.concat(
        [
            overlap.rename(columns={"a": "appid", "b": "other", "share_a": "share"}),
            overlap.rename(columns={"b": "appid", "a": "other", "share_b": "share"}),
        ]
    )[["appid", "other", "jaccard", "share"]]
    return (
        both.sort_values("jaccard", ascending=False)
        .groupby("appid")
        .head(top)
        .sort_values(["appid", "jaccard"], ascending=[True, False], ignore_index=True)
    )
