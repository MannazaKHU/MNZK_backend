"""1-1, 1-2, 그룹 시간 조율. AI 없이 코드로만 계산한다 (크레딧 0원)."""
import json
import math
from datetime import datetime
from zoneinfo import ZoneInfo

from app import prompts
from app.clients import ai_client as ai
from app.config import settings
from app.errors import ApiError
from app.schemas import check_date

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def all_slots(meeting: dict) -> list:
    """time_range 와 slot_minutes 로 만들 수 있는 슬롯 시작 시각 목록. 예: ['10:00','11:00',...]"""
    sh, sm = map(int, meeting["time_range"]["start"].split(":"))
    eh, em = map(int, meeting["time_range"]["end"].split(":"))
    step = meeting["slot_minutes"]
    return [f"{m // 60:02d}:{m % 60:02d}" for m in range(sh * 60 + sm, eh * 60 + em - step + 1, step)]


def clean_availability(meeting: dict, availability: dict, strict: bool = True) -> dict:
    """후보 날짜·슬롯만 남긴다. strict=True 면 틀린 값이 있을 때 에러."""
    valid_slots = set(all_slots(meeting))
    free_dates = meeting.get("date_selection_mode") == "free"
    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    result = {}
    for date, times in availability.items():
        try:
            check_date(date)
        except ValueError:
            if strict:
                raise ApiError(400, "INVALID_DATE", "날짜는 YYYY-MM-DD 형식의 실제 날짜여야 해요.") from None
            continue
        if free_dates and date < today:
            if strict:
                raise ApiError(400, "INVALID_DATE", "오늘 이후의 날짜를 선택해 주세요.")
            continue
        if not free_dates and date not in meeting["candidate_dates"]:
            if strict:
                raise ApiError(400, "INVALID_DATE", f"{date}는 열려 있는 날짜가 아니에요.")
            continue
        good = sorted({t for t in times if t in valid_slots})
        if strict and len(good) != len(set(times)):
            raise ApiError(400, "INVALID_SLOT", "선택할 수 없는 시간이 들어 있어요.")
        if good:
            result[date] = good
    return result


def heatmap(meeting: dict) -> dict:
    """{날짜: {슬롯: 가능한 인원 수}} - 1-2 화면 색칠용."""
    slots = all_slots(meeting)
    table = {d: {s: 0 for s in slots} for d in meeting["candidate_dates"]}
    for m in meeting["members"]:
        for date, times in m["availability"].items():
            for t in times:
                if date in table and t in table[date]:
                    table[date][t] += 1
    return table


def _end_time(start: str, minutes: int) -> str:
    hour, minute = map(int, start.split(":"))
    end = hour * 60 + minute + minutes
    return f"{end // 60:02d}:{end % 60:02d}"


def best_slots(meeting: dict, min_hours: float = 2, top: int = 5) -> dict:
    """가장 많은 사람이 되는 연속 시간 구간을 순위대로."""
    members = meeting["members"]
    responded = [m for m in members if m.get("availability_submitted", bool(m["availability"]))]
    slots = all_slots(meeting)
    step = meeting["slot_minutes"]
    if not math.isfinite(min_hours) or not 0 < min_hours <= 24 or not 1 <= top <= 50:
        raise ApiError(400, "VALIDATION_ERROR", "최소 시간은 0 초과 24 이하, 결과 수는 1~50이어야 해요.")
    need = max(1, math.ceil(min_hours * 60 / step))

    def find(need_slots: int) -> list:
        best = {}  # (날짜, 가능한 사람들) -> 가장 긴 구간
        for date in meeting["candidate_dates"]:
            who = [{m["name"] for m in members if s in m["availability"].get(date, [])} for s in slots]
            for i in range(len(slots)):
                common = set(who[i])
                for j in range(i, len(slots)):
                    common &= who[j]
                    if not common:
                        break
                    length = j - i + 1
                    if length < need_slots:
                        continue
                    key = (date, frozenset(common))
                    if key not in best or length > best[key][2]:
                        best[key] = (i, j, length)
        items = []
        for (date, people), (i, j, length) in best.items():
            items.append({
                "date": date,
                "start": slots[i],
                "end": _end_time(slots[j], step),
                "hours": round(length * step / 60, 1),
                "available_count": len(people),
                "available_members": sorted(people),
                "missing_members": sorted(m["name"] for m in members if m["name"] not in people),
            })
        items.sort(key=lambda x: (-x["available_count"], -x["hours"], x["date"], x["start"]))
        return items

    result = find(need)
    relaxed = False
    if not result and need > 1:  # 조건이 너무 빡빡하면 1슬롯으로 완화
        result, relaxed = find(1), True

    return {
        "total_members": len(members),
        "responded_members": len(responded),
        "relaxed": relaxed,
        "best": result[:top],
        "heatmap": heatmap(meeting),
    }


async def parse_availability_text(meeting: dict, text: str, *, selected_dates: list[str] | None = None) -> dict:
    """(Should) '토요일 저녁 다 돼' -> {'availability': {...}, 'note': '...'}"""
    dates = selected_dates if selected_dates is not None else meeting["candidate_dates"]
    if not dates:
        raise ApiError(400, "DATES_REQUIRED", "달력에서 날짜를 고른 뒤 selected_dates에 보내 주세요.")
    clean_availability(meeting, {d: [] for d in dates})
    # AI/preview output is confined to the selected dates, even in a free-date meeting.
    meeting = {**meeting, "candidate_dates": dates, "date_selection_mode": "fixed"}
    slots = all_slots(meeting)
    if settings.MOCK_MODE:
        if "저녁" in text:
            picked = [s for s in slots if s >= "18:00"]
        elif "오후" in text:
            picked = [s for s in slots if "12:00" <= s < "18:00"]
        else:
            picked = slots
        avail = {d: picked for d in meeting["candidate_dates"]}
        return {"availability": clean_availability(meeting, avail, strict=False),
                "note": "MOCK: 모든 후보 날짜에 같은 시간으로 해석했어요."}

    dates = [f"{d}({WEEKDAYS[datetime.strptime(d, '%Y-%m-%d').weekday()]})"
             for d in meeting["candidate_dates"]]
    user = json.dumps({"후보날짜": dates, "선택가능슬롯": slots, "문장": text}, ensure_ascii=False)
    out = await ai.structured(prompts.AVAILABILITY_SYSTEM, user,
                               prompts.AVAILABILITY_SCHEMA, step="parse_availability")
    avail = {d.get("date", "")[:10]: d.get("slots", []) for d in out.get("days", [])}
    return {"availability": clean_availability(meeting, avail, strict=False),
            "note": out.get("note", "")}
