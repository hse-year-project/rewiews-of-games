import re
from datetime import UTC, datetime, timedelta

from src.collect import raw_store
from src.config import CONFIG
from src.config import since as since_date
from src.db.write import upsert

SOURCE = "news"
URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"

PATCH_TITLE = re.compile(
    r"\b(patch(\s?notes?)?|hotfix(es)?|bug\s?fix(es)?|changelog|release notes)\b"
    r"|\bupdate\s+v?\d|\bv\d+\.\d+"
    r"|патч|обновлени[ея]\s+\d|исправлени[ея]"
    r"|补丁|更新公告|版本更新",
    re.IGNORECASE,
)


def collect(client, appid: int) -> int:
    since = since_date() - timedelta(days=30) if since_date() else None
    run_dir = raw_store.new_run_dir(SOURCE, appid)
    params = {"appid": appid, "count": CONFIG.news.page_size, "maxlength": 0}
    seen = set()
    page_n = 0
    while len(seen) < CONFIG.news.max_items:
        data = client.get_json(URL, params)
        items = data["appnews"]["newsitems"]
        if all(it["gid"] in seen for it in items):
            break
        seen.update(it["gid"] for it in items)
        page_n += 1
        raw_store.save_page(run_dir, page_n, URL, params, data)
        oldest = min(it["date"] for it in items)
        if since and datetime.fromtimestamp(oldest, UTC) < since:
            break
        params = {**params, "enddate": oldest}
    return len(seen)


def is_patch(item: dict) -> bool:
    return "patchnotes" in item.get("tags", []) or bool(PATCH_TITLE.search(item.get("title", "")))


def parse(conn) -> int:
    rows = []
    for appid, page in raw_store.iter_pages(SOURCE):
        for it in page["response"]["appnews"]["newsitems"]:
            rows.append(
                {
                    "gid": it["gid"],
                    "appid": appid,
                    "title": it.get("title"),
                    "url": it.get("url"),
                    "author": it.get("author"),
                    "contents": it.get("contents"),
                    "feedlabel": it.get("feedlabel"),
                    "feedname": it.get("feedname"),
                    "tags": it.get("tags", []),
                    "is_patch": is_patch(it),
                    "published_at": datetime.fromtimestamp(it["date"], UTC),
                }
            )
    upsert(conn, "core.news", rows, ["gid"])
    conn.commit()
    return len(rows)
