from contextlib import contextmanager
from threading import Lock

from psycopg2.pool import ThreadedConnectionPool

from app.core.config import get_settings

_pool = None
_pool_lock = Lock()


def _get_pool() -> ThreadedConnectionPool:
    global _pool

    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("database_url is not configured.")

    if _pool:
        return _pool

    with _pool_lock:
        if not _pool:
            _pool = ThreadedConnectionPool(
                minconn=settings.db_pool_min_connections,
                maxconn=settings.db_pool_max_connections,
                dsn=settings.database_url,
            )
    return _pool


def warm_db_pool() -> None:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        pass
    finally:
        pool.putconn(conn)


@contextmanager
def db_connection():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
    except Exception:
        if not conn.closed:
            conn.rollback()
        raise
    finally:
        if not conn.closed:
            conn.rollback()
            pool.putconn(conn)
