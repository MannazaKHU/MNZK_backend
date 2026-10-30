import os

os.environ["APP_ENV"] = "test"
os.environ["AI_PROVIDER"] = "mock"
os.environ["MAPS_PROVIDER"] = "mock"
os.environ["ENABLE_BLOG_ENRICHMENT"] = "false"
os.environ["DATABASE_URL"] = "sqlite://"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.database import Base, build_engine, get_session
from app.main import app


@pytest.fixture
def database(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def client(database, monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "mock")
    monkeypatch.setattr(settings, "MAPS_PROVIDER", "mock")
    def sessions():
        with database() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()
