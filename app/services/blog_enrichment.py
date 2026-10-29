# INACTIVE API DRAFT: preserved after scope change; not imported by the active recommendation path.
"""Bounded, request-local blog enrichment. Raw search results never enter stored results."""
import asyncio
import copy
import json
import math
from datetime import datetime, timezone

from app.clients import ai_client, blog_client
from app.config import settings
from app.services.blog_evidence import Extraction, SYSTEM, validate_claims


_NEUTRAL_TOPICS = {
    'kind': '메뉴 종류', 'cost': '메뉴 가격', 'atmosphere': '분위기',
    'environment': '공간 환경', 'accessibility': '편의 접근성', 'other': '이용 정보',
}


def topics_for(conditions):
    topics = []
    for c in conditions:
        target = (c['target'] + ' ' + c['raw_text']).lower()
        topic = '주차' if '주차' in target or 'parking' in target else _NEUTRAL_TOPICS[c['category']]
        if topic not in topics:
            topics.append(topic)
    return topics


def _configuration_error():
    limits = (settings.BLOG_MAX_CANDIDATES, settings.BLOG_QUERIES_PER_CANDIDATE,
              settings.BLOG_RESULTS_PER_QUERY, settings.BLOG_MAX_REQUESTS, settings.BLOG_CONCURRENCY)
    if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in limits):
        return 'BLOG_INVALID_CONFIG'
    if settings.BLOG_CONCURRENCY < 1 or not 1 <= settings.BLOG_RESULTS_PER_QUERY <= 100:
        return 'BLOG_INVALID_CONFIG'
    if settings.BLOG_MAX_AGE_DAYS < 0:
        return 'BLOG_INVALID_CONFIG'
    if any(not math.isfinite(t) or t <= 0 for t in (settings.BLOG_TIMEOUT_SECONDS, settings.BLOG_EXTRACTION_TIMEOUT_SECONDS)):
        return 'BLOG_INVALID_CONFIG'
    return None


async def extract(place, conditions, documents, schedule):
    # No raw participant prose, tokens, or origins are needed by this extraction step.
    payload = {'place': {k: place.get(k) for k in ('id', 'name', 'address')},
               'conditions': [{k: c[k] for k in ('id', 'category', 'target', 'alternatives', 'direction', 'scope', 'operator', 'value', 'unit')}
                              for c in conditions],
               'schedule': schedule,
               'untrusted_external_documents': [dict(document_index=i, title=d['title'], description=d['description'])
                                                for i, d in enumerate(documents)]}
    return await ai_client.structured(SYSTEM, json.dumps(payload, ensure_ascii=False),
                                      Extraction.model_json_schema(), step='blog_extract')


async def enrich_candidates(candidates, conditions, schedule):
    """All limits apply across this recommendation, not independently per coroutine."""
    from app.services.recommendation_rules import evaluate

    places = copy.deepcopy(candidates)
    report = {'enabled': settings.ENABLE_BLOG_ENRICHMENT, 'candidate_count': len(places),
              'selected_count': 0, 'performed_count': 0, 'search_requests': 0, 'unique_urls': 0,
              'selection_policy': 'center_distance_then_place_id', 'partial_coverage': False,
              'scope_note': '한도 내 검색된 후보·후기 정보 기준이며 전체 후보 중 절대 최적을 뜻하지 않습니다.',
              'failures': [], 'limits': {
                  'candidates': settings.BLOG_MAX_CANDIDATES, 'queries_per_candidate': settings.BLOG_QUERIES_PER_CANDIDATE,
                  'results_per_query': settings.BLOG_RESULTS_PER_QUERY, 'requests': settings.BLOG_MAX_REQUESTS,
                  'concurrency': settings.BLOG_CONCURRENCY, 'max_age_days': settings.BLOG_MAX_AGE_DAYS}}
    unresolved = {}
    for p in places:
        p['blog_evidence'] = []
        p['blog_enrichment'] = {'performed': False, 'status': 'disabled', 'search_status': 'not_attempted',
                                'search_requests': 0, 'document_count': 0, 'failures': [], 'rejected_claims': []}
        unresolved[p['id']] = [c for c in conditions if evaluate(c, p)['status'] == 'unknown' and not c['needs_confirmation']]
    eligible_for_enrichment = [p for p in places if unresolved[p['id']]]
    report['unresolved_candidate_count'] = len(eligible_for_enrichment)
    if not settings.ENABLE_BLOG_ENRICHMENT:
        return places, report
    error = _configuration_error()
    if not error and (not settings.NAVER_BLOG_CLIENT_ID or not settings.NAVER_BLOG_CLIENT_SECRET):
        error = 'BLOG_KEYS_MISSING'
    if not error and settings.AI_PROVIDER != 'chatkhu':
        error = 'BLOG_EXTRACTOR_UNAVAILABLE'
    if error:
        report['failures'] = [{'code': error}]
        report['partial_coverage'] = bool(eligible_for_enrichment)
        for p in places:
            p['blog_enrichment'].update(status='unavailable', failures=[{'code': error}])
        return places, report
    def selection_key(p):
        distance = p.get('distance_m')
        if not isinstance(distance, (float, int)) or not math.isfinite(distance):
            distance = float('inf')
        return distance, str(p['id'])
    selected = sorted(eligible_for_enrichment, key=selection_key)[:settings.BLOG_MAX_CANDIDATES]
    selected_ids = {p['id'] for p in selected}
    report['selected_count'] = len(selected)
    plans, query_owners = {}, {}
    for p in places:
        p['blog_enrichment']['status'] = 'not_needed' if not unresolved[p['id']] else 'not_selected'
    for p in selected:
        meta = p['blog_enrichment']
        meta['status'] = 'pending'
        # Name includes branch when available; address supplies region without guessing a branch.
        base = ' '.join((p.get('name', ''), ' '.join(p.get('address', '').split()[:3]))).strip()
        if not p.get('name'):
            meta.update(status='unavailable', failures=[{'code': 'PLACE_IDENTITY_MISSING'}])
            continue
        topics = topics_for(unresolved[p['id']])
        # Batch all topics into at most N neutral queries, rather than one request per condition.
        slots = min(len(topics), settings.BLOG_QUERIES_PER_CANDIDATE)
        groups = [topics[i::slots] for i in range(slots)] if slots else []
        for group in groups:
            query = ' '.join((base, *group))
            key = ' '.join(query.split()).casefold()
            if key not in plans:
                if len(plans) >= settings.BLOG_MAX_REQUESTS:
                    meta['failures'].append({'code': 'BLOG_REQUEST_LIMIT'})
                    continue
                plans[key] = query
            query_owners.setdefault(key, []).append(p['id'])
        if slots == 0:
            meta['failures'].append({'code': 'BLOG_REQUEST_LIMIT'})
    semaphore = asyncio.Semaphore(settings.BLOG_CONCURRENCY)
    async def search(key, query):
        async with semaphore:
            try:
                docs = await asyncio.wait_for(blog_client.search_blogs(query, settings.BLOG_RESULTS_PER_QUERY),
                                              timeout=settings.BLOG_TIMEOUT_SECONDS)
                return key, docs[:settings.BLOG_RESULTS_PER_QUERY], None
            except (TimeoutError, asyncio.TimeoutError):
                return key, [], 'BLOG_TIMEOUT'
            except blog_client.BlogSearchError as exc:
                return key, [], exc.code
            except Exception:
                # Optional subsystem: no upstream messages, keys or stack traces in responses.
                return key, [], 'BLOG_SEARCH_FAILED'
    fetched = await asyncio.gather(*(search(key, query) for key, query in plans.items()))
    report['search_requests'] = len(plans)
    documents, assigned, successes = {}, {p['id']: [] for p in selected}, {p['id']: 0 for p in selected}
    by_id = {p['id']: p for p in places}
    for key, docs, failure in fetched:
        for pid in query_owners[key]:
            meta = by_id[pid]['blog_enrichment']
            meta['performed'] = True
            meta['search_requests'] += 1
            if failure:
                meta['failures'].append({'code': failure})
            else:
                successes[pid] += 1
        for doc in docs:
            url = blog_client.url_key(doc.get('url'))
            if not url:
                continue
            documents.setdefault(url, doc)
            for pid in query_owners[key]:
                if url not in assigned[pid]:
                    assigned[pid].append(url)
    report['unique_urls'] = len(documents)
    now = datetime.now(timezone.utc)
    async def enrich_one(p):
        meta = p['blog_enrichment']
        docs = [documents[key] for key in assigned[p['id']]]
        meta['document_count'] = len(docs)
        meta['search_status'] = 'partial_failure' if meta['failures'] and successes[p['id']] else 'failed' if meta['failures'] else 'succeeded' if meta['performed'] else 'not_attempted'
        if not docs:
            meta['status'] = 'no_results' if successes[p['id']] else 'unavailable'
            return
        async with semaphore:
            try:
                raw = await asyncio.wait_for(extract(p, unresolved[p['id']], docs, schedule),
                                             timeout=settings.BLOG_EXTRACTION_TIMEOUT_SECONDS)
                evidence, rejected = validate_claims(raw, p, unresolved[p['id']], docs, schedule, now,
                                                     settings.BLOG_MAX_AGE_DAYS, [q.get('name', '') for q in places])
                p['blog_evidence'] = evidence
                meta['rejected_claims'] = rejected
                meta['status'] = 'enriched' if evidence else 'no_usable_claims'
            except (TimeoutError, asyncio.TimeoutError):
                meta['status'] = 'extraction_failed'
                meta['failures'].append({'code': 'BLOG_EXTRACTION_TIMEOUT'})
            except Exception:
                meta['status'] = 'extraction_failed'
                meta['failures'].append({'code': 'BLOG_EXTRACTION_FAILED'})
    await asyncio.gather(*(enrich_one(p) for p in selected))
    report['performed_count'] = sum(p['blog_enrichment']['performed'] for p in places)
    report['partial_coverage'] = any(p['id'] not in selected_ids or p['blog_enrichment']['failures'] for p in eligible_for_enrichment)
    for p in places:
        report['failures'].extend({'place_id': p['id'], **failure} for failure in p['blog_enrichment']['failures'])
    return places, report
