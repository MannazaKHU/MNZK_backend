"""Run the team interface with handcrafted data only; never invokes an API or database."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from app.recommendation_contracts import RecommendationInputs
from app.services.recommendation_pipeline import evaluate_supplied_inputs
from app.services.recommendation_rules import normalize


async def main():
    root = Path(__file__).resolve().parents[1]
    fixture = json.loads((root / 'tests/fixtures/team_blog_input.json').read_text(encoding='utf-8-sig'))
    conditions = normalize(fixture['condition_items'], 'fixture-member')
    async def mock_extractor(place, conditions, documents, schedule):
        return {'claims': [{**fixture['mock_claim'], 'condition_id': conditions[0]['id']}]}
    result = await evaluate_supplied_inputs(RecommendationInputs.model_validate(fixture['inputs']), conditions,
                                           fixture['schedule'], extractor=mock_extractor,
                                           now=datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
    path = root / 'examples/team_recommendation_result.json'
    path.write_text(json.dumps({'fixture_only': True, 'notice': fixture['notice'], **result},
                               ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Fixture validation only. No API calls or DB writes. Output: examples/team_recommendation_result.json')


if __name__ == '__main__':
    asyncio.run(main())
