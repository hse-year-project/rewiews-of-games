from datetime import datetime

from src.collect import raw_store
from src.config import CONFIG
from src.db.write import upsert

SOURCE = "store_details"
URL = "https://store.steampowered.com/api/appdetails"


def collect(client, appid: int) -> int:
    run_dir = raw_store.new_run_dir(SOURCE, appid)
    for n, country in enumerate(CONFIG.store_details.country_codes, start=1):
        params = {"appids": appid, "cc": country, "l": "english"}
        raw_store.save_page(run_dir, n, URL, params, client.get_json(URL, params))
    return len(CONFIG.store_details.country_codes)


def fetch_name(client, appid: int) -> str:
    """The game's name on Steam (one small request); raises for an unknown appid."""
    entry = client.get_json(URL, {"appids": appid, "filters": "basic", "l": "english"})[str(appid)]
    if not entry["success"]:
        raise ValueError(f"appid {appid} not found on Steam")
    return entry["data"]["name"]


def parse_release_date(text: str):
    for fmt in ("%b %d, %Y", "%d %b, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def parse(conn, full=False) -> int:
    n = 0
    for appid, page in raw_store.iter_pages(SOURCE, conn, full):
        entry = page["response"][str(appid)]
        if not entry["success"]:
            continue
        d, country = entry["data"], page["request"]["params"]["cc"]
        game = {
            "appid": appid,
            "name": d["name"],
            "type": d["type"],
            "is_free": d["is_free"],
            "release_date": parse_release_date(d["release_date"]["date"]),
            "developers": d.get("developers", []),
            "publishers": d.get("publishers", []),
            "genres": [g["description"] for g in d.get("genres", [])],
            "categories": [c["description"] for c in d.get("categories", [])],
            "fetched_at": page["fetched_at"],
        }
        price = d.get("price_overview", {})
        price_row = {
            "appid": appid,
            "country_code": country,
            "currency": price.get("currency"),
            "price_initial": price.get("initial", 0),
            "price_final": price.get("final", 0),
            "discount_percent": price.get("discount_percent", 0),
            "fetched_at": page["fetched_at"],
        }
        n += upsert(conn, "core.games", [game], ["appid"], newer="fetched_at")
        upsert(conn, "core.game_prices", [price_row], ["appid", "country_code"], newer="fetched_at")
    return n
