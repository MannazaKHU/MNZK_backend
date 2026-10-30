"""NAVER API HUB Blog Search. Never fetch blog bodies or follow result URLs."""
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import httpx

from app.clients.naver_client import _clean
from app.config import settings

class BlogSearchError(Exception):
    """Only a public, predefined code may cross the optional enrichment boundary."""
    def __init__(self, code, *, http_status=None):
        self.code = code
        self.http_status = http_status
        super().__init__(code)


def url_key(url):
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            return None
        # Dedup only. The response always keeps the original API-provided URL.
        query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith('utm_')]
        host = parts.netloc.lower().replace('m.blog.naver.com', 'blog.naver.com')
        return urlunsplit(('https', host, parts.path.rstrip('/'), urlencode(sorted(query)), ''))
    except ValueError:
        return None


async def search_blogs(query, display):
    if not settings.NAVER_CLIENT_ID.strip() or not settings.NAVER_CLIENT_SECRET.strip():
        raise BlogSearchError('BLOG_KEYS_MISSING')
    display = min(100, max(1, display))
    try:
        async with httpx.AsyncClient(timeout=settings.BLOG_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = await client.get(settings.NAVER_BLOG_URL, params={
                'query': query, 'display': display, 'start': 1, 'sort': 'sim', 'format': 'json',
            }, headers={'X-NCP-APIGW-API-KEY-ID': settings.NAVER_CLIENT_ID,
                        'X-NCP-APIGW-API-KEY': settings.NAVER_CLIENT_SECRET,
                        'Accept': 'application/json'})
            if response.status_code != 200:
                raise BlogSearchError('BLOG_RATE_LIMIT' if response.status_code == 429 else
                                      'BLOG_AUTH_ERROR' if response.status_code in {401, 403} else 'BLOG_SEARCH_FAILED',
                                      http_status=response.status_code)
            try:
                payload = response.json()
            except ValueError:
                raise BlogSearchError('BLOG_INVALID_RESPONSE') from None
            items = payload.get('items') if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise BlogSearchError('BLOG_INVALID_RESPONSE')
    except httpx.TimeoutException:
        raise BlogSearchError('BLOG_TIMEOUT') from None
    except (httpx.HTTPError, ValueError, AttributeError):
        raise BlogSearchError('BLOG_SEARCH_FAILED') from None
    fetched_at = datetime.now(timezone.utc).isoformat()
    results, seen = [], set()
    for item in items[:display]:
        if not isinstance(item, dict):
            continue
        url = item.get('link')
        key = url_key(url)
        if not key or key in seen or len(url) > 4000:
            continue
        seen.add(key)
        # Clean only markup; quotations are checked against these exact API fields.
        results.append({'url': url, 'title': _clean(str(item.get('title') or ''))[:1000],
                        'description': _clean(str(item.get('description') or ''))[:4000],
                        'postdate': str(item.get('postdate') or '')[:30], 'fetched_at': fetched_at})
    return results
