"""Frontend-free API scenario. Default: isolated SQLite + mocks. --base-url: real server."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta
import math
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx


class FlowError(Exception):
    pass


def error_code(payload):
    if not isinstance(payload, dict):
        return ""
    detail = payload.get("detail") or payload.get("processing_error")
    code = detail.get("code") if isinstance(detail, dict) else None
    allowed = {"AI_ERROR", "MAP_ERROR", "VALIDATION_ERROR", "LOCATION_NOT_FOUND", "DATABASE_UNAVAILABLE",
               "TOKEN_REQUIRED", "INVALID_TOKEN", "MEETING_NOT_FOUND", "MEMBER_CONFLICT", "INVALID_INVITE",
               "MEETING_CHANGED", "SUBMISSION_CHANGED", "PROCESSING_FAILED", "PROCESSING_INTERRUPTED",
               "NO_CANDIDATES", "NO_COMMON_SCHEDULE"}
    return f" ({code})" if code in allowed else ""


def request(client, method, path, *, expected=200, token=None, payload=None):
    response = client.request(method, path, headers={"Authorization": f"Bearer {token}"} if token else {},
                              **({"json": payload} if payload is not None else {}))
    if response.status_code != expected:
        # Do not print full responses, headers, invitation codes or bearer tokens.
        try:
            code = error_code(response.json())
        except ValueError:
            code = ""
        raise FlowError(f"{method} {path}: HTTP {response.status_code}{code} (expected {expected})")
    try:
        return response.json()
    except ValueError:
        raise FlowError(f"{method} {path}: JSON 응답을 확인할 수 없어요.") from None


def require(condition, message):
    if not condition:
        raise FlowError(message)


def run_flow(client, *, members=2, emit=print):
    require(1 <= members <= 10, "참여 인원은 1~10명이어야 해요.")
    require(request(client, "GET", "/health").get("ok"), "서버 상태를 확인해 주세요.")
    require(request(client, "GET", "/health/ready").get("database") == "ready", "DB 준비 상태를 확인해 주세요.")
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    day = (now + timedelta(days=30)).date().isoformat()
    host = request(client, "POST", "/api/meetings", expected=201, payload={
        "title": f"API 테스트 {now:%m%d-%H%M%S}", "host_name": "테스트방장",
        "expected_member_count": members,
    })
    meeting_id = host["meeting"]["id"]
    path = "/api/meetings/" + meeting_id
    emit(f"모임 생성: OK (meeting_id={meeting_id})")
    require(host["meeting"].get("date_selection_mode") == "free", "자유 날짜 모임이 아니에요. 최신 API 배포를 확인해 주세요.")
    people = [host]
    for index in range(1, members):
        people.append(request(client, "POST", path + "/members", expected=201,
                              payload={"name": f"테스트참여자{index}", "invite_code": host["meeting"]["invite_code"]}))
    emit(f"참여: OK ({members}/{members})")
    for index, person in enumerate(people, 1):
        # Public station locations, no actual user location or personal input.
        origin = (dict(text="강남역", lat=37.4979, lng=127.0276) if index % 2 else
                  dict(text="역삼역", lat=37.5006, lng=127.0365))
        meeting = request(client, "PUT", path + "/members/me/submission", token=person["access_token"], payload={
            "availability": {day: ["18:00", "18:30", "19:00", "19:30"]}, "start_location": origin,
            "desired_text": "조용한 카페", "avoided_text": "",
        })
        progress = meeting["progress"]
        require(progress["submitted_member_count"] == index, "제출 인원 집계가 기대한 값과 달라요.")
        if index < members:
            require(meeting["status"] == "waiting" and not meeting["recommendations"], "전원 입력 전 대기 상태가 아니에요.")
        emit(f"응답 {index}/{members}: {meeting['status']}")
    require(meeting["status"] == "recommended", f"추천 처리 상태를 확인해 주세요: {meeting['status']}{error_code(meeting)}")
    require(meeting["progress"]["all_submitted"], "전원 제출 상태를 확인해 주세요.")
    require(meeting["confirmed"]["date"] == day and meeting["confirmed"]["start"] == "18:00"
            and meeting["confirmed"]["end"] == "20:00", "공통 시간 계산 결과가 예상과 달라요.")
    latest = request(client, "GET", path, token=host["access_token"])
    result = meeting["recommendation_result"]
    require(result is not None and latest["recommendation_result"] == result
            and latest["recommendations"] == meeting["recommendations"], "추천 저장·재조회 결과가 달라요.")
    emit("공통 시간·저장·재조회: OK")
    emit(f"추천 장소: {len(meeting['recommendations'])}개")
    for row in meeting["recommendations"]:
        emit(f"  {row['rank']}위 {row['place']['name']} ({row['score']:.1f}점)")
    blog = result.get("blog_enrichment", {})
    emit(f"블로그 연결: {blog.get('connection_status', 'unknown')}, "
         f"검색 {blog.get('search_requests_made', 0)}회, 추출 {blog.get('extraction_attempt_count', 0)}회")
    if not meeting["recommendations"]:
        candidates = result.get("candidate_evaluations", [])
        pending = sum(c["eligibility"] == "needs_verification" for c in candidates)
        missing_travel = sum(c["score_status"] == "unavailable" for c in candidates)
        emit(f"추천 처리 완료, 검증된 추천 없음: 확인 필요 {pending}개 / 이동정보 부족 {missing_travel}개")
    return latest


@contextmanager
def mock_client():
    # Only the dependency-overridden temporary SQLite session is used by API requests.
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    from app.config import settings
    from app.database import Base, build_engine, get_session
    from app.main import app
    saved = {name: getattr(settings, name) for name in ("APP_ENV", "AI_PROVIDER", "MAPS_PROVIDER", "ENABLE_BLOG_ENRICHMENT")}
    previous = app.dependency_overrides.get(get_session)
    with TemporaryDirectory(prefix="mnzk-api-flow-") as directory:
        engine = build_engine(f"sqlite:///{directory}/test.sqlite3")
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        def sessions():
            with factory() as session:
                yield session
        try:
            settings.APP_ENV = "test"
            settings.AI_PROVIDER = settings.MAPS_PROVIDER = "mock"
            settings.ENABLE_BLOG_ENRICHMENT = False
            Base.metadata.create_all(engine)
            app.dependency_overrides[get_session] = sessions
            with TestClient(app) as client:
                yield client
        finally:
            if previous is None:
                app.dependency_overrides.pop(get_session, None)
            else:
                app.dependency_overrides[get_session] = previous
            for name, value in saved.items():
                setattr(settings, name, value)
            engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="프론트 없이 모임 API 흐름 확인. 기본은 임시 DB·mock.")
    parser.add_argument("--base-url", help="실제 API 서버 주소. 테스트 모임을 저장하고 실제 AI/지도 이용량이 발생할 수 있습니다.")
    parser.add_argument("--members", type=int, choices=range(1, 11), default=2, metavar="1..10")
    parser.add_argument("--timeout", type=float, default=180, help="실제 서버 요청별 제한 시간(초), 기본 180")
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout은 양의 유한한 초 단위 값이어야 합니다.")
    if args.base_url:
        parsed = urlsplit(args.base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            parser.error("--base-url에는 인증 정보·경로·쿼리가 없는 API 서버 HTTP(S) 주소를 입력하세요.")
    try:
        if args.base_url:
            print("실제 서버 모드: 테스트 모임 1개를 저장하며 AI·지도 API 이용량이 발생할 수 있습니다.")
            timeout = httpx.Timeout(args.timeout, connect=min(10, args.timeout))
            with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=timeout) as client:
                result = run_flow(client, members=args.members)
        else:
            print("로컬 mock 모드: 임시 SQLite 사용, 실제 DB 변경·외부 API 호출 없음.")
            with mock_client() as client:
                result = run_flow(client, members=args.members)
        return 0 if result["recommendations"] else 2
    except httpx.TimeoutException:
        print("요청 시간이 초과됐어요. 제출이 이미 저장됐을 수 있어 자동 재전송하지 않았습니다.")
    except httpx.HTTPError:
        print("API 서버 연결을 확인해 주세요.")
    except FlowError as error:
        print(f"흐름 확인 실패: {error}")
    except (KeyError, TypeError, ValueError):
        print("API 응답 구조가 예상과 달라요. 배포 버전과 API 계약을 확인해 주세요.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
