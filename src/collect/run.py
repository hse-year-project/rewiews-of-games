import logging
import math
import threading

import pandas as pd
from tqdm.auto import tqdm

from src.collect import news, player_counts, reviews, store_details
from src.collect.steam_client import SteamClient
from src.config import CONFIG, Game, load_games, save_games
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

    appids: any Steam app IDs (None = all games in configs/games.yaml); new ones are added there.
    per_lang: reviews per game x language; None = reviews_per_lang from config, 0 = all reviews.
    Asks Steam for each language's review total.
    """
    cap = CONFIG.reviews.reviews_per_lang if per_lang is None else per_lang
    cap = cap or None  # 0 / null = no limit
    sources = list(SOURCES) if sources == "all" else list(sources)
    client = SteamClient()
    games = resolve_games(client, appids)
    jobs = [
        (source, game, lang)
        for source in sources
        for game in games
        for lang in (CONFIG.reviews.languages if source == "reviews" else [None])
    ]

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


def resolve_games(client, appids) -> list[Game]:
    """Selected games from configs/games.yaml; unknown app IDs are looked up and added."""
    games = {g.appid: g for g in load_games()}
    changed = False
    for appid in appids or []:
        if appid not in games:
            games[appid] = Game(appid=appid, name=store_details.fetch_name(client, appid))
            log.warning("added %s (%s) to configs/games.yaml", games[appid].name, appid)
            changed = True
    if changed:
        save_games(list(games.values()))
    if appids is None:
        return sorted(games.values(), key=lambda g: g.appid)
    return [games[a] for a in appids]


class Stopped(Exception):
    """Raised inside collect() after its `stop` event is set."""


def collect(jobs: pd.DataFrame, stop: threading.Event | None = None) -> pd.DataFrame:
    """Run a plan(): download raw API responses into data/raw.

    stop: once set, collection ends after the current request; the rest of the jobs are
    skipped (a stopped review job continues from its cursor next time).
    """
    client = SteamClient()
    results = []
    with connect() as conn, tqdm(total=int(jobs["requests"].sum()), unit="req") as bar:

        def on_request(host):
            bar.set_postfix_str(f"{client.rate.get(host, 1.0):.2f} req/s", refresh=False)
            bar.update(1)
            if stop is not None and stop.is_set():
                raise Stopped

        client.on_request = on_request
        for job in jobs.itertuples(index=False):
            if stop is not None and stop.is_set():
                results.append({"items": 0, "status": "skipped", "error": None})
                continue
            label = f"{job.source} {job.game}" + (
                f" [{job.language}]" if job.source == "reviews" else ""
            )
            bar.set_description(label)
            done_before = bar.n
            try:
                if job.source == "reviews":
                    cap = None if pd.isna(job.cap) else int(job.cap)
                    total = int(job.steam_total)
                    n = reviews.collect(client, conn, job.appid, job.language, cap, total)
                else:
                    n = SOURCES[job.source].collect(client, job.appid)
                status, error = "done", None
            except Stopped:
                conn.rollback()
                n, status, error = 0, "stopped", None
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


def parse(sources="all", full=False) -> pd.DataFrame:
    """Load raw pages that are not parsed yet into Postgres (full: all pages again)."""
    sources = list(SOURCES) if sources == "all" else list(sources)
    with connect() as conn:
        rows = {name: SOURCES[name].parse(conn, full) for name in sources}
    return pd.DataFrame({"rows": rows})
