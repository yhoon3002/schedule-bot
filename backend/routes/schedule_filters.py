# 필터 & where 해석 유틸

# 이 모듈은 두 가지를 담당함
# 1) _apply_filters: 이미 조회된 원본 Google Calendar 이벤트 리스트에 다양한 세부 필터(제목/설명/위치/참석자/종일/기간/상태/캘린더/종료시각 등)를 적용하여 결과를 좁혀줌.
# 2) _resolve_where: 사용자가 자연어로 지정한 조건(where 객체)을 실제 이벤트 후보 리스트로 해석함.
#   경계값 누락을 막기 위해 from/to에 하루 패딩을 넣어 조회한 뒤, 동일한 필터 체인을 적용함.
#
# 필터 파라미터 구조 예시(filters)"
# {
#   "title_includes": ["운동, "헬스"],  # 모두 제목에 포함되어야 함(AND)
#   "title_excludes": ["취소"],   # 하나라도 있으면 제외
#   "description_includes": ["하체"],
#   "description_excludes": ["미정"],
#   "location_includes": ["강남"],
#   "location_excludes": ["온라인"],
#   "has_attendees": true,  # 참석자 유무 필터(true/false)
#   "attendee_emails_includes": ["a@b.com"],    # 특정 이메일이 참석자에 포함되는가
#   "has_location": true,   # 위치 유무 필터(true/false)
#   "is_all_day": false,    # 종일 이벤트 여부 필터(true/false)
#   "min_duration_minutes": 30, # 최소 지속시간(분)
#   "max_duration_minutes": 120 # 최대 지속시간(분)
#   "status": "confirmed",  # 이벤트 상태(ex: confirmed/cancelled)
#   "calendar_ids_includes": ["primary"],   # 허용 캘린더 ID 화이트리스트

#   시간 관련: 종료시각 기반 필터(모두 KST 기준으로 내부에서 변환 처리됨)
#   "end_before": "2025-08-28T12:00:00+09:00",    # 이 시각 이전에 끝나야 함
#   "end_after": "2025-08-28T09:00:00+09:00",   # 이 시각 이후에 끝나야 함
#   "end_time_equals": "10:30", # 종료시각의 시:분이 정확히 일치
#   "starts_on_date": "2025-08-28", # 시작 날짜(yyyy-mm-dd) 일치
#   "ends_on_date": "2025-08-28"    # 종료 날짜(yyyy-mm-dd) 일치
# }
#
# where 파라미터 구조 예시(where):
# {
#   "from": "2025-08-28T00:00:00+09:00",    # 조회 하한(포함)
#   "to": "2025-08-28T23:59:59+09:00",  # 조회 상한(제외)
#   "query": "운동",  # 자유 텍스트 검색어
#   "include_holidays": false,  # 공휴일 캘린더 포함 여부
#   "include_birthdays": flase, # 생일 캘린더 포함 여부
#   "filters": { ... 위의 filters 예시 ...}
# }

from typing import List, Optional
from datetime import timedelta
from routes.google_calendar import gcal_list_events_all
from routes.schedule_time import _get_kst, _parse_dt, _rfc3339, _parse_hhmm


def _ci_contains(text: Optional[str], needle: str) -> bool:
    """
    대소문자 무시 부분 포함 검사(contains, case-insensitive)

    :param text: 검사 대상 문자열(없으면 False)
    :param needle: 포함 여부를 확인할 문자열
    :return: needle이 text에 포함되어 있으면 True
    """

    if text is None:
        return False
    try:
        return needle.lower() in text.lower()
    except Exception:
        return False


def _any_ci_contains(text: Optional[str], needles: List[str]) -> bool:
    """
    모든 needle이 text에 부분 포함되어야 True(AND 조건)

    :param text: 검사 대상 문자열
    :param needles: 포함되어야 하는 문자열 목록(모두 만족 시 True)
    :return: AND 조건으로 모두 포함되면 True, needles가 비면 True
    """

    return all(_ci_contains(text, n) for n in needles) if needles else True


def _none_ci_contains(text: Optional[str], needles: List[str]) -> bool:
    """
    needles 중 하나라도 text에 포함되면 False(NOT ANY 조건)

    :param text: 검사 대상 문자열
    :param needles: 포함되면 안되는 문자열 목록
    :return: 어떤 것도 포함되지 않으면 True, needles가 비면 True
    """

    return not any(_ci_contains(text, n) for n in needles) if needles else True


def _attendee_emails(e: dict) -> List[str]:
    """
    이벤트 객체에서 참석자 이메일 목록을 소문자로 추출

    :param e: Google Calendar 이벤트 JSON(dict)
    :return: ["a@b.com", "c@d.com", ...]
    """

    return [a.get("email", "").lower() for a in (e.get("attendees") or []) if a.get("email")]


def _is_all_day_event(e: dict) -> bool:
    """
    종일 이벤트 여부 판단
    - start.date가 있고 start.dateTime이 없으면 종일 이벤트로 간주

    :param e: 이벤트 객체
    :return: 종일이면 True
    """

    s = e.get("start", {})
    return "date" in s and "dateTime" not in s


def _duration_minutes(e: dict):
    """
    이벤트 지속 시간을 분 단위로 계산(KST 기준)
    - 시작/종료 시각이 모두 있을 때만 계산

    :param e: 이벤트 객체
    :return: 지속 시간(분), 계산 불가 시 None
    """

    st = _get_kst(e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"))
    ed_raw = e.get("end", {}).get("dateTime") or e.get("end", {}).get("date")
    ed = _get_kst(ed_raw) if ed_raw else None
    if st and ed:
        return int((ed - st).total_seconds() // 60)
    return None


def _end_kst(e: dict):
    """
    이벤트 종료 시각을 KST datetime으로 반환

    :param e: 이벤트 객체
    :return: 종료 datetime(KST) 또는 None
    """

    ed_raw = e.get("end", {}).get("dateTime") or e.get("end", {}).get("date")
    return _get_kst(ed_raw) if ed_raw else None


def _apply_filters(items: List[dict], filters: Optional[dict]) -> List[dict]:
    """
    원본 Google Calendar 이벤트들에 세부 필터를 적용함
    (정렬 순서는 입력 리스트의 순서를 그대로 유지함)

    :param items: 필터링 대상 이벤트 리스트
    :type items: List[dict]
    :param filters: 제목/설명/위치 포함-제외, 참석자/종일/기간/상태/캘린더/종료시각 등
    :type filters: Optional[dict]
    :return: 필터링된 이벤트 리스트(원래 정렬 유지)
    :rtype: List[dict]
    """

    if not filters: return items

    # 문자열 계열 필터들 파싱
    ti = filters.get("title_includes") or []
    te = filters.get("title_excludes") or []
    di = filters.get("description_includes") or []
    de = filters.get("description_excludes") or []
    li = filters.get("location_includes") or []
    le = filters.get("location_excludes") or []

    # 플래그/수치 필터들 파싱
    has_at = filters.get("has_attendees", None)
    email_in = [x.lower() for x in (filters.get("attendee_emails_includes") or [])]
    has_loc = filters.get("has_location", None)
    is_all_day = filters.get("is_all_day", None)
    min_d = filters.get("min_duration_minutes", None)
    max_d = filters.get("max_duration_minutes", None)
    status = (filters.get("status") or "").lower().strip()
    cals_in = [x for x in (filters.get("calendar_ids_includes") or [])]

    # 시간 관련 필터(종료 기준/특정 종료시각/특정 시작/종료 날짜)
    end_before = _parse_dt(filters.get("end_before")) if filters.get("end_before") else None
    end_after  = _parse_dt(filters.get("end_after"))  if filters.get("end_after")  else None
    end_time_eq_str = filters.get("end_time_equals")
    end_time_eq = _parse_hhmm(end_time_eq_str) if end_time_eq_str else None
    starts_on = (filters.get("starts_on_date") or "").strip()
    ends_on   = (filters.get("ends_on_date") or "").strip()

    out = []
    for e in items:
        # 안전한 접근
        title = e.get("summary") or ""
        desc = e.get("description") or ""
        loc  = e.get("location") or ""
        emails = _attendee_emails(e)
        dur = _duration_minutes(e)
        st = (e.get("status") or "").lower()
        cal_id = e.get("_calendarId") or "primary"

        # 포함/제외 문자열 필터
        if not _any_ci_contains(title, ti):
            continue
        if not _none_ci_contains(title, te):
            continue
        if not _any_ci_contains(desc, di):
            continue
        if not _none_ci_contains(desc, de):
            continue
        if not _any_ci_contains(loc, li):
            continue
        if not _none_ci_contains(loc, le):
            continue

        # 참석자/위치/종일 여부 필터
        if has_at is True and len(emails) == 0:
            continue
        if has_at is False and len(emails) > 0:
            continue
        if email_in:
            # 참석자 이메일 소문자 집합과 교집합이 있으면 통과
            lower_emails = set(emails)
            if not any(any(em in ae for ae in lower_emails) for em in email_in):
                continue
        if has_loc is True and not loc.strip():
            continue
        if has_loc is False and loc.strip():
            continue

        if is_all_day is True and not _is_all_day_event(e):
            continue
        if is_all_day is False and _is_all_day_event(e):
            continue

        # 지속시간/상태/캘린더 필터
        if min_d is not None and (dur is None or dur < int(min_d)):
            continue
        if max_d is not None and (dur is None or dur > int(max_d)):
            continue
        if status and st != status:
            continue
        if cals_in and cal_id not in cals_in:
            continue

        # 종료 시각 기반/날짜 일치 필터
        st_dt = _get_kst(e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"))
        ed_dt = _end_kst(e)

        if end_before and (not ed_dt or not (ed_dt < end_before)):
            continue
        if end_after  and (not ed_dt or not (ed_dt > end_after)):
            continue
        if end_time_eq:
            if not ed_dt:
                continue
            hh, mm = end_time_eq
            if not (ed_dt.hour == hh and ed_dt.minute == mm):
                continue
        if starts_on and (not st_dt or st_dt.strftime("%Y-%m-%d") != starts_on):
            continue
        if ends_on   and (not ed_dt or ed_dt.strftime("%Y-%m-%d") != ends_on):
            continue

        out.append(e)
    return out


def _resolve_where(sid: str, where: Optional[dict]) -> List[dict]:
    """
    where 조건을 실제 이벤트 후보로 해석함.
    from/to 경계에 걸린 이벤트를 놓치지 않도록 하루 패딩을 적용하여 조회한 뒤, 동일한 필터 체인을 적용함.

    :param sid: 인증용 세션 ID
    :type sid: str
    :param where: from/to/query/include_* 및 filters를 포함한 조건 객체
    :type where: Optional[dict]
    :return: 조건에 매칭된 이벤트 리스트
    :rtype: List[dict]
    """

    if not where:
        return []

    f_raw = where.get("from")
    t_raw = where.get("to")
    pf = _parse_dt(f_raw)
    pt = _parse_dt(t_raw)

    # 경계 보정: 포함/제외 경계에 걸린 이벤트 커버를 위해 from/todp +-1일 패딩
    f_pad = _rfc3339((pf - timedelta(days=1))) if pf else None
    t_pad = _rfc3339((pt + timedelta(days=1))) if pt else None

    # 기본 조회(패딩이 있으면 패딩값 적용 -> 경계 누락 방지)
    items = gcal_list_events_all(
        sid,
        f_pad if (pf or pt) else where.get("from"),
        t_pad if (pf or pt) else where.get("to"),
        where.get("query") or None,
        bool(where.get("include_holidays", False)),
        bool(where.get("include_birthdays", False)),
    )

    # 동일 필터 체인 적용
    return _apply_filters(items, where.get("filters") or {})