import json
import logging
import time
from datetime import UTC, datetime

from src.collect import raw_store
from src.config import since
from src.db import review_state
from src.db.write import mark_for_cleaning, upsert

log = logging.getLogger(__name__)

SOURCE = "reviews"
URL = "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/"
LOG_EVERY = 10_000
STALL_RETRIES = 3


def query(appid, language, cursor="*", num_per_page=100) -> dict:
    """Request params: GetAppReviews takes everything as one JSON value."""
    return {
        "input_json": json.dumps(
            {
                "appid": appid,
                "filter": 2,
                "languages": [language],
                "review_type": 0,
                "purchase_type": 1,
                "num_per_page": num_per_page,
                "filter_offtopic_activity": False,
                "cursor": cursor,
            }
        )
    }


def count(client, appid: int, language: str) -> int:
    """Steam's total number of reviews in this language."""
    data = client.get_json(URL, query(appid, language, num_per_page=0))
    return data["response"]["total_matching"]


def collect(client, conn, appid, language, cap=None, total=None) -> int:
    """Newest reviews, at most `cap` (None = all). `total`: Steam's count, asked if not given."""
    if total is None:
        total = count(client, appid, language)
    state = review_state.get(conn, appid, language)
    if state:
        watermark = state["watermark_updated_at"]
    else:
        watermark = review_state.newest_collected(conn, appid, language)
    stop_at = max(filter(None, [since(), watermark]), default=None)

    if state and state["status"] == "running" and state["last_cursor"]:
        cursor, n, newest = state["last_cursor"], state["n_items"], state["run_max_updated_at"]
        log.info("resuming %s [%s] at %d reviews", appid, language, n)
    else:
        cursor, n, newest = "*", 0, None
        review_state.start(conn, appid, language)

    run_dir = raw_store.new_run_dir(SOURCE, appid, language)
    page_n = stalls = 0
    while True:
        params = query(appid, language, cursor)
        page = client.get_json(URL, params)
        data = page["response"]
        reviews = data.get("reviews") or []
        if reviews:
            page_n += 1
            raw_store.save_page(run_dir, page_n, URL, params, page)
            n += len(reviews)
            updated = [_ts(r["timestamp_updated"]) for r in reviews]
            newest = max(updated) if newest is None else max(newest, *updated)
            review_state.update(
                conn,
                appid,
                language,
                last_cursor=data["cursor"],
                n_items=n,
                run_max_updated_at=newest,
            )
            if n // LOG_EVERY > (n - len(reviews)) // LOG_EVERY:
                log.info(
                    "%s [%s]: %d / %d reviews, now at %s",
                    appid,
                    language,
                    n,
                    total,
                    min(updated).date(),
                )
            if (stop_at and min(updated) < stop_at) or (cap and n >= cap):
                break

        if not reviews or data["cursor"] == cursor:
            if n >= total - max(10, total // 100):
                break
            stalls += 1
            if stalls > STALL_RETRIES:
                raise RuntimeError(
                    f"Steam stopped returning reviews after {n} of {total}; re-run to resume"
                )
            log.warning(
                "%s [%s]: empty page at %d of %d, retry in %d min",
                appid,
                language,
                n,
                total,
                stalls,
            )
            time.sleep(60 * stalls)
            continue
        stalls = 0
        cursor = data["cursor"]

    review_state.update(
        conn,
        appid,
        language,
        status="done",
        finished_at=datetime.now(UTC),
        watermark_updated_at=newest or watermark,
    )
    return n


def parse_review(r: dict, appid: int, fetched_at: str) -> dict:
    a = r.get("author", {})
    return {
        "recommendation_id": int(r["recommendationid"]),
        "appid": appid,
        "language": r["language"],
        "review": r.get("review"),
        "voted_up": r["voted_up"],
        "votes_up": _count(r.get("votes_up")),
        "votes_funny": _count(r.get("votes_funny")),
        "weighted_vote_score": float(r.get("weighted_vote_score") or 0),
        "comment_count": _count(r.get("comment_count")),
        "steam_purchase": r.get("steam_purchase"),
        "received_for_free": r.get("received_for_free"),
        "refunded": r.get("refunded"),
        "written_during_early_access": r.get("written_during_early_access"),
        "primarily_steam_deck": r.get("primarily_steam_deck"),
        "created_at": _ts(r["timestamp_created"]),
        "updated_at": _ts(r["timestamp_updated"]),
        "author_steamid": int(a["steamid"]) if a.get("steamid") else None,
        "author_num_games_owned": a.get("num_games_owned"),
        "author_num_reviews": a.get("num_reviews"),
        "author_playtime_forever": a.get("playtime_forever"),
        "author_playtime_at_review": a.get("playtime_at_review"),
        "author_playtime_last_two_weeks": a.get("playtime_last_two_weeks"),
        "author_last_played": _ts(a.get("last_played")),
        "fetched_at": fetched_at,
    }


def page_contents(page) -> tuple[dict, dict]:
    """(data, request) of a raw page; reads both the new and the retired /appreviews format."""
    response, params = page["response"], page["request"]["params"]
    if "input_json" in params:
        request = json.loads(params["input_json"])
        return response["response"], request | {"language": request["languages"][0]}
    return response, params


def parse(conn, full=False) -> int:
    total = 0
    for appid, page in raw_store.iter_pages(SOURCE, conn, full):
        data, request = page_contents(page)
        rows = [parse_review(r, appid, page["fetched_at"]) for r in data.get("reviews", [])]
        total += upsert(conn, "core.reviews", rows, ["recommendation_id"], newer="fetched_at")
        mark_for_cleaning(conn, [row["recommendation_id"] for row in rows])

        summary = data.get("query_summary", {})
        if "total_reviews" in summary and "start_date" not in request:
            upsert(
                conn,
                "core.review_summaries",
                [
                    {
                        "appid": appid,
                        "language": request["language"],
                        "fetched_at": page["fetched_at"],
                        "total_reviews": summary["total_reviews"],
                        "total_positive": summary["total_positive"],
                        "total_negative": summary["total_negative"],
                        "review_score_desc": summary.get("review_score_desc"),
                    }
                ],
                ["appid", "language", "fetched_at"],
            )
    return total


def _count(n):
    """Steam sometimes sends 4294967295 (a count below zero, wrapped around): treat as unknown."""
    return n if n is None or n < 2**31 else None


def _ts(unix):
    return datetime.fromtimestamp(int(unix), UTC) if unix else None
