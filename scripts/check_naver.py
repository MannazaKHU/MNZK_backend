"""Explicit real API checks. Never called during startup or automated tests.

Each selected check uses the real API even while MAPS_PROVIDER=mock.
No AI calls or database writes. Credentials and upstream bodies are never printed.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

from app.clients import naver_client as naver
from app.config import settings
from app.errors import ApiError


def warn_env_conflicts(names):
    """load_dotenv는 이미 export된 변수를 덮어쓰지 않는다. 값은 출력하지 않는다."""
    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.is_file():
        return
    values = dotenv_values(path)
    conflicts = [name for name in names if name in values and name in os.environ
                 and values[name] != os.environ[name]]
    if conflicts:
        print("WARNING: exported environment overrides .env: " + ", ".join(conflicts))
        print("To use .env in this terminal, run: unset " + " ".join(conflicts))


async def run_checks(*, search: bool, maps: bool) -> int:
    required = []
    if search:
        required += ["NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"]
    if maps:
        required += ["NCP_MAPS_KEY_ID", "NCP_MAPS_KEY"]
    endpoint_names = (["NAVER_SEARCH_URL"] if search else []) + (["NCP_MAPS_BASE"] if maps else [])
    warn_env_conflicts(required + endpoint_names + ["MAPS_TIMEOUT_SECONDS"])
    missing = [name for name in required if not getattr(settings, name).strip()]
    if missing:
        print("Missing configuration: " + ", ".join(missing))
        return 1

    # A process-local override: do not edit .env or switch the running service.
    previous_provider = settings.MAPS_PROVIDER
    settings.MAPS_PROVIDER = "naver"
    failures = 0
    async def check(label, operation):
        nonlocal failures
        try:
            result = await operation()
            if not result:
                failures += 1
                print(f"{label}: FAIL (no usable result; check API permissions and configuration)")
            else:
                print(f"{label}: OK")
        except ApiError as error:
            failures += 1
            print(f"{label}: FAIL ({error.detail['message']})")
        except Exception:
            failures += 1
            print(f"{label}: FAIL (check URL, credentials, permissions and network)")

    try:
        if search:
            await check("NAVER API HUB Local Search", lambda: naver.local_search("강남역 카페", display=1))
        if maps:
            await check("NCP Geocoding", lambda: naver.address_geocode("분당구 불정로 6"))
            await check("NCP Reverse Geocoding", lambda: naver.area_name(37.4979, 127.0276))
            await check("NCP Directions 5", lambda: naver.driving(
                {"lat": 37.4979, "lng": 127.0276}, {"lat": 37.5133, "lng": 127.1001},
                raise_on_error=True))
    finally:
        settings.MAPS_PROVIDER = previous_provider
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description="Real Naver API checks (usage may be billed).")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--search", action="store_true", help="Local Search: 1 request")
    group.add_argument("--maps", action="store_true", help="Geocoding, Reverse Geocoding, Directions 5: 3 requests")
    group.add_argument("--all", action="store_true", help="Search + Maps: 4 requests")
    args = parser.parse_args()
    return asyncio.run(run_checks(search=args.search or args.all, maps=args.maps or args.all))


if __name__ == "__main__":
    sys.exit(main())
