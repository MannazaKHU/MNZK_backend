"""Request-scoped SQLAlchemy sessions; Alembic manages schema changes."""
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.exc import ArgumentError
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings


class Base(DeclarativeBase):
    pass


def connection_url(url: str):
    """Accept dashboard PostgreSQL URIs with the installed synchronous psycopg driver."""
    try:
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        parsed = make_url(url)
        if parsed.drivername == "postgresql":
            parsed = parsed.set(drivername="postgresql+psycopg")
        host = (parsed.host or "").lower()
        if host.endswith((".supabase.co", ".supabase.com")):
            mode = parsed.query.get("sslmode", "require")
            if mode not in {"require", "verify-ca", "verify-full"}:
                raise ValueError("Supabase connections require SSL")
            parsed = parsed.update_query_dict({"sslmode": mode})
        return parsed
    except (ArgumentError, ValueError):
        # Invalid URLs may contain passwords; do not include the source URL in the error.
        raise ValueError("Invalid DATABASE_URL. Use an encoded PostgreSQL connection URI; Supabase requires SSL.") from None


def build_engine(url: str, **kwargs):
    parsed = connection_url(url)
    if settings.APP_ENV == "production" and parsed.get_backend_name() != "postgresql":
        raise ValueError("Production requires a PostgreSQL DATABASE_URL")
    if parsed.get_backend_name() == "sqlite":
        if parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        kwargs.setdefault("connect_args", {"check_same_thread": False})
    elif parsed.drivername == "postgresql+psycopg":
        kwargs.setdefault("pool_size", settings.DB_POOL_SIZE)
        kwargs.setdefault("max_overflow", settings.DB_MAX_OVERFLOW)
        kwargs.setdefault("pool_recycle", 300)
        kwargs.setdefault("connect_args", {"connect_timeout": settings.DB_CONNECT_TIMEOUT,
                                           "prepare_threshold": None})
    engine = create_engine(parsed, pool_pre_ping=True, **kwargs)
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
