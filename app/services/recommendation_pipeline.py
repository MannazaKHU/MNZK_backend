"""Process data supplied by the team adapter. This module never searches or fetches URLs."""
import asyncio
import html
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.recommendation_contracts import EvidenceExtractor, RecommendationInputs
from app.services.blog_evidence import validate_claims
from app.services.evidence_extraction import extract_condition_evidence, ExtractionUnavailable
from app.services.recommendation_rules import POLICY, evaluate, score_candidates


@dataclass(frozen=True)
class EvidencePolicy:
    max_candidates: int = 5
    queries_per_candidate: int = 2
    results_per_query: int = 5
    max_queries: int = 10
    extraction_concurrency: int = 3
    extraction_timeout_seconds: float = 20
    max_age_days: int = 180

    def __post_init__(self):
        limits = (self.max_candidates, self.queries_per_candidate, self.results_per_query, self.max_queries, self.max_age_days)
        if any(type(v) is not int or v < 0 for v in limits):
            raise ValueError('Evidence limits must be nonnegative integers')
        if self.extraction_concurrency < 1 or not math.isfinite(self.extraction_timeout_seconds) or self.extraction_timeout_seconds <= 0:
            raise ValueError('Extraction concurrency and timeout must be positive')


def _clean(value):
    return html.unescape(re.sub(r'<[^>]+>', '', value))


def _url_key(value):
    try:
        parts = urlsplit(value)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            return None
        query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith('utm_')]
        host = parts.netloc.lower().replace('m.blog.naver.com', 'blog.naver.com')
        return urlunsplit(('https', host, parts.path.rstrip('/'), urlencode(sorted(query)), ''))
    except ValueError:
        return None


def _selection_key(place):
    distance = place.get('distance_m')
    return (distance if distance is not None else float('inf'), str(place['id']))


def _topics(conditions):
    labels = {'kind': '메뉴 종류', 'cost': '메뉴 가격', 'atmosphere': '분위기',
              'environment': '공간 환경', 'accessibility': '편의 접근성', 'other': '이용 정보'}
    result = []
    for c in conditions:
        target = (c['target'] + ' ' + c['raw_text']).lower()
        topic = '주차' if '주차' in target or 'parking' in target else labels[c['category']]
        if topic not in result:
            result.append(topic)
    return result


def prepare_search_tasks(places, conditions, policy=EvidencePolicy()):
    """Return neutral query suggestions for the teammate. Does not perform requests."""
    unresolved = {p['id']: [c for c in conditions if evaluate(c, p)['status'] == 'unknown' and not c['needs_confirmation']]
                  for p in places}
    selected = sorted((p for p in places if unresolved[p['id']]), key=_selection_key)[:policy.max_candidates]
    tasks, query_keys = [], set()
    for p in selected:
        topics = _topics(unresolved[p['id']])
        slots = min(len(topics), policy.queries_per_candidate)
        for i in range(slots):
            group = topics[i::slots]
            query = ' '.join((p['name'], p.get('branch_name') or '', ' '.join(p.get('address', '').split()[:3]), *group))
            query = ' '.join(query.split())
            key = query.casefold()
            if key not in query_keys and len(query_keys) >= policy.max_queries:
                continue
            query_keys.add(key)
            tasks.append({'place_id': p['id'], 'query': query, 'condition_ids': [c['id'] for c in unresolved[p['id']]],
                          'max_results': policy.results_per_query})
    return tasks, unresolved, {p['id'] for p in selected}


async def enrich_supplied_inputs(places, conditions, searches, schedule, *, extractor=extract_condition_evidence,
                                 policy=EvidencePolicy(), now=None, enabled=True):
    """Receive completed searches; use default AI extraction, or explicit None to disable it."""
    now = now or datetime.now(timezone.utc)
    tasks, unresolved, selected = prepare_search_tasks(places, conditions, policy)
    report = {'connection_status': 'not_connected' if searches is None else 'supplied',
              'enabled': enabled, 'external_requests_made': 0, 'external_requests_scope': 'search_only', 'search_requests_made': 0,
              'extraction_attempt_count': 0, 'processing_limits': asdict(policy),
              'selection_policy': 'center_distance_then_place_id', 'candidate_count': len(places),
              'selected_count': len(selected), 'performed_count': 0, 'accepted_query_count': 0,
              'unique_url_count': 0, 'partial_coverage': False, 'failures': [],
              'data_origins': sorted({s.data_origin for s in searches}) if searches else [],
              'scope_note': '제공된 일부 후보·검색 요약문 범위에서 평가합니다. 전체 후보 중 절대 최적을 뜻하지 않습니다.',
              'search_tasks': tasks}
    by_id = {p['id']: p for p in places}
    for p in places:
        p['blog_evidence'] = []
        p['blog_enrichment'] = {'performed': False, 'search_status': 'not_supplied',
                                'status': 'not_needed' if not unresolved[p['id']] else 'not_selected' if p['id'] not in selected else 'not_connected',
                                'document_count': 0, 'failures': [], 'rejected_claims': [], 'searches': []}
    if not enabled or searches is None:
        report['partial_coverage'] = any(unresolved.values())
        if not enabled:
            for p in places:
                p['blog_enrichment']['status'] = 'disabled'
        return report
    documents, assigned = {}, {pid: [] for pid in selected}
    seen_batches, unique_queries, per_place = set(), set(), {pid: set() for pid in selected}
    for batch in searches:
        pid = batch.place_id
        if pid not in by_id:
            report['failures'].append({'code': 'UNKNOWN_PLACE_REFERENCE'})
            continue
        meta = by_id[pid]['blog_enrichment']
        if pid not in selected:
            if unresolved[pid]:
                meta['failures'].append({'code': 'CANDIDATE_PROCESSING_LIMIT'})
            continue
        query = ' '.join(batch.query.split()).casefold()
        key = pid, query
        if key in seen_batches:
            continue
        seen_batches.add(key)
        if ((query not in unique_queries and len(unique_queries) >= policy.max_queries)
                or len(per_place[pid]) >= policy.queries_per_candidate):
            meta['failures'].append({'code': 'QUERY_PROCESSING_LIMIT'})
            continue
        unique_queries.add(query)
        per_place[pid].add(query)
        meta['performed'] = batch.status not in {'not_requested', 'limit_exceeded'} or meta['performed']
        meta['searches'].append({'query': batch.query, 'status': batch.status,
                                 'fetched_at': batch.fetched_at.isoformat() if batch.fetched_at else None,
                                 'failure_code': batch.failure_code, 'data_origin': batch.data_origin})
        if batch.status != 'success':
            meta['failures'].append({'code': 'SUPPLIED_SEARCH_' + batch.status.upper(),
                                     'failure_code': batch.failure_code})
            continue
        if len(batch.items) > policy.results_per_query:
            meta['failures'].append({'code': 'RESULT_PROCESSING_LIMIT'})
        for item in batch.items[:policy.results_per_query]:
            url_key = _url_key(item.url)
            if not url_key:
                meta['failures'].append({'code': 'INVALID_SOURCE_URL'})
                continue
            # One URL per place, including duplicate queries. Keep original URL for attribution.
            # Retrieval metadata can differ across batches, so source records remain place-local.
            document_key = pid, url_key
            documents.setdefault(document_key, {'title': _clean(item.title), 'description': _clean(item.description),
                                                'url': item.url, 'postdate': item.postdate or '',
                                                'fetched_at': batch.fetched_at.isoformat() if batch.fetched_at else None,
                                                'data_origin': batch.data_origin})
            if document_key not in assigned[pid]:
                assigned[pid].append(document_key)
    report['accepted_query_count'] = len(unique_queries)
    report['unique_url_count'] = len({key[1] for key in documents})
    semaphore = asyncio.Semaphore(policy.extraction_concurrency)
    async def one_place(p):
        meta = p['blog_enrichment']
        if p['id'] not in selected:
            return
        statuses = {s['status'] for s in meta['searches']}
        meta['search_status'] = ('partial_failure' if 'success' in statuses and statuses != {'success'} else
                                 'succeeded' if statuses == {'success'} else 'failed' if statuses else 'not_supplied')
        docs = [documents[key] for key in assigned[p['id']]]
        meta['document_count'] = len(docs)
        if not docs:
            meta['status'] = 'no_results' if 'success' in statuses else 'unavailable'
            return
        if extractor is None:
            meta['status'] = 'extractor_not_connected'
            meta['failures'].append({'code': 'EXTRACTOR_NOT_CONNECTED'})
            return
        async with semaphore:
            try:
                report['extraction_attempt_count'] += 1
                raw = await asyncio.wait_for(extractor(p, unresolved[p['id']], docs, schedule),
                                             timeout=policy.extraction_timeout_seconds)
                evidence, rejected = validate_claims(raw, p, unresolved[p['id']], docs, schedule,
                                                     now, policy.max_age_days, [p['name'] for p in places])
                p['blog_evidence'] = evidence
                meta['rejected_claims'] = rejected
                meta['status'] = 'evaluated' if evidence else 'no_valid_claims'
            except ExtractionUnavailable:
                meta['status'] = 'extraction_unavailable'
                meta['failures'].append({'code': 'EXTRACTION_UNAVAILABLE'})
            except TimeoutError:
                meta['status'] = 'extraction_failed'
                meta['failures'].append({'code': 'EXTRACTION_TIMEOUT'})
            except Exception:
                meta['status'] = 'extraction_failed'
                meta['failures'].append({'code': 'EXTRACTION_FAILED'})
    await asyncio.gather(*(one_place(p) for p in places))
    report['performed_count'] = sum(p['blog_enrichment']['performed'] for p in places)
    report['partial_coverage'] = any(unresolved[p['id']] and
                                   (p['id'] not in selected or not p['blog_enrichment']['performed'] or p['blog_enrichment']['failures'])
                                   for p in places)
    for p in places:
        report['failures'].extend({'place_id': p['id'], **f} for f in p['blog_enrichment']['failures'])
    return report


async def evaluate_supplied_inputs(inputs: RecommendationInputs, conditions: list[dict], schedule: dict, *,
                                   extractor: EvidenceExtractor | None = extract_condition_evidence, evidence_policy=EvidencePolicy(),
                                   scoring_policy=POLICY, now=None, use_blog_evidence=True):
    """Evaluate supplied data; default extraction uses existing AI only when snippets exist."""
    if any(c['member_id'] not in inputs.participant_ids for c in conditions):
        raise ValueError('Condition belongs to another meeting participant')
    places = [p.model_dump(mode='json', exclude_none=True) for p in inputs.places]
    travel_map = {(t.place_id, t.member_id): t for t in inputs.travel_times}
    travels = {}
    for place in places:
        rows = []
        for mid in inputs.participant_ids:
            t = travel_map.get((place['id'], mid))
            row = t.model_dump(mode='json') if t else {'place_id': place['id'], 'member_id': mid,
                                                      'status': 'not_requested', 'duration': None, 'unit': None,
                                                      'mode': 'unknown', 'failure_code': 'not_requested'}
            row['unit'] = row['unit'] or 'minutes'
            row['driving_minutes'] = (row['duration'] / {'minutes': 1, 'seconds': 60, 'milliseconds': 60000}[row['unit']]
                                      if row['duration'] is not None and row['mode'] == 'driving' else None)
            row['from'] = row.pop('origin_text', None)
            rows.append(row)
        travels[place['id']] = rows
    report = await enrich_supplied_inputs(places, conditions, inputs.blog_searches, schedule, extractor=extractor,
                                          policy=evidence_policy, now=now, enabled=use_blog_evidence)
    ranked, evaluated = score_candidates(conditions, places, travels, scoring_policy)
    return {'meeting_id': inputs.meeting_id, 'recommendations': ranked, 'candidate_evaluations': evaluated,
            'blog_enrichment': report, 'conditions': conditions, 'policy_version': scoring_policy.version,
            'recommendation_status': 'available' if ranked else 'no_verified_recommendations',
            'message': None if ranked else '조건을 확인한 추천 후보가 없음'}
