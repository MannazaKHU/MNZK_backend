import asyncio

import httpx
import pytest

from app.clients import blog_client
from app.config import settings
from scripts.check_naver import run_checks


def transport(monkeypatch, handler):
    monkeypatch.setattr(settings, "NAVER_CLIENT_ID", "hub-id")
    monkeypatch.setattr(settings, "NAVER_CLIENT_SECRET", "hub-secret")
    monkeypatch.setattr(settings, "NAVER_BLOG_URL", "https://naverapihub.apigw.ntruss.com/search/v1/blog")
    original = httpx.AsyncClient
    monkeypatch.setattr(blog_client.httpx, "AsyncClient",
                        lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def test_api_hub_headers_endpoint_and_normalization(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"items": [
            dict(title="<b>가게</b> &amp; 후기", description="<b>조용한</b> 곳", postdate="20261030",
                 link="https://blog.naver.com/test/1"),
            dict(title="중복", link="https://m.blog.naver.com/test/1?utm_source=test"),
            dict(title="잘못된 링크", link="javascript:alert(1)"),
        ]})
    transport(monkeypatch, handler)
    docs = asyncio.run(blog_client.search_blogs("가게 분위기", 5))
    assert len(docs) == 1
    assert docs[0]["title"] == "가게 & 후기" and docs[0]["description"] == "조용한 곳"
    assert docs[0]["postdate"] == "20261030" and docs[0]["fetched_at"]
    request = requests[0]
    assert request.url.host == "naverapihub.apigw.ntruss.com"
    assert request.url.path == "/search/v1/blog"
    assert request.headers["X-NCP-APIGW-API-KEY-ID"] == "hub-id"
    assert request.headers["X-NCP-APIGW-API-KEY"] == "hub-secret"
    assert "x-naver-client-id" not in request.headers
    assert dict(request.url.params) == dict(query="가게 분위기", display="5", start="1", sort="sim", format="json")


@pytest.mark.parametrize("status, code", [(401, "BLOG_AUTH_ERROR"), (403, "BLOG_AUTH_ERROR"),
                                          (429, "BLOG_RATE_LIMIT"), (503, "BLOG_SEARCH_FAILED")])
def test_upstream_errors_do_not_leak_credentials(monkeypatch, status, code):
    transport(monkeypatch, lambda r: httpx.Response(status, text="hub-secret private upstream body"))
    with pytest.raises(blog_client.BlogSearchError) as error:
        asyncio.run(blog_client.search_blogs("가게", 1))
    assert error.value.code == code and error.value.http_status == status
    assert "secret" not in str(error.value) and "private" not in str(error.value)


@pytest.mark.parametrize("response", [httpx.Response(200, text="invalid json"),
                                      httpx.Response(200, json=[]), httpx.Response(200, json={"items": None})])
def test_invalid_json_and_shape_are_controlled(monkeypatch, response):
    transport(monkeypatch, lambda r: response)
    with pytest.raises(blog_client.BlogSearchError) as error:
        asyncio.run(blog_client.search_blogs("가게", 1))
    assert error.value.code == "BLOG_INVALID_RESPONSE"


def test_timeout_and_missing_credentials(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("hub-secret", request=request)
    transport(monkeypatch, handler)
    with pytest.raises(blog_client.BlogSearchError) as error:
        asyncio.run(blog_client.search_blogs("가게", 1))
    assert error.value.code == "BLOG_TIMEOUT"
    monkeypatch.setattr(settings, "NAVER_CLIENT_SECRET", "")
    with pytest.raises(blog_client.BlogSearchError) as error:
        asyncio.run(blog_client.search_blogs("가게", 1))
    assert error.value.code == "BLOG_KEYS_MISSING"


def test_blog_smoke_uses_existing_keys_and_accepts_empty_results(monkeypatch, capsys):
    transport(monkeypatch, lambda r: httpx.Response(200, json={"items": []}))
    assert asyncio.run(run_checks(search=False, maps=False, blog=True)) == 0
    assert "NAVER API HUB Blog Search: OK" in capsys.readouterr().out


def test_blog_smoke_reports_http_auth_failure(monkeypatch, capsys):
    transport(monkeypatch, lambda r: httpx.Response(403, text="hub-secret"))
    assert asyncio.run(run_checks(search=False, maps=False, blog=True)) == 1
    output = capsys.readouterr().out
    assert "BLOG_AUTH_ERROR, HTTP 403" in output and "hub-secret" not in output
