"""User-facing recommendation labels; raw ranking arithmetic stays unchanged."""
from decimal import Decimal, ROUND_HALF_UP
import re

CATEGORIES = {'kind': '음식·활동·장소 종류', 'cost': '비용', 'atmosphere': '분위기',
              'environment': '공간·환경', 'accessibility': '편의·접근성', 'other': '기타'}
DIRECTIONS = {'preference': '선호 조건', 'avoidance': '비선호 조건'}
TARGETS = {'parking': '주차 가능', 'free_parking': '무료 주차', 'price_per_person': '1인 비용',
           'indoor': '실내', 'outdoor': '야외'}
STATUS_LABELS = {'satisfied': '충족 확인', 'unsatisfied': '불충족 확인', 'unknown': '확인 필요',
                 'estimated_support': '후기에서 충족 지지', 'estimated_conflict': '후기에서 충돌 언급'}


def rounded(value, digits=0):
    return float(Decimal(str(value)).quantize(Decimal('1').scaleb(-digits), rounding=ROUND_HALF_UP))


def percent(value):
    return '산정 불가' if value is None else f'{int(rounded(value))}%'


def condition_label(e):
    labels = [TARGETS.get(t, t) for t in [e['target']] + e['alternatives']]
    label = ' 또는 '.join(labels)
    if e['value'] is not None:
        units = {'KRW': '원', 'minutes': '분', 'people': '명'}
        op = {'eq': '', 'lte': ' 이하', 'gte': ' 이상'}[e['operator']]
        label += f" {e['value']:g}{units[e['unit']]}{op}"
    # Internal identifiers should not become UI copy.
    if any('_' in t or t.isascii() for t in [e['target']] + e['alternatives']) and e['target'] not in TARGETS:
        label = e['raw_text']
    return label


def present_candidate(row):
    row['score_percent'] = None if row['score'] is None else int(rounded(row['score']))
    row['score_display'] = percent(row['score'])
    row['score_label'] = '종합 추천 점수'
    row['score_description'] = '평가 기준을 가중 합산한 100점 만점 점수이며 만족 확률이 아닙니다.'
    details = []
    for e in row['condition_evaluations']:
        state = e.get('assessment_status', e['status'])
        details.append({'condition_id': e['id'], 'member_id': e['member_id'],
                        'direction': e['direction'], 'direction_label': DIRECTIONS[e['direction']],
                        'category': e['category'], 'category_label': CATEGORIES[e['category']],
                        'label': condition_label(e), 'strength': e['strength'],
                        'strength_label': '필수' if e['strength'] == 'required' else '희망',
                        'status': state, 'status_label': STATUS_LABELS[state],
                        'evidence_level': e['evidence_level'], 'evidence': e['evidence'], 'sources': e['sources']})
    # Avoidance stays in condition_evaluations for scoring, but is not user-facing copy.
    details = [d for d in details if d['direction'] == 'preference']
    row['condition_details'] = details
    row['condition_groups'] = [
        {'direction': direction, 'label': direction_label, 'categories': [
            {'category': category, 'label': label,
             'conditions': [d for d in details if d['direction'] == direction and d['category'] == category]}
            for category, label in CATEGORIES.items()]}
        for direction, direction_label in DIRECTIONS.items() if direction == 'preference']
    row['score_breakdown'] = [
        {'key': key, 'label': label, 'score_display': percent(row['raw_scores'][key]) if key in row['weights'] else '입력 없음',
         'weight_percent': rounded(row['weights'].get(key, 0)*100, 1), 'active': key in row['weights']}
        for key, label in [('D', '이동 편의'), ('P', '선호 조건 만족'), ('N', '조건 적합성')]]
    sentences = []
    for direction in ('preference',):
        positives = [d for d in details if d['direction'] == direction and d['status'] == 'satisfied']
        estimates = [d for d in details if d['direction'] == direction and d['status'] == 'estimated_support']
        negative = [d for d in details if d['direction'] == direction and d['status'] in {'unsatisfied', 'estimated_conflict'}]
        if positives:
            labels = ', '.join(dict.fromkeys(d['label'] for d in positives[:2]))
            sentences.append(f"{DIRECTIONS[direction]} 중 ‘{labels}’의 {'회피 가능 여부' if direction == 'avoidance' else '충족 여부'}가 확인됐어요.")
        if estimates:
            labels = ', '.join(dict.fromkeys(d['label'] for d in estimates[:2]))
            fixture = any(e.get('data_origin') == 'fixture' for ev in row['condition_evaluations'] for e in ev.get('review_evidence', []) if e.get('applied'))
            sentences.append(f"{'모의 후기 자료' if fixture else '제공된 후기'}에 ‘{labels}’ 조건을 지지하는 언급이 있어요. 직접 확인된 정보는 아니에요.")
        if negative:
            labels = ', '.join(dict.fromkeys(d['label'] for d in negative[:2]))
            sentences.append(f"‘{labels}’ 조건은 충족하지 못하거나 후기에서 충돌하는 언급이 있어 확인이 필요해요.")
    row['travel'] = [present_travel(t) for t in row.get('travel', [])]
    row['travel_text'] = [t.get('text', '') for t in row['travel']]
    travel = row['travel_summary']
    for field in ('average_minutes', 'maximum_minutes'):
        value = travel.get(field)
        travel[field + '_display'] = None if value is None else f'{rounded(value, 1):g}분'
    if travel['score'] is not None:
        sentences.append(f"자동차 이동시간은 평균 {rounded(travel['average_minutes'], 1):g}분, 가장 먼 참여자는 {rounded(travel['maximum_minutes'], 1):g}분이에요.")
    else:
        sentences.append('참여자 전원의 지원 가능한 이동시간이 확보되지 않아 종합 점수와 순위를 계산하지 못했어요.')
    unknown = [d for d in details if d['status'] == 'unknown']
    if unknown:
        labels = ', '.join(dict.fromkeys(d['label'] for d in unknown[:2]))
        sentences.append(f"‘{labels}’ 등 {len(unknown)}개 조건은 정보가 부족해 확인이 필요해요.")
    if row['eligibility'] == 'needs_verification':
        sentences.append('필수 조건이 확인되지 않아 일반 추천 순위에서 보류했어요.')
    elif row['eligibility'] == 'excluded':
        sentences.append('필수 조건 위반이 확인되어 일반 추천 순위에서 제외했어요.')
    row['reason'] = ' '.join(sentences)


def present_comparison(first, second):
    delta = first['comparison']['total_delta']
    if abs(delta) < .1:
        summary = ('2순위와 종합 점수 차이가 0.1점 미만으로 매우 비슷해요.' if delta > 0 else
                   '종합 점수가 같아 미확인 조건 수와 최대 이동시간 등의 기준으로 순위를 정했어요.')
    else:
        advantages = [label for key, label in [('D', '이동 편의'), ('P', '선호 조건 만족'), ('N', '조건 적합성')]
                      if first['comparison']['contribution_deltas'].get(key, 0) > 0]
        summary = f"2순위보다 {'·'.join(advantages)}에서 유리해 종합 점수가 {rounded(delta, 1):g}점 높아요."
    first['comparison']['summary'] = summary
    first['reason'] += ' ' + summary


def present_saved_result(result):
    """Refresh display copy on reads without changing persisted scores or database JSON."""
    if result is None:
        return None
    from copy import deepcopy
    projected = deepcopy(result)
    for collection in ('candidate_evaluations', 'recommendations'):
        for row in projected.get(collection, []):
            row['travel'] = [present_travel(t) for t in row.get('travel', [])]
            row['travel_text'] = [t.get('text', '') for t in row['travel']]
            if all(key in row for key in ('condition_evaluations', 'travel_summary', 'raw_scores', 'weights', 'eligibility')):
                present_candidate(row)
    recommendations = projected.get('recommendations', [])
    if recommendations and recommendations[0].get('comparison') and 'score_display' in recommendations[0]:
        # Comparison arithmetic is already stored. Presentation does not rerank or recalculate it.
        present_comparison(recommendations[0], None)
    return projected


def present_travel(travel):
    """Display only; duration/unit and precomputed travel scores retain precision."""
    result = dict(travel)
    value = result.get('driving_minutes')
    if value is not None:
        result['driving_minutes'] = rounded(value, 1)
        result['driving_minutes_display'] = f"{rounded(value, 1):g}분"
    else:
        result['driving_minutes_display'] = '확인 불가'
    for field in ('straight_km', 'driving_km'):
        if result.get(field) is not None:
            result[field] = rounded(result[field], 1)
    result['text'] = re.sub(r'(\d+\.\d{2,})(?=분|km)',
                            lambda match: f'{rounded(float(match[1]), 1):g}', result.get('text', ''))
    return result
