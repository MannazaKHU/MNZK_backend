import os

os.environ["APP_ENV"] = "test"
os.environ["AI_PROVIDER"] = "mock"
os.environ["MAPS_PROVIDER"] = "mock"
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


@pytest.fixture
def group(client):
    created = client.post("/api/meetings", json={
        "host_name": "방장", "candidate_dates": ["2026-10-28"],
        "time_range": {"start": "10:00", "end": "14:00"},
    })
    assert created.status_code == 201, created.text
    host = created.json()
    mid = host["meeting"]["id"]
    joined = client.post(f"/api/meetings/{mid}/members", json={
        "name": "참여자", "invite_code": host["meeting"]["invite_code"],
    })
    assert joined.status_code == 201, joined.text
    return {
        "id": mid, "host": host, "guest": joined.json(),
        "url": f"/api/meetings/{mid}",
        "hh": {"Authorization": f"Bearer {host['access_token']}"},
        "gh": {"Authorization": f"Bearer {joined.json()['access_token']}"},
    }
