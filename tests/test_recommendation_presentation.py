import pytest
from app.services.recommendation_rules import normalize, score_candidates


def travel(minutes):
    return [{'driving_minutes': minutes, 'mode': 'driving'}]


def test_tiny_score_gap_is_readable_and_does_not_change_ranking():
    ranked, _ = score_candidates([], [{'id':'b'}, {'id':'a'}], {'a':travel(12), 'b':travel(12.02202)})
    assert [r['place_id'] for r in ranked] == ['a','b']
    assert ranked[0]['score_display'] == '80%'
    assert ranked[0]['score_percent'] == 80
    assert '0.1점 미만' in ranked[0]['reason']
    assert '반올림 전' not in ranked[0]['reason']
    assert "'D':" not in ranked[0]['reason']
    assert '자동차 이동시간' in ranked[0]['reason']
    assert ranked[0]['comparison']['total_delta'] > 0


def test_condition_categories_and_evidence_appear_without_inventing_facts():
    conditions = normalize([
        dict(category='atmosphere', target='조용한', raw_text='조용한 곳'),
        dict(category='accessibility', target='parking', direction='avoidance', raw_text='주차 불편한 곳 싫어'),
    ], 'a')
    ranked, _ = score_candidates(conditions, [{'id':'p','facts':[dict(target='조용한',scope='place',value=True,source='fixture:verified')]}], {'p':travel(12)})
    r=ranked[0]
    assert '조용한' in r['reason'] and '확인됐어요' in r['reason']
    assert '주차 가능' not in r['reason']
    assert '주차 불편' not in r['reason']
    assert [g['label'] for g in r['condition_groups']] == ['선호 조건']
    assert all(len(g['categories']) == 6 for g in r['condition_groups'])
    assert [c['label'] for c in r['condition_groups'][0]['categories']] == ['음식·활동·장소 종류','비용','분위기','공간·환경','편의·접근성','기타']
    assert len(r['condition_details']) == 1
    assert len(r['condition_evaluations']) == 2
    assert r['score_description'].endswith('만족 확률이 아닙니다.')


def test_unavailable_score_is_never_shown_as_zero_percent():
    _, rows=score_candidates([], [{'id':'p'}], {'p':[{'driving_minutes':None}]})
    assert rows[0]['score_display']=='산정 불가'
    assert rows[0]['score_percent'] is None
    assert '계산하지 못했어요' in rows[0]['reason']


def test_display_rounding_and_raw_contributions_remain_separate():
    ranked, _ = score_candidates([], [{'id':'p'}], {'p':travel(12.3)})
    r=ranked[0]
    assert r['score']==pytest.approx(79.5)
    assert r['score_display']=='80%'
    assert sum(r['contributions'].values())==r['score']
    assert r['score_breakdown'][1]['score_display']=='입력 없음'


def test_saved_result_projection_removes_old_copy_without_mutating_stored_data():
    from copy import deepcopy
    from app.services.recommendation_presentation import present_saved_result
    ranked, candidates = score_candidates([], [{'id':'a'}, {'id':'b'}], {'a':travel(12),'b':travel(12.02202)})
    ranked[0]['reason']="2순위 대비 항목별 기여분 차이: {'D':0.036699999999}. 총점 차이: 0.036700000002점 (반올림 전)."
    stored={'recommendations':ranked,'candidate_evaluations':candidates}
    original=deepcopy(stored)
    display=present_saved_result(stored)
    assert stored==original
    assert "'D':" not in display['recommendations'][0]['reason']
    assert display['recommendations'][0]['score']==stored['recommendations'][0]['score']
    assert display['recommendations'][0]['score_display']=='80%'


def test_travel_display_rounds_without_changing_score_or_source():
    from app.services.recommendation_presentation import present_saved_result
    raw=59.26083333333333
    source={'p':[{'driving_minutes':raw,'duration':raw,'unit':'minutes','mode':'driving',
                  'text':f'서울에서 직선 18.6km · 도로 18.9km - 자동차 약 {raw}분'}]}
    ranked,_=score_candidates([], [{'id':'p'}], source)
    r=ranked[0]
    assert r['travel'][0]['driving_minutes']==59.3
    assert r['travel'][0]['driving_minutes_display']=='59.3분'
    assert '59.3분' in r['travel_text'][0]
    assert '333333' not in r['travel_text'][0]
    assert r['score']==pytest.approx(100*(1-raw/60))
    assert source['p'][0]['driving_minutes']==raw
    assert r['travel'][0]['duration']==raw
    assert present_saved_result({'recommendations':[r]})['recommendations'][0]['travel'][0]['driving_minutes']==59.3


def test_avoidance_is_not_displayed_but_still_scored():
    c=normalize([dict(category='kind',target='술집',direction='avoidance',scope='place',raw_text='술집 싫어')],'a')
    r=score_candidates(c,[{'id':'p','facts':[dict(target='술집',scope='place',value=True,source='fixture')]}],{'p':travel(12)})[0][0]
    assert r['raw_scores']['N']==0
    assert '술집' not in r['reason'] and '비선호' not in r['reason']
    assert r['condition_details']==[]
    assert r['condition_evaluations'][0]['target']=='술집'
