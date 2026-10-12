import logging
from datetime import UTC, datetime

from src.collect import raw_store
from src.config import since
from src.db import review_state
from src.db.write import upsert

log = logging.getLogger(__name__)

SOURCE = "reviews"
URL = "https://store.steampowered.com/appreviews/{appid}"
LOG_EVERY = 10_000


def count(client, appid: int, language: str) -> int:
    """Steam's total number of reviews in this language."""
    params = {
        "json": 1,
        "language": language,
        "review_type": "all",
        "purchase_type": "all",
        "num_per_page": 0,
        "filter_offtopic_activity": 0,
    }
    return client.get_json(URL.format(appid=appid), params)["query_summary"]["total_reviews"]


def collect(client, conn, appid: int, language: str, cap: int | None = None) -> int:
    """Newest reviews, at most `cap` (None = all)."""
    state = review_state.get(conn, appid, language)
    watermark = state["watermark_updated_at"] if state else None
    stop_at = max(filter(None, [since(), watermark]), default=None)

    if state and state["status"] == "running" and state["last_cursor"]:
        cursor, n, newest = state["last_cursor"], state["n_items"], state["run_max_updated_at"]
        log.info("resuming %s [%s] at %d reviews", appid, language, n)
    else:
        cursor, n, newest = "*", 0, None
        review_state.start(conn, appid, language)

    url = URL.format(appid=appid)
    run_dir = raw_store.new_run_dir(SOURCE, appid, language)
    page_n = 0
    while True:
        params = {
            "json": 1,
            "filter": "updated",
            "language": language,
            "review_type": "all",
            "purchase_type": "all",
            "num_per_page": 100,
            "filter_offtopic_activity": 0,  # keep review-bombing periods
            "cursor": cursor,
        }
        data = client.get_json(url, params)
        reviews = data.get("reviews") or []
        if not reviews:
            break
        page_n += 1
        raw_store.save_page(run_dir, page_n, url, params, data)

        n += len(reviews)
        updated = [_ts(r["timestamp_updated"]) for r in reviews]
        newest = max(updated) if newest is None else max(newest, *updated)
        review_state.update(
            conn, appid, language, last_cursor=data["cursor"], n_items=n, run_max_updated_at=newest
        )
        if n // LOG_EVERY > (n - len(reviews)) // LOG_EVERY:
            log.info(
                "%s [%s]: %d reviews so far, now at %s", appid, language, n, min(updated).date()
            )
        if (stop_at and min(updated) < stop_at) or (cap and n >= cap) or data["cursor"] == cursor:
            break
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


def parse(conn) -> int:
    total = 0
    for appid, page in raw_store.iter_pages(SOURCE):
        data = page["response"]
        rows = [parse_review(r, appid, page["fetched_at"]) for r in data["reviews"]]
        total += upsert(conn, "core.reviews", rows, ["recommendation_id"])

        summary = data.get("query_summary", {})
        params = page["request"]["params"]
        if "total_reviews" in summary and "start_date" not in params:
            upsert(
                conn,
                "core.review_summaries",
                [
                    {
                        "appid": appid,
                        "language": params["language"],
                        "fetched_at": page["fetched_at"],
                        "total_reviews": summary["total_reviews"],
                        "total_positive": summary["total_positive"],
                        "total_negative": summary["total_negative"],
                        "review_score_desc": summary.get("review_score_desc"),
                    }
                ],
                ["appid", "language", "fetched_at"],
            )
    conn.commit()
    return total


def _count(n):
    """Steam sometimes sends 4294967295 (a count below zero, wrapped around): treat as unknown."""
    return n if n is None or n < 2**31 else None


def _ts(unix):
    return datetime.fromtimestamp(int(unix), UTC) if unix else None
