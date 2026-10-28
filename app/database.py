"""Request-scoped SQLAlchemy sessions; Alembic manages schema changes."""
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings


class Base(DeclarativeBase):
    pass


def build_engine(url: str, **kwargs):
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        if parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        kwargs.setdefault("connect_args", {"check_same_thread": False})
    engine = create_engine(url, pool_pre_ping=True, **kwargs)
    if parsed.get_backend_name() == "sqlite":
        @event.listens_for(engine, "connect")
        def sqlite_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    return engine


engine = build_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session():
    with SessionLocal() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
