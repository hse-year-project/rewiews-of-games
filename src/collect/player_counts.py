import logging
from datetime import UTC, datetime

import httpx

from src.collect import raw_store
from src.db.write import upsert

log = logging.getLogger(__name__)

SOURCE = "player_counts"
URL = "https://steamcharts.com/app/{appid}/chart-data.json"


def collect(client, appid: int) -> int:
    url = URL.format(appid=appid)
    try:
        points = client.get_json(url)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            log.warning("no SteamCharts data for %s", appid)
            return 0
        raise
    raw_store.save_page(raw_store.new_run_dir(SOURCE, appid), 1, url, None, points)
    return len(points)


def parse_points(points: list, appid: int) -> list[dict]:
    return [
        {
            "appid": appid,
            "ts": datetime.fromtimestamp(ts_ms / 1000, UTC),
            "players": players,
        }
        for ts_ms, players in points
        if players is not None
    ]


def parse(conn) -> int:
    rows = []
    for appid, page in raw_store.iter_pages(SOURCE):
        rows += parse_points(page["response"], appid)
    upsert(conn, "core.player_counts", rows, ["appid", "ts"])
    conn.commit()
    return len(rows)
