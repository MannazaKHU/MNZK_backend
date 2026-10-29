import json
from pathlib import Path
from app.services.recommendation_rules import normalize, score_candidates
conditions = normalize([dict(category='kind',target='짬뽕',scope='menu',raw_text='짬뽕 좋아')], 'member-a')
conditions += normalize([dict(category='kind',target='회',scope='menu',direction='avoidance',raw_text='회 싫어')], 'member-b')
places=[dict(id='fixture-place',name='시연 식당',facts=[dict(scope='menu',target='짬뽕',value=True,source='fixture:menu'),dict(scope='menu',target='회',value=False,source='fixture:verified-complete-menu')])]
travel={'fixture-place':[dict(member_id=mid,driving_minutes=t,source='fixture:driving',mode='driving') for mid,t in zip(['member-a','member-b','member-c'],[12,20,28])]}
ranked,all_=score_candidates(conditions,places,travel)
Path('examples/recommendation_demo.json').write_text(json.dumps(dict(meeting_id='fixture-meeting',conditions=conditions,recommendations=ranked,candidate_evaluations=all_),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
