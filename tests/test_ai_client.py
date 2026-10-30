import asyncio
import json
import httpx
import pytest
from app.clients import ai_client as ai
from app.config import Settings, settings
from app.errors import ApiError

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def configure(monkeypatch, handler):
    monkeypatch.setattr(settings, "AI_PROVIDER", "chatkhu")
    monkeypatch.setattr(settings, "CHATKHU_BASE_URL", "https://gateway.example/v1/gateway")
    monkeypatch.setattr(settings, "CHATKHU_API_KEY", "test-key")
    monkeypatch.setattr(settings, "AI_MODEL", "account-model")
    monkeypatch.setattr(settings, "MAPS_PROVIDER", "mock")
    original = httpx.AsyncClient
    monkeypatch.setattr(ai.httpx, "AsyncClient",
                        lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def test_gateway_request_and_strict_output(monkeypatch):
    captured = []
    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        })
    configure(monkeypatch, handler)
    result = asyncio.run(ai.structured("system", "user", SCHEMA, step="test"))
    assert result == {"ok": True}
    req = captured[0]
    assert str(req.url) == "https://gateway.example/v1/gateway/chat/completions/"
    assert req.headers["Authorization"] == "Bearer test-key"
    body = json.loads(req.content)
    assert body["model"] == "account-model"
    assert "temperature" not in body
    assert "responseFormat" not in body
    schema = body["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert "additionalProperties" not in SCHEMA


@pytest.mark.parametrize("content", ['{"ok": "yes"}', '{"ok": true, "extra": 1}', '[]', 'not json'])
def test_invalid_ai_output_is_controlled_error(monkeypatch, content):
    configure(monkeypatch, lambda r: httpx.Response(200, json={
        "choices": [{"message": {"content": content}}]
    }))
    with pytest.raises(ApiError) as error:
        asyncio.run(ai.structured("s", "u", SCHEMA, step="test"))
    assert error.value.status_code == 502


def test_invalid_structured_output_is_retried_once(monkeypatch):
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        content = '{"ok": "yes"}' if calls == 1 else '{"ok": true}'
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        })
    configure(monkeypatch, handler)

    result = asyncio.run(ai.structured("system", "long user preference", SCHEMA, step="test"))

    assert result == {"ok": True}
    assert calls == 2


@pytest.mark.parametrize("response", [
    httpx.Response(401, text="secret test-key"),
    httpx.Response(200, json={"choices": []}),
    httpx.Response(200, json={"choices": [{"message": None}]}),
    httpx.Response(200, json=[]),
    httpx.Response(200, json={"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}),
])
def test_upstream_failures_hide_credentials(monkeypatch, response):
    configure(monkeypatch, lambda r: response)
    with pytest.raises(ApiError) as error:
        asyncio.run(ai.chat("s", "u", step="test"))
    assert error.value.status_code == 502
    assert "test-key" not in str(error.value.detail)


@pytest.mark.parametrize("mode, expected", [("true", True), ("1", True), ("false", False), ("0", False)])
def test_legacy_mock_values(monkeypatch, mode, expected):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.setenv("MOCK_MODE", mode)
    assert Settings().MOCK_MODE is expected


def test_missing_gateway_settings_fail_before_serving(monkeypatch):
    config = Settings()
    config.AI_PROVIDER = "chatkhu"
    config.CHATKHU_API_KEY = ""
    with pytest.raises(ValueError):
        config.validate()


def test_missing_usage_counts_do_not_break_valid_completion(monkeypatch):
    configure(monkeypatch, lambda r: httpx.Response(200, json={
        "choices": [{"message": {"content": "OK"}}], "usage": {"prompt_tokens": None},
    }))
    assert asyncio.run(ai.chat("s", "u", step="test")) == "OK"
