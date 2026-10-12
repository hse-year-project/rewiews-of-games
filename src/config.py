from datetime import UTC, date, datetime, time
from pathlib import Path

import yaml
from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"


class Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    pg_host: str = "localhost"
    pg_port: int = 5433
    pg_db: str = "steam"
    pg_user: str = "steam"
    pg_password: SecretStr = SecretStr("")
    steam_api_key: SecretStr | None = None


class Game(BaseModel):
    appid: int
    name: str
    group: str


class Http(BaseModel):
    host_rps: dict[str, float]
    max_retries: int


class Reviews(BaseModel):
    languages: list[str]
    since_date: date | None
    reviews_per_lang: int | None  # None or 0: all reviews


class News(BaseModel):
    page_size: int
    max_items: int


class StoreDetails(BaseModel):
    country_codes: list[str]


class Cleaning(BaseModel):
    min_chars: int
    junk_min_letter_ratio: float
    dup_min_chars: int
    lang_min_chars: int
    lang_min_confidence: float
    edited_min_delay_hours: int


class Config(BaseModel):
    games: list[Game]
    http: Http
    reviews: Reviews
    news: News
    store_details: StoreDetails
    cleaning: Cleaning


def since() -> datetime | None:
    d = CONFIG.reviews.since_date
    return datetime.combine(d, time(), UTC) if d else None


CONFIG = Config.model_validate(
    yaml.safe_load((ROOT / "configs" / "config.yaml").read_text(encoding="utf-8"))
)
ENV = Env()
