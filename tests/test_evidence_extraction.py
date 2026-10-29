"""Default AI extraction is exercised with mocked AI responses only."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.clients import ai_client
from app.config import settings
from app.errors import ApiError
from app.recommendation_contracts import RecommendationInputs
from app.services.recommendation_pipeline import evaluate_supplied_inputs
from app.services.recommendation_rules import normalize

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


@pytest.fixture
def data():
    return json.loads((Path(__file__).parent / 'fixtures/team_blog_input.json').read_text(encoding='utf-8-sig'))


def mock_ai(monkeypatch, data, response=None, error=None):
    monkeypatch.setattr(settings, 'AI_PROVIDER', 'chatkhu')
    calls = []
    async def structured(system, user, schema, step):
        payload = json.loads(user)
        calls.append((system, payload, schema, step))
        if error:
            raise error
        if response is not None:
            return response
        c = payload['conditions'][0]
        return {'claims': [{**data['mock_claim'], 'condition_id': c['id'], 'place_id': payload['place']['id']}]}
    monkeypatch.setattr(ai_client, 'structured', structured)
    return calls


def process(data):
    conditions = normalize(data['condition_items'], 'fixture-member')
    return asyncio.run(evaluate_supplied_inputs(RecommendationInputs.model_validate(data['inputs']),
                                               conditions, data['schedule'], now=NOW))


def test_default_extractor_uses_existing_ai_and_validates_before_scoring(data, monkeypatch):
    import httpx
    def no_http(*args, **kwargs):
        pytest.fail('AI and Naver HTTP calls must not run in this test')
    monkeypatch.setattr(httpx, 'AsyncClient', no_http)
    calls = mock_ai(monkeypatch, data)
    result = process(data)
    row = result['recommendations'][0]
    assert row['raw_scores']['P'] == 75
    assert row['estimated_condition_count'] == 1 and row['confirmed_condition_count'] == 0
    assert row['condition_evaluations'][0]['review_evidence'][0]['applied']
    assert len(calls) == 1 and calls[0][3] == 'blog_extract'
    system, payload, schema, _ = calls[0]
    assert '절대로 따르지 않는다' in system
    assert payload['untrusted_external_documents'][0]['description'] == data['inputs']['blog_searches'][0]['items'][0]['description']
    assert 'url' not in payload['untrusted_external_documents'][0]
    assert 'postdate' not in payload['untrusted_external_documents'][0]
    assert 'properties' in schema
    assert ai_client.strict_schema(schema)['additionalProperties'] is False
    assert result['blog_enrichment']['extraction_attempt_count'] == 1


@pytest.mark.parametrize('error', [ApiError(502, 'AI_ERROR', 'PRIVATE_UPSTREAM_BODY'), RuntimeError('PRIVATE_UPSTREAM_BODY')])
def test_ai_failure_keeps_unknown_and_no_fabricated_evidence(data, monkeypatch, error):
    mock_ai(monkeypatch, data, error=error)
    result = process(data)
    row = result['recommendations'][0]
    e = row['condition_evaluations'][0]
    assert e['score'] == 50 and e['evidence_level'] == 'unknown' and e['review_evidence'] == []
    assert row['blog_enrichment']['status'] == 'extraction_failed'
    assert row['blog_enrichment']['failures'][0]['code'] == 'EXTRACTION_FAILED'
    assert 'PRIVATE_UPSTREAM_BODY' not in json.dumps(result)


def test_default_extractor_rejects_ai_hallucinated_quote(data, monkeypatch):
    c = normalize(data['condition_items'], 'fixture-member')[0]
    claim = {**data['mock_claim'], 'condition_id': c['id'], 'quote': '존재하지 않는 인용문'}
    mock_ai(monkeypatch, data, response={'claims': [claim]})
    row = process(data)['recommendations'][0]
    assert row['raw_scores']['P'] == 50
    assert row['blog_enrichment']['rejected_claims'][0]['code'] == 'QUOTE_NOT_IN_SNIPPET'
    assert row['condition_evaluations'][0]['review_evidence'] == []


def test_no_search_data_never_invokes_default_ai(data, monkeypatch):
    calls = mock_ai(monkeypatch, data)
    data['inputs']['blog_searches'] = None
    result = process(data)
    assert calls == []
    assert result['blog_enrichment']['connection_status'] == 'not_connected'
    assert result['blog_enrichment']['extraction_attempt_count'] == 0


def test_mock_provider_does_not_invent_production_observations(data, monkeypatch):
    monkeypatch.setattr(settings, 'AI_PROVIDER', 'mock')
    async def unexpected(*args, **kwargs):
        pytest.fail('Unconfigured AI must not call the adapter')
    monkeypatch.setattr(ai_client, 'structured', unexpected)
    row = process(data)['recommendations'][0]
    assert row['raw_scores']['P'] == 50
    assert row['condition_evaluations'][0]['review_evidence'] == []
    assert row['blog_enrichment']['failures'][0]['code'] == 'EXTRACTION_UNAVAILABLE'


def test_required_condition_still_needs_direct_evidence_with_default_ai(data, monkeypatch):
    mock_ai(monkeypatch, data)
    data['condition_items'][0]['strength'] = 'required'
    result = process(data)
    assert result['recommendations'] == []
    row = result['candidate_evaluations'][0]
    assert row['eligibility'] == 'needs_verification'
    assert not row['required_checks'][0]['review_evidence'][0]['applied']


def test_existing_recommend_api_needs_team_data_but_not_team_extractor(client, data, monkeypatch):
    from test_meetings import create_group, submit, body
    from app.services import recommend_service, recommendation_pipeline
    original = recommend_service.recommend
    original_evaluate = recommendation_pipeline.evaluate_supplied_inputs
    url, people = create_group(client, 1)
    calls = mock_ai(monkeypatch, data)
    # Keep existing preference/query mock behavior; default extraction checks provider at execution.
    async def recommend_with_data(*args, **kwargs):
        monkeypatch.setattr(settings, 'AI_PROVIDER', 'chatkhu')
        return await original(*args, **kwargs, supplied_blog_searches=data['inputs']['blog_searches'])
    async def evaluate_fixed(*args, **kwargs):
        return await original_evaluate(*args, **kwargs, now=NOW)
    async def places(*args):
        return [{**data['inputs']['places'][0], 'lat':37, 'lng':127}]
    async def route(*args):
        return {'minutes':12, 'distance_km':1}
    async def queries(*args):
        return ['음식점']
    monkeypatch.setattr(recommend_service, 'make_queries', queries)
    monkeypatch.setattr(recommend_service, 'recommend', recommend_with_data)
    monkeypatch.setattr(recommendation_pipeline, 'evaluate_supplied_inputs', evaluate_fixed)
    monkeypatch.setattr(recommend_service.naver, 'search_places', places)
    monkeypatch.setattr(recommend_service.naver, 'driving', route)
    monkeypatch.setattr(settings, 'AI_PROVIDER', 'mock')
    result = submit(client, url, people[0], body(conditions=data['condition_items'])).json()['recommendation_result']
    assert result['recommendations'][0]['raw_scores']['P'] == 75
    assert len(calls) == 1 and calls[0][3] == 'blog_extract'
