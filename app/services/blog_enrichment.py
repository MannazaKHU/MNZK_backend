"""Collect bounded API HUB search batches for the existing evidence/scoring pipeline."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from app.clients import blog_client
from app.config import settings
from app.recommendation_contracts import BlogDocument, BlogSearchBatch
from app.services.recommendation_pipeline import EvidencePolicy, prepare_search_tasks


def configured_policy() -> EvidencePolicy:
    return EvidencePolicy(
        max_candidates=settings.BLOG_MAX_CANDIDATES,
        queries_per_candidate=settings.BLOG_QUERIES_PER_CANDIDATE,
        results_per_query=settings.BLOG_RESULTS_PER_QUERY,
        max_queries=settings.BLOG_MAX_REQUESTS,
        extraction_concurrency=settings.BLOG_CONCURRENCY,
        extraction_timeout_seconds=settings.BLOG_EXTRACTION_TIMEOUT_SECONDS,
        max_age_days=settings.BLOG_MAX_AGE_DAYS,
    )


@dataclass
class CollectedSearches:
    batches: list[BlogSearchBatch]
    request_count: int


async def collect_searches(places: list[dict], conditions: list[dict], policy: EvidencePolicy) -> CollectedSearches:
    """Only call search endpoints. Extract and score once in evaluate_supplied_inputs."""
    tasks, _, _ = prepare_search_tasks(places, conditions, policy)
    # One request per normalized query, even if multiple candidate places share it.
    queries = {}
    for task in tasks:
        queries.setdefault(task['query'].casefold(), task['query'])
    if not queries:
        return CollectedSearches([], 0)
    semaphore = asyncio.Semaphore(settings.BLOG_CONCURRENCY)
    request_count = 0

    async def search(key, query):
        nonlocal request_count
        async with semaphore:
            fetched = datetime.now(timezone.utc)
            if not settings.NAVER_CLIENT_ID.strip() or not settings.NAVER_CLIENT_SECRET.strip():
                return key, [], fetched, 'not_requested', 'keys_missing'
            request_count += 1
            try:
                rows = await asyncio.wait_for(blog_client.search_blogs(query, policy.results_per_query),
                                              timeout=settings.BLOG_TIMEOUT_SECONDS)
                documents = [BlogDocument(**{k: row[k] for k in ('title', 'description', 'url', 'postdate')})
                             for row in rows[:policy.results_per_query]]
                return key, documents, fetched, 'success', None
            except TimeoutError:
                return key, [], fetched, 'timeout', 'timeout'
            except blog_client.BlogSearchError as exc:
                status, failure = {
                    'BLOG_TIMEOUT': ('timeout', 'timeout'),
                    'BLOG_RATE_LIMIT': ('rate_limited', 'rate_limit'),
                    'BLOG_AUTH_ERROR': ('failed', 'auth_error'),
                    'BLOG_INVALID_RESPONSE': ('failed', 'invalid_response'),
                }.get(exc.code, ('failed', 'upstream_error'))
                return key, [], fetched, status, failure
            except Exception:
                return key, [], fetched, 'failed', 'upstream_error'

    results = await asyncio.gather(*(search(key, query) for key, query in queries.items()))
    fetched = {key: (docs, timestamp, status, failure) for key, docs, timestamp, status, failure in results}
    batches = []
    for task in tasks:
        docs, timestamp, status, failure = fetched[task['query'].casefold()]
        batches.append(BlogSearchBatch(place_id=task['place_id'], query=task['query'], status=status,
                                       fetched_at=timestamp, items=docs, failure_code=failure, data_origin='live'))
    return CollectedSearches(batches, request_count)
