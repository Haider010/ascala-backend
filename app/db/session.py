from contextlib import contextmanager

import psycopg2

from app.core.config import get_settings


@contextmanager
def db_connection():
    database_url = get_settings().database_url
    if not database_url:
        raise RuntimeError("database_url is not configured.")

    conn = psycopg2.connect(database_url)
    try:
        yield conn
    finally:
        conn.close()
