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
    row['score_breakdown'] = [
        {'key': key, 'label': label, 'score_display': percent(row['raw_scores'][key]) if key in row['weights'] else '입력 없음',
         'weight_percent': rounded(row['weights'].get(key, 0)*100, 1), 'active': key in row['weights']}
        for key, label in [('D', '이동 편의'), ('P', '선호 조건 만족'), ('N', '조건 적합성')]]
    row['travel'] = [present_travel(t) for t in row.get('travel', [])]
    row['travel_text'] = [t.get('text', '') for t in row['travel']]
    travel = row['travel_summary']
    badges = []
    for field, label in (('average_minutes', '평균'), ('maximum_minutes', '최대')):
        value = travel.get(field)
        display = None if value is None else f'{rounded(value, 1):g}분'
        travel[field + '_display'] = display
        if display is not None:
            badges.append({'label': label, 'value': display, 'text': f'{label} {display}'})
    tags = public_reason_tags(row['condition_evaluations'])
    row['reason_tags'] = tags
    row['travel_badges'] = badges
    # Compatibility field: short labels only, never user prose, unknown conditions or comparisons.
    row['reason'] = '\n'.join(tags)
    row['recommendation_card'] = {'reason_tags': tags, 'show_reasons': bool(tags),
                                  'travel_badges': badges, 'score_display': row['score_display']}
    # Display fields must not contain condition originals, participant IDs or unmet requests.
    row['condition_details'] = []
    row['condition_groups'] = []


PUBLIC_FEATURE_TAGS = {
    ('atmosphere', '조용한'): '조용한 분위기',
    ('atmosphere', '조용'): '조용한 분위기',
    ('atmosphere', '대화하기 좋은'): '대화하기 좋음',
    ('environment', '대화하기 좋은'): '대화하기 좋음',
    ('environment', '넓은'): '넓은 공간',
    ('environment', 'indoor'): '실내 공간',
    ('environment', 'outdoor'): '야외 공간',
    ('accessibility', 'parking'): '주차 가능',
    ('accessibility', 'free_parking'): '무료 주차',
    ('accessibility', '역 가까움'): '역에서 가까움',
    ('accessibility', '역에서 가까운'): '역에서 가까움',
}


def public_reason_tags(evaluations):
    tags = []
    for e in evaluations:
        # Only confirmed place attributes are safe for unqualified green feature chips.
        # Do not turn an OR evaluation into a claim that a particular alternative is true.
        if (e['direction'] != 'preference' or e['scope'] != 'place' or e['alternatives']
                or e['status'] != 'satisfied' or e['evidence_level'] != 'confirmed'):
            continue
        label = PUBLIC_FEATURE_TAGS.get((e['category'], e['target']))
        if label and label not in tags:
            tags.append(label)
    return tags[:3]


def present_comparison(first, second):
    # Detailed arithmetic remains available; comparisons never enter the public feature chips.
    first['comparison'].pop('summary', None)


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
            # Older records without structured evaluations must not retain stale private prose.
            row['reason'] = ''
            row['reason_tags'] = []
            row['condition_details'] = []
            row['condition_groups'] = []
            row['recommendation_card'] = {'reason_tags': [], 'show_reasons': False, 'travel_badges': [],
                                           'score_display': percent(row.get('score'))}
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
