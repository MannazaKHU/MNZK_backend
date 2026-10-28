"""ChatKHU Gateway adapter. Services depend on chat/structured, not vendor payloads."""
import copy
import json
import logging

import httpx
from jsonschema import Draft202012Validator, ValidationError

from app.config import settings
from app.errors import ApiError

logger = logging.getLogger(__name__)
USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "by_step": {}}


def strict_schema(schema: dict) -> dict:
    result = copy.deepcopy(schema)

    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(result)
    Draft202012Validator.check_schema(result)
    return result


async def _post(body: dict, step: str) -> str:
    if settings.AI_PROVIDER != "chatkhu":
        raise ApiError(503, "AI_NOT_CONFIGURED", "실제 AI 호출은 chatkhu 설정이 필요해요.")
    settings.validate()
    try:
        async with httpx.AsyncClient(timeout=settings.AI_TIMEOUT_SECONDS) as client:
            response = await client.post(
                settings.CHATKHU_BASE_URL + "/chat/completions/",
                headers={"Authorization": f"Bearer {settings.CHATKHU_API_KEY}"},
                json={"model": settings.AI_MODEL, **body},
            )
        response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]
        content = choice["message"].get("content")
        if choice.get("finish_reason") == "length" or not isinstance(content, str) or not content.strip():
            raise ValueError("empty, refused or truncated completion")
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        # Never include a key, user text or the upstream response body in the API error/log.
        logger.warning("ChatKHU request failed at step %s", step)
        raise ApiError(502, "AI_ERROR", "AI 응답을 가져오지 못했어요. 잠시 후 다시 시도해 주세요.")
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}

    def tokens(key):
        value = usage.get(key)
        return value if type(value) is int and value >= 0 else 0

    USAGE["calls"] += 1
    USAGE["prompt_tokens"] += tokens("prompt_tokens")
    USAGE["completion_tokens"] += tokens("completion_tokens")
    USAGE["by_step"][step] = USAGE["by_step"].get(step, 0) + 1
    return content.strip()


def _messages(system: str, user: str):
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def chat(system: str, user: str, step: str) -> str:
    return await _post({"messages": _messages(system, user),
                        "max_tokens": settings.AI_MAX_TOKENS}, step)


async def structured(system: str, user: str, schema: dict, step: str) -> dict:
    schema = strict_schema(schema)
    content = await _post({
        "messages": _messages(system, user), "max_tokens": settings.AI_MAX_TOKENS,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": step, "strict": True, "schema": schema,
        }},
    }, step)
    try:
        result = json.loads(content)
        Draft202012Validator(schema).validate(result)
    except (ValueError, ValidationError):
        raise ApiError(502, "AI_ERROR", "AI 응답 형식이 올바르지 않아요. 다시 시도해 주세요.")
    return result
