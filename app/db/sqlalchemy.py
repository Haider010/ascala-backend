from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

_engine = None
_SessionLocal = None


class Base(DeclarativeBase):
    pass


def get_engine():
    global _engine

    if _engine:
        return _engine

    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("database_url is not configured.")

    _engine = create_engine(settings.database_url, pool_pre_ping=True)
    return _engine


def get_session_factory():
    global _SessionLocal

    if not _SessionLocal:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False)

    return _SessionLocal


@contextmanager
def sqlalchemy_session() -> Session:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_sqlalchemy_tables() -> None:
    import app.escouade.models  # noqa: F401

    get_engine()
    Base.metadata.create_all(bind=_engine)
    with _engine.begin() as conn:
        conn.execute(text("ALTER TABLE escouade_batches ADD COLUMN IF NOT EXISTS batch_name TEXT"))
        conn.execute(text("ALTER TABLE escouade_batches ADD COLUMN IF NOT EXISTS source_type VARCHAR(80)"))
        conn.execute(text("ALTER TABLE escouade_batches ADD COLUMN IF NOT EXISTS source_label TEXT"))
        conn.execute(text("ALTER TABLE escouade_batches ADD COLUMN IF NOT EXISTS strategy_review JSONB NOT NULL DEFAULT '{}'::jsonb"))
