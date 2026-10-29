import pytest
from app.services.recommendation_rules import normalize, score_candidates, evaluate, travel_score
from app.services.preference_service import extract_conditions, merge_group


def c(member='a', **kw):
    return normalize([dict(category='kind',target='짬뽕',raw_text='짬뽕',scope='menu',**kw)], member)[0]


def fact(target='짬뽕',scope='menu',value=True):
    return dict(target=target,scope=scope,value=value,source='fixture:verified')


def rank(conditions, places=None, times=(12,20,28)):
    places = places or [dict(id='p',facts=[fact()])]
    return score_candidates(conditions,places,{p['id']:[dict(member_id=str(i),driving_minutes=t) for i,t in enumerate(times)] for p in places})


def test_required_and_unknown():
    required=c(strength='required')
    ranked,all_=rank([required],[dict(id='no',facts=[fact(value=False)]),dict(id='unknown')])
    assert ranked==[]
    assert [r['eligibility'] for r in all_]==['excluded','needs_verification']
    ranked,_=rank([required]);assert ranked[0]['raw_scores']['P'] is None


def test_menu_and_place_and_conflict():
    likes=c(); avoids=c('b',direction='avoidance')
    p=dict(id='p',facts=[fact()])
    assert evaluate(avoids,p)['status']=='unknown'
    p['ordering']=dict(individual=True,alternatives_verified=True,source='fixture:menu')
    assert evaluate(avoids,p)['score']==100
    r,_=rank([likes,avoids],[p]);assert not r[0]['conflicts']
    assert len(r[0]['condition_evaluations'])==2
    p['ordering']['individual']=False
    r,_=rank([likes,avoids],[p]);assert r[0]['conflicts']
    assert evaluate(avoids,p)['score']==0
    place=c('b',direction='avoidance');place['scope']='place'
    p['facts'].append(fact(scope='place'))
    assert evaluate(place,p)['score']==0


def test_dedup_and_or():
    a=dict(category='atmosphere',target='조용한',raw_text='조용한 곳')
    b=dict(category='atmosphere',target='시끄러운',direction='avoidance',raw_text='시끄러운 곳 싫어')
    assert len(normalize([a,b,a],'a'))==1
    assert normalize([a],'a')[0]['id']!=normalize([a],'b')[0]['id']
    alt=c();alt.update(target='한식',alternatives=['일식'],scope='place')
    assert evaluate(alt,dict(category='음식점>일식'))['score']==100
    assert evaluate(alt,{})['status']=='unknown'
    items=extract_conditions('한식이나 일식',{},'preference')
    assert len(items)==1 and items[0]['alternatives']==['일식']


def test_formulas_weights_and_missing():
    yes=c(); no=c('b',direction='avoidance');no['target']='회'
    p=dict(id='p',facts=[fact(),fact('회',value=False)])
    r,_=rank([yes,no],[p]);r=r[0]
    assert r['raw_scores']['D']==pytest.approx(60)
    assert r['score']==pytest.approx(84)
    assert sum(r['contributions'].values())==pytest.approx(r['score'])
    p['facts']=[fact('회',value=False)]
    r,_=rank([yes,no],[p],(12,12,12));assert r[0]['score']==pytest.approx(77)
    r,_=rank([]);assert r[0]['weights']=={'D':1}
    r,_=rank([yes]);assert r[0]['weights']['D']==pytest.approx(4/7)
    assert travel_score([dict(duration=720,unit='seconds')])['score']==pytest.approx(80)
    assert travel_score([dict(duration=720000,unit='milliseconds')])['score']==pytest.approx(80)
    ranked,all_=rank([],times=(12,None,28));assert ranked==[] and all_[0]['score'] is None
    assert all_[0]['eligibility']=='eligible'
    assert travel_score([dict(duration=120)])['score']==0


def test_ranking_explanation_and_stable_ties():
    places=[dict(id='b'),dict(id='a')]
    r,_=rank([],places)
    assert [x['place_id'] for x in r]==['a','b']
    assert r[0]['comparison']['total_delta']==sum(r[0]['comparison']['contribution_deltas'].values())
    assert 'comparison' not in rank([])[0][0]


def test_equal_member_category_aggregation():
    conditions=[c(),c('b')]
    conditions[1]['target']='회'
    conditions += normalize([dict(category='cost',target='price',value=100,unit='KRW',operator='lte',raw_text='100원 이하')],'a')
    r,_=rank(conditions)
    # a: kind=100, cost=50 -> 75; b: kind=50 -> 50; group=62.5
    assert r[0]['raw_scores']['P']==62.5


def test_explicit_unrecognized_requires_confirmation():
    assert extract_conditions('주차 필수',{},'preference')[0]['needs_confirmation']

    # Each authenticated meeting keeps its own JSON conditions and computed explanations.
def test_meeting_condition_isolation_and_required_unknown(client):
    from test_meetings import create_group, submit, body, headers
    url,a=create_group(client,1)
    other,b=create_group(client,1)
    condition=dict(category='accessibility',target='parking',strength='required',raw_text='주차 필수')
    first=submit(client,url,a[0],body(conditions=[condition])).json()
    second=submit(client,other,b[0],body(conditions=[])).json()
    result=first['recommendation_result']
    assert result['recommendations']==[]
    assert result['recommendation_status']=='no_verified_recommendations'
    assert all(r['eligibility']=='needs_verification' for r in result['candidate_evaluations'])
    assert result['conditions'][0]['member_id']==a[0]['member_id']
    assert second['recommendation_result']['conditions']==[]
    assert second['recommendations']
    assert client.get(other,headers=headers(a[0])).status_code==401


def test_recommendation_route_failure_preserves_eligibility(client,monkeypatch):
    from test_meetings import create_group, submit, body
    from app.services import recommend_service
    async def missing(*args): return None
    monkeypatch.setattr(recommend_service.naver,'driving',missing)
    url,a=create_group(client,1)
    result=submit(client,url,a[0],body(conditions=[])).json()['recommendation_result']
    assert result['recommendations']==[]
    assert all(r['eligibility']=='eligible' and r['score_status']=='unavailable' for r in result['candidate_evaluations'])

def test_unknown_tie_breaker_and_unrounded_scores():
    conditions=[c()]
    # Both raw totals are 75: known P=100,D=56.25 vs unknown P=50,D=93.75.
    places=[dict(id='unknown'),dict(id='known',facts=[fact()])]
    travels={'unknown':[dict(driving_minutes=3.75)],'known':[dict(driving_minutes=26.25)]}
    ranked,_=score_candidates(conditions,places,travels)
    assert ranked[0]['score']==pytest.approx(ranked[1]['score'])
    assert ranked[0]['place_id']=='known'
    assert ranked[0]['comparison']['total_delta']==pytest.approx(0)


def test_quantity_validation_and_evidence():
    from app.conditions import Condition
    with pytest.raises(ValueError):
        Condition(category='cost',target='price',value=3,raw_text='3')
    condition=normalize([dict(category='cost',target='price',value=20000,unit='KRW',operator='lte',raw_text='2만원 이하')],'a')[0]
    assert evaluate(condition,dict(facts=[dict(scope='place',target='price',value=19000,unit='KRW',source='fixture:price')]))['score']==100
    assert evaluate(condition,dict(facts=[dict(scope='place',target='price',value=19,unit='minutes',source='fixture:wrong-unit')]))['status']=='unknown'


def test_same_person_conflicting_interpretation():
    items=[dict(category='kind',target='카페',raw_text='카페 좋아'),dict(category='kind',target='카페',direction='avoidance',raw_text='카페 싫어')]
    assert all(c['needs_confirmation'] for c in normalize(items,'a'))


def test_ai_structure_preserves_individual_opinions(monkeypatch):
    import asyncio
    from app.config import settings
    from app.services import preference_service
    from app.schemas import SubmissionReq
    calls=[]
    async def structured(system,user,schema,step):
        calls.append(step)
        return dict(likes=['짬뽕'],dislikes=['짬뽕'],mood=[],budget_max=0,indoor='any',start_location_text='',conditions=[
            dict(category='kind',target='짬뽕',scope='menu',raw_text='짬뽕 좋아'),
            dict(category='kind',target='짬뽕',scope='menu',direction='avoidance',raw_text='짬뽕 싫어'),
        ])
    monkeypatch.setattr(settings,'AI_PROVIDER','chatkhu')
    monkeypatch.setattr(preference_service.ai,'structured',structured)
    req=SubmissionReq(availability={},start_location=dict(text='origin',lat=37,lng=127),desired_text='짬뽕 좋아',avoided_text='짬뽕 싫어')
    result=asyncio.run(preference_service.build_submission_preference(req))
    assert [c['direction'] for c in result['conditions']]==['preference','avoidance']
    assert calls==['parse_preference']

def test_waiting_submission_has_server_member_condition_ids(client):
    from test_meetings import create_group, submit, body
    url,people=create_group(client,2)
    data=submit(client,url,people[0],body(desired_text='짬뽕 좋아',avoided_text='')).json()
    preference=next(m['preference'] for m in data['members'] if m['id']==people[0]['member_id'])
    assert preference['conditions'][0]['member_id']==people[0]['member_id']
    assert preference['conditions'][0]['id']
    assert data['status']=='waiting'
