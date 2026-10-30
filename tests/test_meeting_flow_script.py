from contextlib import contextmanager
from pathlib import Path

import httpx

from app.config import settings
from app.database import get_session
from app.main import app
from scripts import check_meeting_flow as flow


def test_default_mock_is_isolated_and_restores_settings(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline flow must not create an outbound HTTP client")
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    names = ("APP_ENV", "AI_PROVIDER", "MAPS_PROVIDER", "ENABLE_BLOG_ENRICHMENT")
    before = {name: getattr(settings, name) for name in names}
    dependencies = dict(app.dependency_overrides)
    output = []
    with flow.mock_client() as client:
        session_iterator = app.dependency_overrides[get_session]()
        session = next(session_iterator)
        path = Path(session.get_bind().url.database)
        assert path.is_file()
        session_iterator.close()
        result = flow.run_flow(client, members=3, emit=output.append)
        assert result["progress"]["submitted_member_count"] == 3
        assert result["recommendations"]
    assert not path.exists()
    assert {name: getattr(settings, name) for name in names} == before
    assert app.dependency_overrides == dependencies
    assert "응답 1/3: waiting" in output and "응답 2/3: waiting" in output
    assert "응답 3/3: recommended" in output


def test_api_only_flow_does_not_print_tokens_or_invitation_codes(client):
    secrets = []
    class RecordingClient:
        def request(self, *args, **kwargs):
            response = client.request(*args, **kwargs)
            data = response.json()
            if "access_token" in data:
                secrets.append(data["access_token"])
                if "invite_code" in data["meeting"]:
                    secrets.append(data["meeting"]["invite_code"])
            return response
    output = []
    result = flow.run_flow(RecordingClient(), emit=output.append)
    assert result["status"] == "recommended" and result["recommendations"]
    assert secrets and all(value not in "\n".join(output) for value in secrets)
    assert "공통 시간·저장·재조회: OK" in output


def test_empty_result_has_distinct_exit_code(client, monkeypatch, capsys):
    from app.clients import naver_client
    async def no_route(*args):
        return None
    @contextmanager
    def provided_client():
        yield client
    monkeypatch.setattr(naver_client, "driving", no_route)
    monkeypatch.setattr(flow, "mock_client", provided_client)
    monkeypatch.setattr("sys.argv", ["check_meeting_flow"])
    assert flow.main() == 2
    assert "검증된 추천 없음" in capsys.readouterr().out
