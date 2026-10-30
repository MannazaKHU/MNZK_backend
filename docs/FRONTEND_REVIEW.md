# 프론트 입력과 전원 응답 자동 추천

검토 기준: `만나자쿠 연두.html`은 버튼·입력 항목 참고용입니다.
목업의 통신 코드·샘플 값·화면 완성도를 구현 요구사항으로 판단하지 않습니다.

## 프론트 접속 주소

- 공개 주소: https://mannajaku-flutter-production-75f1.up.railway.app
- Railway 내부 수신 포트: `8080`
- 로컬 개발 주소: `http://localhost:8080` 또는 `http://127.0.0.1:8080`

백엔드의 `CORS_ORIGINS`에는 공개 HTTPS 주소를 `:8080` 없이 등록합니다.
프론트의 API 기본 주소는 백엔드 서비스의 공개 HTTPS 주소로 설정합니다.
Railway Variables 적용 방법은 [배포 가이드](DEPLOYMENT.md#6-프론트-공개-주소-연결)를 참고합니다.

## 확정한 흐름

1. 방장이 그룹 생성 시 방장 포함 총 인원 N명을 지정합니다.
2. 각 참가자가 가능한 날짜·시간과 출발지·선호·비선호를 함께 입력합니다.
3. 서로 다른 참가자의 완료 응답이 N명 미만이면 대기 상태입니다.
4. N명 전원이 제출하면 백엔드가 공통 시간·추천 장소를 자동으로 계산합니다.
5. 프론트는 GET으로 상태를 조회해 대기·처리·추천 결과 화면을 표시합니다.
6. 방장은 준비된 추천 중 최종 장소를 선택할 수 있습니다.

예시의 4명은 고정값이 아닙니다. API의 `expected_member_count`를 기준으로 판정합니다.
현재 서비스 인원 상한은 10명이며 방장도 포함합니다.
모임 가입만으로 입력 완료로 세지 않으며 같은 참여자의 재전송도 인원을 늘리지 않습니다.

## 역할과 연결

| 프론트 동작 | API·서버 역할 |
|---|---|
| 그룹 인원·후보 조건 설정 | POST /api/meetings |
| 초대 코드로 참여 | POST /api/meetings/{id}/members, 현재는 모임 ID·이름도 필요 |
| 날짜·시간·출발지·선호·비선호 함께 제출 | PUT /api/meetings/{id}/members/me/submission |
| N명 중 제출 완료·남은 인원 표시 | GET /api/meetings/{id}의 progress |
| 처리 중·결과 화면 표시 | GET의 status와 recommendation_result |
| 장소 최종 선택 | POST /api/meetings/{id}/place/confirm |

추천·교집합 계산·검색·순위·이동 정보 계산·저장은 모두 백엔드가 담당합니다.
프론트는 입력 전달과 서버 결과 렌더링을 담당합니다.

## 상태 처리

| status | 표시 |
|---|---|
| waiting | 입력 완료 인원 / 목표 인원, 남은 인원 |
| processing | 전원 제출 완료, 처리 중 |
| recommended | 서버가 계산한 공통 시간과 장소 목록 |
| finalized | 최종 선택한 장소 |
| schedule_conflict | 전원 공통 시간이 없어 일정 수정 필요 |
| failed | 응답은 저장됐지만 추천 실패, 오류·재시도 표시 |

입력을 먼저 모두 받고 서버가 공통 시간을 선택하므로 수동 시간 확정 단계는 없습니다.
공통 시간이 없을 때 일부 인원만으로 추천하지 않습니다.
현재 서버는 전원에게 가능한 가장 긴 연속 구간을 선택합니다.
이동 시간 자체를 최소화하는 최적화·영업 여부 검증은 포함하지 않습니다.

## 전송 계약

- 생성 인원: expected_member_count 필수
- 슬롯: API는 30분 또는 60분을 지원. 30분을 쓰려면 slot_minutes: 30
- 날짜·시간: YYYY-MM-DD, HH:MM. 종료 24:00은 현재 지원하지 않음
- 최종 제출: availability, desired_text, avoided_text, 출발지 이름 또는 좌표
- 가입·응답·결과 상태를 복원하려면 모임 ID와 본인 토큰 사용
- 결과 자동 전환은 GET 주기적 조회로 구현. 현재 WebSocket·이벤트 푸시 없음
- 마지막 제출자는 처리 완료까지 요청 응답을 기다림. 다른 참가자는 GET으로 processing 확인
- 동일 응답 재전송은 카운트·AI 호출을 중복 증가시키지 않음
- 응답 수정 시 이전 추천·최종 장소를 해제하고 전원 제출 상태면 자동 재처리

## DB 영향

기존 3개 업무 테이블을 유지하며 다음 필드를 추가했습니다.

- meetings.expected_member_count: 동적 목표 인원
- meetings.recommendation_options: 모임 전체 추천 옵션
- meetings.processing_started_at / processing_error: 처리 시작·실패 상태
- members.submitted_at: 전체 응답 제출 완료 표시
- members.preference JSON: 출발지·희망·회피 원문과 제출 입력 보존

전원 응답 집계·자동 처리 기능은 현재 구현이며,
별도 계정·채팅·짧은 코드 단독 참여는 버튼 존재만으로 추가하지 않았습니다.
현재 계약은 [API.md](API.md), 필드 명세는 [DATABASE.md](DATABASE.md)를 참고합니다.
