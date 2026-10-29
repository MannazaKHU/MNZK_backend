"""Pure, evidence-based recommendation policy; no network or database calls."""
import hashlib
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from statistics import mean
from datetime import datetime, timezone
from app.conditions import Condition
from app.services.blog_evidence import apply_reviews

@dataclass(frozen=True)
class Policy:
    version: str = "recommendation-v2-blog"
    travel_weight: float = .4
    preference_weight: float = .3
    avoidance_weight: float = .3
    travel_limit_minutes: float = 60
    average_share: float = .5
    maximum_share: float = .5
    unknown_score: float = 50
    review_support_score: float = 75
    review_conflict_score: float = 25

    def __post_init__(self):
        weights = (self.travel_weight, self.preference_weight, self.avoidance_weight)
        if any(not math.isfinite(v) or v < 0 for v in weights) or self.travel_weight <= 0:
            raise ValueError("Travel weight must be positive and other weights nonnegative")
        if self.travel_limit_minutes <= 0 or not math.isfinite(self.travel_limit_minutes):
            raise ValueError("Travel limit must be finite and positive")
        if self.average_share < 0 or self.maximum_share < 0 or not math.isclose(self.average_share+self.maximum_share, 1):
            raise ValueError("Travel shares must be nonnegative and sum to one")
        if any(not 0 <= score <= 100 for score in (self.unknown_score, self.review_support_score, self.review_conflict_score)):
            raise ValueError("Unknown score must be in 0..100")

POLICY = Policy()


def normalize(items, member_id):
    result, seen = [], {}
    for item in items:
        c = Condition.model_validate({k:v for k,v in item.items() if k not in {'id','member_id'}}).model_dump()
        c['target'] = c['target'].strip()
        c['alternatives'] = sorted(set(t.strip() for t in c['alternatives'] if t.strip() and t.strip() != c['target']))
        # Equivalent desired outcomes share one vote, even across polarity.
        outcome = (tuple(sorted([c['target']] + c['alternatives'])), c['direction'])
        if c['target'] in {'조용한', '조용', '시끄러운', '시끄러움'}:
            quiet = c['target'] in {'조용한', '조용'}
            outcome = ('noise', quiet == (c['direction'] == 'preference'))
        key = (c['category'], outcome, (), c['scope'],
               c['operator'], c['value'], c['unit'])
        if key in seen:
            old = result[seen[key] - 1]
            if c['strength'] == 'required': old['strength'] = 'required'
            old['needs_confirmation'] |= c['needs_confirmation']
            continue
        seen[key] = len(result) + 1
        c.update(member_id=member_id, id=hashlib.sha256((str(member_id)+repr(key)).encode()).hexdigest()[:20])
        result.append(c)
    for i, a in enumerate(result):
        for b in result[i+1:]:
            if (a['category'],a['scope'],a['target'],a['alternatives'],a['operator'],a['value'],a['unit']) == (b['category'],b['scope'],b['target'],b['alternatives'],b['operator'],b['value'],b['unit']) and a['direction'] != b['direction']:
                a['needs_confirmation'] = b['needs_confirmation'] = True
    return result


def _fact_time(fact):
    try:
        stamp = datetime.fromisoformat(fact.get('verified_at', '').replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return float('-inf')
        return stamp.timestamp()
    except (ValueError, TypeError, AttributeError):
        return float('-inf')


def evaluate(c, place, policy=POLICY):
    """Facts are explicit records: scope, target, value, unit, source. No absent=false inference."""
    values = []
    sources = []
    targets = [c['target']] + c['alternatives']
    for target in targets:
        facts = [f for f in place.get('facts', []) if f.get('scope') == c['scope']
                 and f.get('target') == target and f.get('source')
                 and f.get('information_type', 'confirmed') == 'confirmed']
        # Existing facts are explicit direct records. New timestamped records take priority.
        facts.sort(key=_fact_time, reverse=True)
        fact = facts[0] if facts else None
        value = None
        if fact:
            v = fact.get('value')
            if c['value'] is not None:
                if fact.get('unit') == c['unit'] and isinstance(v, (int, float)) and not isinstance(v, bool):
                    value = {'eq': v == c['value'], 'lte': v <= c['value'], 'gte': v >= c['value']}[c['operator']]
            elif isinstance(v, bool): value = v
            sources.append(fact['source'])
        elif c['category'] == 'kind' and c['scope'] == 'place' and target in place.get('category', '').split('>'):
            value = True
            sources.append('naver.category')
        values.append(value)
    matched = True if True in values else False if all(v is False for v in values) else None
    desired = matched if c['direction'] == 'preference' else None if matched is None else not matched
    reason = '검증된 속성 근거' if desired is not None else '판단할 속성 정보가 부족합니다.'
    if c['scope'] == 'menu' and c['direction'] == 'avoidance' and matched is True:
        ordering = place.get('ordering', {})
        if ordering.get('source') and ordering.get('individual') is True and ordering.get('alternatives_verified') is True:
            desired = True
            sources.append(ordering['source'])
            reason = '개별 주문과 회피 메뉴 외 선택지가 확인되었습니다. 다른 메뉴 선호를 의미하지 않습니다.'
        elif ordering.get('source') and ordering.get('individual') is False:
            desired = False
            reason = '같은 메뉴를 함께 주문해야 하므로 회피할 수 없습니다.'
        else: desired = None; reason = '개별 주문 가능 여부와 대체 메뉴 확인이 필요합니다.'
    if c['needs_confirmation']: desired = None; reason = '사용자 해석 확인이 필요합니다.'
    direct = {**c, 'status': 'unknown' if desired is None else 'satisfied' if desired else 'unsatisfied',
            'score': policy.unknown_score if desired is None else 100 if desired else 0,
            'evidence': reason, 'sources': sources, 'evidence_facts': [f for f in place.get('facts', []) if f.get('scope') == c['scope'] and f.get('target') in targets and f.get('source')]}
    return apply_reviews(c, direct, place, policy)


def aggregate(evaluations, direction):
    members = defaultdict(lambda: defaultdict(list))
    for e in evaluations:
        if e['strength'] == 'desired' and e['direction'] == direction:
            members[e['member_id']][e['category']].append(e['score'])
    personal = [{ 'member_id': mid, 'categories': {k: mean(v) for k,v in cats.items()},
                  'score': mean([mean(v) for v in cats.values()])} for mid,cats in members.items()]
    return (mean([p['score'] for p in personal]) if personal else None), personal


def travel_score(travel, policy=POLICY):
    minutes, modes = [], set()
    for t in travel:
        v = t.get('duration')
        unit = t.get('unit', 'minutes')
        if v is None: v = t.get('driving_minutes')
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 or unit not in {'minutes','seconds','milliseconds'}:
            return {'score': None, 'average_minutes': None, 'maximum_minutes': None, 'mode': 'driving', 'failure_reason': 'MISSING_OR_INVALID_TRAVEL'}
        minutes.append(v / {'minutes':1,'seconds':60,'milliseconds':60000}[unit])
        modes.add(t.get('mode', 'driving'))
    if not minutes or modes != {'driving'}:
        return {'score': None, 'average_minutes': None, 'maximum_minutes': None, 'mode': None, 'failure_reason': 'UNSUPPORTED_OR_MIXED_TRAVEL'}
    avg, maximum = mean(minutes), max(minutes)
    burden = policy.average_share*avg + policy.maximum_share*maximum
    return {'score': min(100,max(0,100*(1-burden/policy.travel_limit_minutes))), 'average_minutes':avg,
            'maximum_minutes':maximum, 'mode':'driving', 'failure_reason':None}


def score_candidates(conditions, candidates, travels, policy=POLICY):
    results = []
    for place in candidates:
        ev = [evaluate(c,place,policy) for c in conditions]
        required = [e for e in ev if e['strength'] == 'required']
        eligibility = 'excluded' if any(e['status']=='unsatisfied' for e in required) else 'needs_verification' if any(e['status']=='unknown' for e in required) else 'eligible'
        p, personal_p = aggregate(ev,'preference'); n, personal_n = aggregate(ev,'avoidance')
        travel = travels.get(place['id'], [])
        d = travel_score(travel,policy)
        raw = {'D':d['score'],'P':p,'N':n}
        weights = {'D':policy.travel_weight, **({'P':policy.preference_weight} if p is not None else {}), **({'N':policy.avoidance_weight} if n is not None else {})}
        weights = {k:v/sum(weights.values()) for k,v in weights.items()}
        contributions = {k:raw[k]*v if raw[k] is not None else None for k,v in weights.items()}
        total = sum(contributions.values()) if d['score'] is not None else None
        conflicts = []
        for a in ev:
            for b in ev:
                if a['member_id'] < b['member_id'] and a['direction'] != b['direction'] and a['scope']==b['scope'] and set([a['target']]+a['alternatives']) & set([b['target']]+b['alternatives']):
                    if a['status'] != 'satisfied' or b['status'] != 'satisfied': conflicts.append({'condition_ids':[a['id'],b['id']], 'status':'unresolved'})
        results.append({'place':place,'place_id':place['id'],'eligibility':eligibility,'rank':None,'score':total,
                        'score_status':'calculated' if total is not None else 'unavailable','score_failure_reason':d['failure_reason'],
                        'raw_scores':raw,'weights':weights,'contributions':contributions,'travel':travel,'travel_summary':d,
                        'condition_evaluations':ev,'required_checks':required,'personal':{'preference':personal_p,'avoidance':personal_n},
                        'confirmed_condition_count':sum(e['evidence_level']=='confirmed' for e in ev),
                        'estimated_condition_count':sum(e['evidence_level']=='review_estimate' for e in ev),'total_condition_count':len(ev),
                        'unknown_condition_count':sum(e['evidence_level']=='unknown' for e in ev),'conflicts':conflicts,'policy_version':policy.version, 'policy':asdict(policy),
                        'reason':'확보한 정보와 추천 정책으로 계산한 결과입니다.','matched':[e['target'] for e in ev if e['status']=='satisfied'],
                        'blog_enrichment':place.get('blog_enrichment', {}),
                        'warnings':['영업시간·예약 가능 여부는 확인이 필요합니다.', '일부 후보의 검색된 후기 범위에서 평가하며 전체 후보 중 절대 최적을 보장하지 않습니다.'], 'travel_text':[t.get('text','') for t in travel]})
    ranked = sorted((r for r in results if r['eligibility']=='eligible' and r['score'] is not None), key=lambda r:(-r['score'],r['unknown_condition_count'],r['travel_summary']['maximum_minutes'],str(r['place_id'])))
    for i,r in enumerate(ranked,1): r['rank']=i
    if len(ranked)>1:
        a,b=ranked[:2]
        delta={k:a['contributions'][k]-b['contributions'][k] for k in a['contributions']}
        a['comparison']={'place_id':b['place_id'],'contribution_deltas':delta,'total_delta':a['score']-b['score']}
        a['reason']=f"2순위 대비 항목별 기여분 차이: {delta}. 총점 차이: {a['score']-b['score']!r}점 (반올림 전)."
    return ranked, results

