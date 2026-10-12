import logging
import math

import pandas as pd
from tqdm.auto import tqdm

from src.collect import news, player_counts, reviews, store_details
from src.collect.steam_client import SteamClient
from src.config import CONFIG
from src.db.connection import connect

log = logging.getLogger(__name__)

SOURCES = {
    "store_details": store_details,
    "reviews": reviews,
    "news": news,
    "player_counts": player_counts,
}


def plan(sources="all", appids=None, per_lang=None) -> pd.DataFrame:
    """One row per job (source x game x language) with the expected number of requests.

    per_lang: reviews per game x language; None = reviews_per_lang from config, 0 = all reviews.
    Asks Steam for each language's review total.
    """
    cap = CONFIG.reviews.reviews_per_lang if per_lang is None else per_lang
    cap = cap or None  # 0 / null = no limit
    sources = list(SOURCES) if sources == "all" else list(sources)
    games = [g for g in CONFIG.games if appids is None or g.appid in appids]
    jobs = [
        (source, game, lang)
        for source in sources
        for game in games
        for lang in (CONFIG.reviews.languages if source == "reviews" else [None])
    ]

    client = SteamClient()
    rows = []
    for source, game, lang in tqdm(jobs, desc="counting reviews on Steam", unit="job"):
        row = {"source": source, "appid": game.appid, "game": game.name, "language": lang}
        if source == "reviews":
            total = reviews.count(client, game.appid, lang)
            wanted = total if cap is None else min(total, cap)
            row |= {"steam_total": total, "cap": cap, "requests": math.ceil(wanted / 100)}
        else:
            row["requests"] = estimate_requests(source)
        rows.append(row)
    jobs = pd.DataFrame(rows)
    for col in ("steam_total", "cap"):
        if col in jobs:
            jobs[col] = jobs[col].astype("Int64")
    return jobs


def collect(jobs: pd.DataFrame) -> pd.DataFrame:
    """Run a plan(): download raw API responses into data/raw."""
    client = SteamClient()
    results = []
    with connect() as conn, tqdm(total=int(jobs["requests"].sum()), unit="req") as bar:
        client.on_request = lambda: bar.update(1)
        for job in jobs.itertuples(index=False):
            label = f"{job.source} {job.game}" + (
                f" [{job.language}]" if job.source == "reviews" else ""
            )
            bar.set_description(label)
            done_before = bar.n
            try:
                if job.source == "reviews":
                    cap = None if pd.isna(job.cap) else int(job.cap)
                    n = reviews.collect(client, conn, job.appid, job.language, cap)
                else:
                    n = SOURCES[job.source].collect(client, job.appid)
                status, error = "done", None
            except Exception as e:
                conn.rollback()
                n, status, error = 0, "failed", repr(e)
                log.exception("%s failed", label)
            bar.total += (bar.n - done_before) - job.requests
            bar.refresh()
            results.append({"items": n, "status": status, "error": error})
    return pd.concat([jobs.reset_index(drop=True), pd.DataFrame(results)], axis=1)


def estimate_requests(source) -> int:
    """Upper bound of requests for a non-review job."""
    if source == "store_details":
        return len(CONFIG.store_details.country_codes)
    if source == "news":
        return math.ceil(CONFIG.news.max_items / CONFIG.news.page_size)
    return 1


def parse(sources="all") -> pd.DataFrame:
    sources = list(SOURCES) if sources == "all" else list(sources)
    with connect() as conn:
        rows = {name: SOURCES[name].parse(conn) for name in sources}
    return pd.DataFrame({"rows": rows})
