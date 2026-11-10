from datetime import UTC, date, datetime, time
from pathlib import Path

import yaml
from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
EMBEDDINGS_DIR = ROOT / "data" / "embeddings"
TOPICS_DIR = ROOT / "data" / "models" / "bertopic"
FEATURES_DIR = ROOT / "data" / "features"
TOPIC_SHORT, TOPIC_NOISE = -2, -1  # special topic ids: too short to have a topic, no clear topic


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


class Http(BaseModel):
    host_rps: dict[str, float]
    max_rps: float
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
    short_chars: int
    short_han: int


class Config(BaseModel):
    http: Http
    reviews: Reviews
    news: News
    store_details: StoreDetails
    cleaning: Cleaning


GAMES_FILE = ROOT / "configs" / "games.yaml"


def load_games() -> list[Game]:
    games: dict[int, Game] = {}
    for entry in yaml.safe_load(GAMES_FILE.read_text(encoding="utf-8")) or []:
        game = Game(**entry)
        if game.appid in games and games[game.appid] != game:
            raise ValueError(
                f"configs/games.yaml lists appid {game.appid} twice with different values: "
                f"{games[game.appid]} / {game} (e.g. after a git merge); keep one line"
            )
        games[game.appid] = game
    return sorted(games.values(), key=lambda g: g.appid)


def game_names() -> dict[int, str]:
    return {g.appid: g.name for g in load_games()}


def save_games(games: list[Game]):
    lines = [
        yaml.safe_dump(
            [g.model_dump()],
            default_flow_style=None,
            sort_keys=False,
            allow_unicode=True,
            width=10_000,
        )
        for g in sorted(games, key=lambda g: g.appid)
    ]
    GAMES_FILE.write_text("".join(lines), encoding="utf-8")


def since() -> datetime | None:
    d = CONFIG.reviews.since_date
    return datetime.combine(d, time(), UTC) if d else None


CONFIG = Config.model_validate(
    yaml.safe_load((ROOT / "configs" / "config.yaml").read_text(encoding="utf-8"))
)
ENV = Env()
