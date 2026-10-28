"""Explicit external connectivity check; never called by the test suite or server startup."""
import argparse
import asyncio
import sys
import httpx
from app.clients import ai_client
from app.config import settings


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--completion", action="store_true",
                        help="Also make one billable structured completion")
    args = parser.parse_args()
    if settings.AI_PROVIDER != "chatkhu":
        print("Set AI_PROVIDER=chatkhu and ChatKHU credentials in .env first.")
        return 1
    settings.validate()
    try:
        async with httpx.AsyncClient(timeout=settings.AI_TIMEOUT_SECONDS) as client:
            response = await client.get(settings.CHATKHU_BASE_URL + "/models/",
                                        headers={"Authorization": f"Bearer {settings.CHATKHU_API_KEY}"})
            response.raise_for_status()
            ids = [m["id"] for m in response.json()["data"]]
        print("Gateway authentication OK; configured model available:", settings.AI_MODEL in ids)
        if settings.AI_MODEL not in ids:
            return 1
        if args.completion:
            result = await ai_client.structured(
                "Return the requested JSON object.", "Set ok to true.",
                {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
                step="smoke",
            )
            print("Structured completion OK:", result == {"ok": True})
        return 0
    except Exception:
        print("Gateway check failed. Check Base URL, credentials, model permissions and network.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
