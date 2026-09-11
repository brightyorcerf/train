"""Postgres (system of record, §7.6). Plain psycopg 3 + one schema file — no ORM."""
from pathlib import Path

import psycopg

from app.core.config import settings

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(**kw) -> psycopg.Connection:
    return psycopg.connect(host=settings.postgres_host, port=settings.postgres_port, dbname=settings.postgres_db,
                           user=settings.postgres_user, password=settings.postgres_password, **kw)


def init_schema() -> None:
    with connect() as c:
        c.execute(SCHEMA.read_text())
