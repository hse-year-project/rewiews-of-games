from pathlib import Path

import psycopg

from src.config import ENV

SCHEMA = Path(__file__).with_name("schema.sql")


def connect():
    return psycopg.connect(
        host=ENV.pg_host,
        port=ENV.pg_port,
        dbname=ENV.pg_db,
        user=ENV.pg_user,
        password=ENV.pg_password.get_secret_value(),
        client_encoding="utf8",
    )


def init_db():
    with connect() as conn:
        conn.execute(SCHEMA.read_text(encoding="utf-8"))
