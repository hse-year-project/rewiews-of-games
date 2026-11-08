import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

from tqdm.auto import tqdm

from src import files
from src.config import RAW_DIR
from src.db import parsed_pages

SECRET_PARAMS = {"key", "access_token"}
COMMIT_EVERY = 500


def new_run_dir(source: str, appid: int, language: str | None = None) -> Path:
    run = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return RAW_DIR.joinpath(source, str(appid), *([language] if language else []), run)


def save_page(run_dir: Path, n: int, url: str, params: dict | None, response):
    run_dir.mkdir(parents=True, exist_ok=True)
    page = {
        "request": {
            "url": url,
            "params": {k: "***" if k in SECRET_PARAMS else v for k, v in (params or {}).items()},
        },
        "fetched_at": datetime.now(UTC).isoformat(),
        "response": response,
    }
    path = run_dir / f"page_{n:05d}.json.gz"
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(page, f, ensure_ascii=False)
    files.replace(tmp, path)


def iter_pages(source: str, conn, full=False):
    """Yield (appid, page) for saved pages not parsed yet (all if full), oldest run first.

    After the caller has handled a page, it is recorded in meta.parsed_pages; the caller's
    writes and these records are committed together every COMMIT_EVERY pages.
    """
    done = set() if full else parsed_pages.get(conn, source)
    root = RAW_DIR / source
    pages = {p.relative_to(root).as_posix(): p for p in root.rglob("page_*.json.gz")}
    todo = sorted(
        (rel for rel in pages if rel not in done),
        key=lambda rel: (pages[rel].parent.name, pages[rel].name),
    )
    handled = []
    for rel in tqdm(todo, desc=f"parse {source}", unit="page", leave=False):
        with gzip.open(pages[rel], "rt", encoding="utf-8") as f:
            yield int(rel.split("/")[0]), json.load(f)
        handled.append(rel)
        if len(handled) == COMMIT_EVERY:
            _commit(conn, source, handled)
    _commit(conn, source, handled)


def _commit(conn, source, handled):
    parsed_pages.add(conn, source, handled)
    conn.commit()
    handled.clear()
