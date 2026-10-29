"""Extract condition observations using the existing AI adapter; never search or fetch URLs."""
import json

from app.clients import ai_client
from app.config import settings
from app.services.blog_evidence import Extraction, SYSTEM


class ExtractionUnavailable(Exception):
    """No configured real extractor; never fabricate observations in mock mode."""


async def extract_condition_evidence(place, conditions, documents, schedule):
    if settings.AI_PROVIDER != 'chatkhu':
        raise ExtractionUnavailable()
    payload = {
        'place': {key: place.get(key) for key in ('id', 'name', 'address', 'branch_name')},
        'conditions': [{key: c[key] for key in
                        ('id', 'category', 'target', 'alternatives', 'direction', 'scope', 'operator', 'value', 'unit')}
                       for c in conditions],
        'schedule': {key: schedule.get(key) for key in ('date', 'start', 'end')},
        # URLs, dates, origins and credentials are deliberately not model-generated fields.
        'untrusted_external_documents': [
            {'document_index': i, 'title': d['title'], 'description': d['description']}
            for i, d in enumerate(documents)
        ],
    }
    return await ai_client.structured(SYSTEM, json.dumps(payload, ensure_ascii=False),
                                      Extraction.model_json_schema(), step='blog_extract')
