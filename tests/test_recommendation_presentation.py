"""Public card presentation must never echo participant requirements."""
from copy import deepcopy
import json
import pytest
from app.services.recommendation_rules import normalize, score_candidates
from app.services.recommendation_presentation import present_saved_result


def travel(minutes):
    return [{'driving_minutes':minutes,'mode':'driving'}]


def test_public_card_matches_tag_and_badge_layout():
    conditions=normalize([
        dict(category='atmosphere',target='조용한',raw_text='비밀 입력 원문'),
        dict(category='atmosphere',target='대화하기 좋은',raw_text='우리끼리 이야기'),
        dict(category='accessibility',target='역 가까움',raw_text='원문'),
    ],'private-member')
    place={'id':'p','facts':[dict(scope='place',target=c['target'],value=True,source='fixture') for c in conditions]}
    row=score_candidates(conditions,[place],{'p':travel(18)})[0][0]
    assert row['reason_tags']==['조용한 분위기','대화하기 좋음','역에서 가까움']
    assert row['reason']=='조용한 분위기\n대화하기 좋음\n역에서 가까움'
    assert row['travel_badges']==[{'label':'평균','value':'18분','text':'평균 18분'},{'label':'최대','value':'18분','text':'최대 18분'}]
    public=json.dumps(row['recommendation_card'],ensure_ascii=False)
    assert '비밀' not in public and 'private-member' not in public
    assert row['condition_details']==[] and row['condition_groups']==[]


def test_unknown_and_avoidance_do_not_echo_private_conditions():
    conditions=normalize([
        dict(category='other',target='비밀표적',raw_text='공개하면 안 되는 개인정보'),
        dict(category='atmosphere',target='조용한',raw_text='조용한 곳'),
        dict(category='kind',target='술집',direction='avoidance',raw_text='비밀 사유'),
    ],'secret-member')
    row=score_candidates(conditions,[{'id':'p'}],{'p':travel(10.4)})[0][0]
    assert row['reason']=='' and row['reason_tags']==[]
    assert not row['recommendation_card']['show_reasons']
    assert '확인' not in json.dumps(row['recommendation_card'],ensure_ascii=False)
    assert len(row['condition_evaluations'])==3
    assert row['unknown_condition_count']==3


def test_review_estimates_do_not_become_confirmed_green_chips():
    c=normalize([dict(category='atmosphere',target='조용한',raw_text='secret')],'a')
    p={'id':'p','blog_evidence':[dict(condition_id=c[0]['id'],target='조용한',url='https://fixture.invalid',
                                      usable=True,relation='supports',unused_reasons=[],data_origin='fixture')]}
    row=score_candidates(c,[p],{'p':travel(12)})[0][0]
    assert row['raw_scores']['P']==75
    assert row['reason_tags']==[]


def test_display_rounds_but_preserves_precision_and_ranking():
    raw=59.2608333333333
    source={'p':[{'duration':raw,'unit':'minutes','driving_minutes':raw,'mode':'driving','text':f'자동차 약 {raw}분'}]}
    row=score_candidates([],[{'id':'p'}],source)[0][0]
    assert row['travel'][0]['driving_minutes']==59.3
    assert row['travel_text']==['자동차 약 59.3분']
    assert row['score']==pytest.approx(100*(1-raw/60))
    assert row['travel_badges'][0]['text']=='평균 59.3분'
    assert source['p'][0]['driving_minutes']==raw
    assert row['score_display'].endswith('%')


def test_comparison_never_turns_into_paragraph_or_discloses_unknown_count():
    ranked,_=score_candidates([],[{'id':'a'},{'id':'b'}],{'a':travel(12),'b':travel(12.02202)})
    assert ranked[0]['reason']==''
    assert ranked[0]['comparison']['total_delta']>0
    assert 'summary' not in ranked[0]['comparison']


def test_saved_results_reproject_without_db_writes():
    ranked,rows=score_candidates([],[{'id':'a'},{'id':'b'}],{'a':travel(12),'b':travel(13)})
    ranked[0]['reason']='비밀 조건은 정보가 부족해 확인이 필요해요. 2순위와 비슷해요.'
    ranked[0]['comparison']['summary']='비밀 비교'
    stored={'recommendations':ranked,'candidate_evaluations':rows}
    original=deepcopy(stored)
    projected=present_saved_result(stored)
    assert stored==original
    assert projected['recommendations'][0]['reason']==''
    assert 'summary' not in projected['recommendations'][0]['comparison']


def test_missing_times_have_no_invented_badges():
    row=score_candidates([],[{'id':'p'}],{'p':[{'driving_minutes':None}]})[1][0]
    assert row['travel_badges']==[]
    assert row['reason']==''
    assert row['score_display']=='산정 불가'


def test_legacy_record_without_evaluations_does_not_leak_old_paragraph():
    stored={'recommendations':[{'score':80,'reason':'비밀 조건 확인 필요','condition_details':[{'label':'비밀'}]}]}
    row=present_saved_result(stored)['recommendations'][0]
    assert row['reason']=='' and row['condition_details']==[]
    assert row['recommendation_card']['show_reasons'] is False
    assert stored['recommendations'][0]['reason']=='비밀 조건 확인 필요'
