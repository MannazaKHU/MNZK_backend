"""All sources in this file are handcrafted fixtures. No external API calls."""
import asyncio
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.recommendation_contracts import RecommendationInputs, BlogSearchBatch
from app.services.blog_evidence import validate_claims
from app.services.recommendation_pipeline import EvidencePolicy, evaluate_supplied_inputs, prepare_search_tasks
from app.services.recommendation_rules import evaluate, normalize

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


@pytest.fixture
def data():
    return json.loads((Path(__file__).parent / 'fixtures' / 'team_blog_input.json').read_text(encoding='utf-8-sig'))


def condition(data, **changes):
    item = {**data['condition_items'][0], **changes}
    return normalize([item], 'fixture-member')[0]


def supplied(data):
    return RecommendationInputs.model_validate(data['inputs'])


def extractor_for(data, observations=('matches',)):
    async def extractor(place, conditions, documents, schedule):
        claims = []
        for i, observation in enumerate(observations):
            claim = {**data['mock_claim'], 'document_index': min(i, len(documents)-1),
                     'condition_id': conditions[0]['id'], 'place_id': place['id'], 'target': conditions[0]['target'],
                     'observation': observation}
            claims.append(claim)
        return {'claims': claims}
    return extractor


def run(data, c=None, extractor=None, **kwargs):
    c = c or condition(data)
    return asyncio.run(evaluate_supplied_inputs(supplied(data), [c], data['schedule'], now=NOW,
                                               extractor=extractor if extractor is not None else extractor_for(data), **kwargs))


def evaluation(result):
    return result['candidate_evaluations'][0]['condition_evaluations'][0]


@pytest.mark.parametrize('observation,score', [('matches', 75), ('does_not_match', 25)])
def test_review_scores_and_counts(data, observation, score):
    result = run(data, extractor=extractor_for(data, (observation,)))
    e = evaluation(result)
    assert e['score'] == score
    assert e['evidence_level'] == 'review_estimate'
    assert e['status'] == 'unknown'  # factual confirmation is deliberately separate
    assert e['assessment_status'] == ('estimated_support' if score == 75 else 'estimated_conflict')
    row = result['recommendations'][0]
    assert row['confirmed_condition_count'] == 0
    assert row['estimated_condition_count'] == 1
    assert row['unknown_condition_count'] == 0
    assert sum(row['contributions'].values()) == row['score']
    assert row['raw_scores']['P'] == score
    assert e['review_evidence'][0]['applied']
    assert e['review_evidence'][0]['data_origin'] == 'fixture'
    assert result['blog_enrichment']['external_requests_made'] == 0


def test_other_branch_blocked_even_if_extractor_says_match(data):
    data['inputs']['blog_searches'][0]['items'][0].update(title='봄식당 홍대점 후기', description='봄식당 홍대점에서 조용하게 식사했다.')
    data['mock_claim']['identity_quote'] = '봄식당 홍대점'
    e = evaluation(run(data))
    assert e['score'] == 50
    assert e['review_evidence'][0]['place_match'] == 'mismatch'
    assert 'PLACE_MISMATCH' in e['review_evidence'][0]['unused_reasons']


@pytest.mark.parametrize('title,address,description', [
    ('봄식당 후기', '서울특별시 강남구 역삼동 1', '조용하게 식사했다.'),
    ('봄식당 후기', '서울특별시 강남구 역삼동 1', '부산광역시 해운대구 봄식당에서 조용하게 식사했다.'),
])
def test_name_or_query_alone_not_identity(data, title, address, description):
    data['inputs']['places'][0].update(name='봄식당', address=address)
    data['inputs']['blog_searches'][0]['items'][0].update(title=title, description=description)
    data['mock_claim']['identity_quote'] = '봄식당'
    assert evaluation(run(data))['score'] == 50


def test_multiple_places_and_unclear_attribution(data):
    data['mock_claim']['multiple_places'] = True
    assert evaluation(run(data))['review_evidence'][0]['place_match'] == 'unclear'
    assert evaluation(run(data))['score'] == 50


def test_empty_results_do_not_prove_absence(data):
    data['inputs']['blog_searches'][0]['items'] = []
    async def unexpected(*args):
        pytest.fail('No documents must not invoke extraction')
    result = run(data, extractor=unexpected)
    assert evaluation(result)['score'] == 50
    assert result['candidate_evaluations'][0]['blog_enrichment']['status'] == 'no_results'


def test_conflicting_and_repeated_reviews(data):
    doc = copy.deepcopy(data['inputs']['blog_searches'][0]['items'][0])
    doc['url'] = 'https://fixture.invalid/blog/2'
    data['inputs']['blog_searches'][0]['items'].append(doc)
    e = evaluation(run(data, extractor=extractor_for(data, ('matches', 'does_not_match'))))
    assert e['score'] == 50 and e['review_conflict']
    assert not any(x['applied'] for x in e['review_evidence'])
    assert evaluation(run(data, extractor=extractor_for(data, ('matches', 'matches'))))['score'] == 75


@pytest.mark.parametrize('postdate,reason', [(None, 'POSTDATE_MISSING_OR_INVALID'), ('20200101', 'STALE_EVIDENCE'), ('20271029', 'FUTURE_DATE')])
def test_missing_old_future_dates_hold_score(data, postdate, reason):
    data['inputs']['blog_searches'][0]['items'][0]['postdate'] = postdate
    e = evaluation(run(data))
    assert e['score'] == 50
    assert reason in e['review_evidence'][0]['unused_reasons']


def test_missing_retrieval_time_and_iso_postdate(data):
    data['inputs']['blog_searches'][0]['items'][0]['postdate'] = '2026-10-20'
    assert evaluation(run(data))['score'] == 75
    data['inputs']['blog_searches'][0]['fetched_at'] = None
    assert evaluation(run(data))['score'] == 50


@pytest.mark.parametrize('observation', ['matches', 'does_not_match'])
def test_required_review_neither_passes_nor_excludes(data, observation):
    result = run(data, condition(data, strength='required'), extractor=extractor_for(data, (observation,)))
    assert result['recommendations'] == []
    row = result['candidate_evaluations'][0]
    assert row['eligibility'] == 'needs_verification'
    assert row['confirmed_condition_count'] == 0
    assert row['required_checks'][0]['review_evidence'][0]['unused_reasons'] == ['REQUIRED_NEEDS_DIRECT_EVIDENCE']


def test_ungrounded_quotes_and_model_url_or_date_rejected(data):
    data['mock_claim']['quote'] = '원문에는 없는 무료주차 보장'
    result = run(data)
    assert evaluation(result)['review_evidence'] == []
    assert result['candidate_evaluations'][0]['blog_enrichment']['rejected_claims'][0]['code'] == 'QUOTE_NOT_IN_SNIPPET'
    data['mock_claim']['url'] = 'https://invented.invalid'
    result = run(data)
    assert evaluation(result)['review_evidence'] == []
    assert result['candidate_evaluations'][0]['blog_enrichment']['rejected_claims'][0]['code'] == 'INVALID_CLAIM'


def test_parking_available_does_not_mean_free(data):
    data['inputs']['blog_searches'][0]['items'][0]['description'] = '봄식당 강남점은 주차 가능하다.'
    data['mock_claim']['quote'] = '주차 가능하다.'
    free = condition(data, category='accessibility', target='free_parking', raw_text='무료 주차 선호')
    e = evaluation(run(data, free))
    assert e['score'] == 50 and 'FREE_PARKING_NOT_STATED' in e['review_evidence'][0]['unused_reasons']
    parking = condition(data, category='accessibility', target='parking', raw_text='주차 가능 선호')
    assert evaluation(run(data, parking))['score'] == 75


def test_avoidance_uses_desired_outcome_and_menu_ordering(data):
    avoidance = condition(data, direction='avoidance')
    assert evaluation(run(data, avoidance))['score'] == 25
    menu = condition(data, category='kind', target='짬뽕', scope='menu', direction='avoidance', raw_text='짬뽕 싫어')
    data['inputs']['blog_searches'][0]['items'][0]['description'] = '봄식당 강남점은 짬뽕을 판매한다.'
    data['mock_claim']['quote'] = '짬뽕을 판매한다.'
    e = evaluation(run(data, menu))
    assert e['score'] == 50
    assert 'MENU_ORDERING_NOT_VERIFIED' in e['review_evidence'][0]['unused_reasons']
    data['inputs']['places'][0]['ordering'] = dict(individual=False, source='fixture:direct-ordering')
    assert evaluation(run(data, menu))['score'] == 25


def test_restrictions_match_full_schedule_or_hold(data):
    text = '평일 낮에 조용하게 식사했다.'
    data['inputs']['blog_searches'][0]['items'][0]['description'] = '봄식당 강남점에서 ' + text
    data['mock_claim']['quote'] = text
    data['mock_claim']['context_quotes'] = ['평일 낮']
    assert evaluation(run(data))['score'] == 75
    data['schedule']['start'], data['schedule']['end'] = '19:00', '20:00'
    assert evaluation(run(data))['score'] == 50
    data['schedule'] = {}
    assert evaluation(run(data))['score'] == 50


def test_new_direct_fact_priority_over_reviews(data):
    c = condition(data)
    place = {'id': 'fixture-place', 'facts': [
        dict(scope='place', target=c['target'], value=False, source='fixture:old', verified_at='2026-10-01T00:00:00Z'),
        dict(scope='place', target=c['target'], value=True, source='fixture:new', verified_at='2026-10-28T00:00:00Z')],
        'blog_evidence': [dict(condition_id=c['id'], target=c['target'], url='https://fixture.invalid/blog', usable=True,
                               relation='conflicts', unused_reasons=[], data_origin='fixture')]}
    e = evaluate(c, place)
    assert e['score'] == 100 and e['evidence_level'] == 'confirmed' and e['review_conflict']
    assert 'DIRECT_EVIDENCE_PRIORITY' in e['review_evidence'][0]['unused_reasons']


def test_failures_preserve_direct_info_and_are_sanitized(data):
    data['inputs']['blog_searches'][0].update(status='failed', failure_code='upstream_error', items=[])
    result = run(data)
    assert evaluation(result)['score'] == 50
    assert result['recommendations']
    assert result['candidate_evaluations'][0]['blog_enrichment']['search_status'] == 'failed'
    async def bad_extractor(*args):
        raise RuntimeError('DO_NOT_LEAK_SECRET')
    data['inputs']['blog_searches'][0].update(status='success', failure_code=None, items=[{
        'title':'봄식당 강남점', 'description':'조용하게 식사했다.', 'url':'https://fixture.invalid/1', 'postdate':'20261020'}])
    result = run(data, extractor=bad_extractor)
    assert evaluation(result)['score'] == 50
    assert 'DO_NOT_LEAK_SECRET' not in json.dumps(result)
    assert result['blog_enrichment']['failures'][0]['code'] == 'EXTRACTION_FAILED'


def test_disconnected_input_and_extractor_never_call_provider(data, monkeypatch):
    import httpx
    def forbidden(*args, **kwargs):
        pytest.fail('The supplied-data pipeline must never create an HTTP client')
    monkeypatch.setattr(httpx, 'AsyncClient', forbidden)
    result = asyncio.run(evaluate_supplied_inputs(supplied(data), [condition(data)], data['schedule'], now=NOW, extractor=None))
    assert evaluation(result)['score'] == 50
    assert result['candidate_evaluations'][0]['blog_enrichment']['status'] == 'extractor_not_connected'
    data['inputs']['blog_searches'] = None
    result = run(data)
    assert result['blog_enrichment']['connection_status'] == 'not_connected'
    assert result['blog_enrichment']['external_requests_made'] == 0

def test_or_distinct_alternatives_are_not_conflicting_reviews(data):
    c = condition(data, category='kind', target='한식', alternatives=['일식'], scope='place')
    data['inputs']['blog_searches'][0]['items'][0]['description'] = '봄식당 강남점은 한식을 제공한다. 일식은 제공하지 않는다.'
    async def extract(place, conditions, documents, schedule):
        a = {**data['mock_claim'], 'condition_id': c['id'], 'target':'한식','observation':'matches','quote':'한식을 제공한다.'}
        b = {**a, 'target':'일식','observation':'does_not_match','quote':'일식은 제공하지 않는다.'}
        return {'claims':[a,b]}
    e = evaluation(run(data,c,extractor=extract))
    assert e['score'] == 75 and not e['review_conflict']
    assert [r['applied'] for r in e['review_evidence']] == [True,False]
    async def incomplete(place, conditions, documents, schedule):
        return {'claims':[{**data['mock_claim'],'condition_id':c['id'],'target':'일식','observation':'does_not_match','quote':'일식은 제공하지 않는다.'}]}
    assert evaluation(run(data,c,extractor=incomplete))['score'] == 50


def test_supplied_query_url_dedup_and_limits(data):
    batch = data['inputs']['blog_searches'][0]
    duplicate = copy.deepcopy(batch)
    duplicate['query'] += ' '
    duplicate['items'][0]['url'] += '#same-url'
    data['inputs']['blog_searches'] += [duplicate]
    other = copy.deepcopy(batch)
    other['query'] = '다른 검색어'
    other['items'][0]['url'] += '?utm_source=fixture'
    data['inputs']['blog_searches'].append(other)
    result = run(data)
    assert result['blog_enrichment']['accepted_query_count'] == 2
    assert result['blog_enrichment']['unique_url_count'] == 1
    assert result['candidate_evaluations'][0]['blog_enrichment']['document_count'] == 1
    result = run(data,evidence_policy=EvidencePolicy(max_queries=1))
    assert result['blog_enrichment']['accepted_query_count'] == 1
    assert result['blog_enrichment']['partial_coverage']
    assert 'QUERY_PROCESSING_LIMIT' in [f['code'] for f in result['blog_enrichment']['failures']]


def test_candidate_limit_preserves_unprocessed_candidates(data):
    for i in range(1,7):
        p = {**data['inputs']['places'][0], 'id':f'p{i}', 'distance_m':100+i}
        data['inputs']['places'].append(p)
        data['inputs']['travel_times'].append({**data['inputs']['travel_times'][0],'place_id':p['id']})
    result = run(data)
    assert len(result['candidate_evaluations']) == 7
    assert result['blog_enrichment']['selected_count'] == 5
    assert result['blog_enrichment']['partial_coverage']
    assert [evaluation['raw_scores']['P'] for evaluation in result['candidate_evaluations'][1:]] == [50]*6
    assert result['candidate_evaluations'][-1]['blog_enrichment']['status'] == 'not_selected'
    tasks,_,_ = prepare_search_tasks([p.model_dump(exclude_none=True) for p in supplied(data).places],[condition(data)])
    assert len({task['place_id'] for task in tasks}) == 5
    assert not any('조용한 맛집' in task['query'] for task in tasks)


def test_missing_traveler_is_not_dropped_and_units_remain_explicit(data):
    data['inputs']['participant_ids'].append('other-member')
    result = run(data)
    row = result['candidate_evaluations'][0]
    assert len(row['travel']) == 2 and row['score'] is None and row['rank'] is None
    assert row['eligibility'] == 'eligible'
    assert row['condition_evaluations'][0]['score'] == 75
    data['inputs']['travel_times'].append({**data['inputs']['travel_times'][0], 'member_id':'other-member', 'duration':720000, 'unit':'milliseconds'})
    result = run(data)
    assert result['recommendations'][0]['travel_summary']['average_minutes'] == 12


def test_contract_rejects_foreign_participants_and_error_bodies(data):
    other = condition(data)
    other['member_id'] = 'foreign-member'
    with pytest.raises(ValueError):
        run(data,other)
    data['inputs']['blog_searches'][0]['raw_error_body'] = 'secret must never be passed'
    with pytest.raises(ValidationError):
        supplied(data)


def test_extraction_timeout_and_disable(data):
    async def slow(*args):
        await asyncio.sleep(.1)
        return {'claims':[]}
    result=run(data,extractor=slow,evidence_policy=EvidencePolicy(extraction_timeout_seconds=.001))
    assert evaluation(result)['score']==50
    assert result['blog_enrichment']['failures'][0]['code']=='EXTRACTION_TIMEOUT'
    async def forbidden(*args):
        pytest.fail('Disabled evidence must not extract')
    assert evaluation(run(data,extractor=forbidden,use_blog_evidence=False))['score']==50


def test_existing_recommend_api_uses_same_supplied_data_pipeline(client,monkeypatch,data):
    from test_meetings import create_group, submit, body
    from app.services import recommend_service, recommendation_pipeline
    original = recommend_service.recommend
    original_evaluate = recommendation_pipeline.evaluate_supplied_inputs
    async def evaluate_fixed(*args, **kwargs):
        return await original_evaluate(*args, **kwargs, now=NOW)
    async def recommend_with_team_data(*args, **kwargs):
        return await original(*args, **kwargs,supplied_blog_searches=data['inputs']['blog_searches'],evidence_extractor=extractor_for(data))
    async def places(*args):
        return [{**data['inputs']['places'][0],'lat':37,'lng':127}]
    async def route(*args):
        return {'minutes':12,'distance_km':1}
    monkeypatch.setattr(recommendation_pipeline,'evaluate_supplied_inputs',evaluate_fixed)
    monkeypatch.setattr(recommend_service,'recommend',recommend_with_team_data)
    monkeypatch.setattr(recommend_service.naver,'search_places',places)
    monkeypatch.setattr(recommend_service.naver,'driving',route)
    url,people=create_group(client,1)
    response=submit(client,url,people[0],body(conditions=data['condition_items']))
    assert response.status_code==200
    result=response.json()['recommendation_result']
    assert result['meeting_id']==url.rsplit('/',1)[-1]
    row=result['recommendations'][0]
    assert row['confirmed_condition_count']==0 and row['estimated_condition_count']==1
    assert row['raw_scores']['P']==75
    assert row['score']==pytest.approx((.4*80+.3*75)/.7)
    assert row['condition_evaluations'][0]['review_evidence'][0]['url']=='https://fixture.invalid/blog/1'
    assert '모의' in row['condition_evaluations'][0]['evidence']
    assert '"description":' not in json.dumps(result)
    assert result['blog_enrichment']['external_requests_made']==0

def test_unmentioned_menu_not_inferred_absent(data):
    c = condition(data, category='kind', target='짬뽕', scope='menu', direction='avoidance')
    e = evaluation(run(data, c, extractor=extractor_for(data,('does_not_match',))))
    assert e['score'] == 50
    assert 'TARGET_NOT_IN_QUOTE' in e['review_evidence'][0]['unused_reasons']


def test_grounded_quantity_uses_server_comparison(data):
    c = condition(data,category='cost',target='price_per_person',value=20000,unit='KRW',operator='lte',raw_text='1인 2만원 이하')
    data['inputs']['blog_searches'][0]['items'][0]['description'] = '봄식당 강남점 1인 식사 비용은 18,000원이었다.'
    data['mock_claim'].update(quote='1인 식사 비용은 18,000원이었다.',observed_value=18000,observed_unit='KRW',observation='does_not_match')
    assert evaluation(run(data,c))['score']==75  # comparison comes from the server, not the observation label
    data['mock_claim']['observed_value']=10000
    assert evaluation(run(data,c))['score']==50


def test_extraction_concurrency_is_bounded(data):
    for i in range(1,5):
        p={**data['inputs']['places'][0],'id':f'p{i}','distance_m':100+i}
        data['inputs']['places'].append(p)
        data['inputs']['travel_times'].append({**data['inputs']['travel_times'][0],'place_id':p['id']})
        data['inputs']['blog_searches'].append({**data['inputs']['blog_searches'][0],'place_id':p['id']})
    active,maximum=0,0
    async def extractor(*args):
        nonlocal active,maximum
        active+=1; maximum=max(active,maximum)
        await asyncio.sleep(.005)
        active-=1
        return {'claims':[]}
    run(data,extractor=extractor,evidence_policy=EvidencePolicy(extraction_concurrency=2))
    assert maximum==2
