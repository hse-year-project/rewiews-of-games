import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

from tqdm.auto import tqdm

from src.config import RAW_DIR

SECRET_PARAMS = {"key", "access_token"}


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
    tmp.replace(path)


def iter_pages(source: str):
    """Yield (appid, page) for all saved pages, oldest run first so newer data wins."""
    paths = sorted(
        (RAW_DIR / source).rglob("page_*.json.gz"), key=lambda p: (p.parent.name, p.name)
    )
    for path in tqdm(paths, desc=f"parse {source}", unit="page", leave=False):
        appid = int(path.relative_to(RAW_DIR / source).parts[0])
        with gzip.open(path, "rt", encoding="utf-8") as f:
            yield appid, json.load(f)
