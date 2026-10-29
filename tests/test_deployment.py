import sys
from unittest.mock import Mock

import pytest

from app import database
from app.config import Settings


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+psycopg"])
def test_supabase_uri_uses_installed_driver_ssl_and_preserves_encoded_password(scheme):
    url = database.connection_url(
        f"{scheme}://postgres.test-project:p%40ss%3Aword%2F%23@aws-0.example.pooler.supabase.com:5432/postgres"
    )
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "p@ss:word/#"
    assert url.query["sslmode"] == "require"
    assert url.username == "postgres.test-project"


def test_supabase_cannot_silently_disable_ssl():
    with pytest.raises(ValueError):
        database.connection_url("postgresql://postgres:secret@db.example.supabase.co/postgres?sslmode=disable")


def test_local_postgres_does_not_require_cloud_ssl():
    assert "sslmode" not in database.connection_url("postgresql://user:pass@localhost/test").query


def test_invalid_database_url_does_not_leak_password():
    with pytest.raises(ValueError) as error:
        database.connection_url("not-a-uri secret-password")
    assert "secret-password" not in str(error.value)


def test_production_cannot_use_ephemeral_sqlite(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///data/test.sqlite3")
    with pytest.raises(ValueError, match="PostgreSQL"):
        Settings().validate()


def test_railway_host_and_port_are_used_and_predeploy_is_not_repeated(monkeypatch):
    from app import __main__ as entry
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db/test")
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.setenv("MAPS_PROVIDER", "mock")
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "12345")
    monkeypatch.setattr(entry, "settings", Settings())
    monkeypatch.setattr(sys, "argv", ["app", "--skip-migrations"])
    migrate = Mock()
    run = Mock()
    monkeypatch.setattr(entry.command, "upgrade", migrate)
    monkeypatch.setattr(entry.uvicorn, "run", run)
    entry.main()
    migrate.assert_not_called()
    assert run.call_args.kwargs["host"] == "0.0.0.0"
    assert run.call_args.kwargs["port"] == 12345
    assert run.call_args.kwargs["reload"] is False


def test_cloud_check_refuses_local_db_before_connecting(monkeypatch, capsys):
    from scripts import check_database
    from app.config import settings
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite://")
    monkeypatch.setattr(sys, "argv", ["check_database", "--supabase", "--migrate"])
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(database.engine, "connect", connect)
    assert check_database.main() == 1
    connect.assert_not_called()
    assert "No connection or migration attempted" in capsys.readouterr().out
