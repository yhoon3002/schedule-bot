# Google Calendar API 래퍼 모듈
# - access_token 갱신/헤더 구성
# - 캘린더/이벤트 조회/생성/수정/삭제
# - LLM 도구 핸들러들이 직접 이 함수를 호출함
import logging, requests
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Body
from routes.google_oauth import _refresh
from urllib.parse import quote

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/google/calendar", tags=["google-calendar"])

GCAL_BASE = "https://www.googleapis.com/calendar/v3"
KST = timezone(timedelta(hours=9))

# 생일 제외 시 포함할 이벤트 타입(생일만 빼고 나머지는 모두 포함)
_EVENT_TYPES_NO_BIRTHDAY = [
    "default",
    "fromGmail",
    "outOfOffice",
    "workingLocation",
    "focusTime",
]


def _auth_header(session_id: str) -> Dict[str, str]:
    """
    세션 토큰을 새로고침하여 Authorization 헤더를 만든다.

    :param session_id: 토큰 조회에 사용할 세션 식별자
    :type session_id: str
    :return: {"Authorization": "Bearer <access_token>"} 형태의 헤더
    :rtype: Dict[str, str]
    :raises HTTPException: 401 - 미연결 또는 리프레시 실패
    """

    # 토큰 만료 확인 및 필요 시 refresh_token 사용해 갱싱
    tok = _refresh(session_id)
    # Google API 호출에 필요한 Bearer 헤더 구성
    return {"Authorization": f"Bearer {tok['access_token']}"}


def _rfc3339(dt: datetime) -> str:
    """
    datetime을 RFC3339 UTC(Z) 문자열로 반환한다. (timeMin/timeMax 용)

    :param dt: 기준 datetime
    :type dt: datetime
    :return: 'Z'로 끝나는 RFC3339 문자열(UTC)
    :rtype: str
    """
    # Google Calendar list 엔드포인트는 RFC3339(UTC) 권장
    return (
        dt.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _normalize_rfc3339(s: Optional[str]) -> Optional[str]:
    """
    시간 문자열에 타임존이 없으면 'Z'를 덧붙여 RFC3339로 정규화한다.

    # - 이미 'Z' 또는 타임존 오프셋(+09:00/-08:00 등)이 포함되어 있으면 그대로 사용
    # - 없으면 UTC로 간주하여 'Z'를 붙임

    :param s: 타임존 포함 여부가 불명확한 문자열
    :type s: Optional[str]
    :return: RFC3339 규격 문자열 또는 None
    :rtype: Optional[str]
    """

    if not s:
        return None
    # 날짜 부분의 '-'와 구분하기 위해 보통 타임존이 나타나는 'T' 이후에서 오프셋을 검사함
    if "Z" in s or "+" in s or "-" in s[11:]:
        return s
    return s + "Z"


# 캘린더/이벤트 ID를 URL 경로 세그먼트로 안전 인코딩
def _cid(s: str) -> str:
    """
    캘린더 ID를 URL 경로 세그먼트로 안전하게 인코딩한다.

    :param s: 캘린더 ID
    :type s: str
    :return: 인코딩된 캘린더 ID
    :rtype: str
    """

    return quote(s, safe='@._-+%')


def _eid(s: str) -> str:
    """
    이벤트 ID를 URL 경로 세그먼트로 안전하게 인코딩한다.

    :param s: 이벤트 ID
    :type s: str
    :return: 인코딩된 이벤트 ID
    :rtype: str
    """

    return quote(s, safe='@._-+%')


def gcal_list_calendar_list(session_id: str) -> List[Dict[str, Any]]:
    """
    사용자의 캘린더 목록(calendarList)을 조회함. 'selected'가 표시된 캘린더가 있으면 우선 사용함.

    :param session_id: 인증에 사용할 세션 ID
    :type session_id: str
    :return: 캘린더 목록(필요 시 selected만)
    :rtype: List[Dict[str, Any]]
    :raises HTTPException: 502 - Google API 오류
    """

    headers = _auth_header(session_id)
    r = requests.get(f"{GCAL_BASE}/users/me/calendarList", headers=headers, timeout=20)
    if not r.ok:
        logger.error("CalendarList failed: %s | %s", r.status_code, r.text)
        raise HTTPException(502, "Google Calendar list (calendarList) failed")
    items = r.json().get("items", [])
    # 사용자가 UI에서 체크한 'selected'가 있다면 그 캘린더만 사용
    selected = [c for c in items if c.get("selected")]
    return selected or items

def _cal_type(cal: Dict[str, Any]) -> str:
    """
    캘린더 유형을 'holiday' / 'birthday' / 'normal'로 분류한다.

    :param cal: 캘린더 항목
    :type cal: Dict[str, Any]
    :return: 'holiday' | 'birthday' | 'normal'
    :rtype: str
    """

    cid = (cal.get("id") or "").lower()
    summary = (cal.get("summaryOverride") or cal.get("summary") or "").lower()
    if "holiday" in cid or cid.endswith("holiday@group.v.calendar.google.com") or "holiday" in summary:
        return "holiday"
    if (
        cid.startswith("addressbook#")
        or cid.endswith("contacts@group.v.calendar.google.com")
        or "birthday" in cid
        or "birthdays" in summary
        or "생일" in summary
    ):
        return "birthday"
    return "normal"


def _list_events_for_calendar(
    session_id: str,
    calendar_id: str,
    time_min: Optional[str],
    time_max: Optional[str],
    query: Optional[str],
    include_birthdays: bool,
) -> List[Dict[str, Any]]:
    """
    단일 캘린더의 이벤트를 조회한다. 단일 인스턴스 전개(singleEvents) + 시작시간 정렬

    :param session_id: 인증용 세션 ID
    :type session_id: str
    :param calendar_id: 대상 캘린더 ID (예: 'primary')
    :type calendar_id: str
    :param time_min: 하한(포함, RFC3339)
    :type time_min: Optional[str]
    :param time_max: 상한(제외, RFC3339)
    :type time_max: Optional[str]
    :param query: 자유 텍스트 검색어(q)
    :type query: Optional[str]
    :param include_birthdays: 생일 이벤트 포함 여부
    :type include_birthdays: bool
    :return: '_calendarId'가 주석된 이벤트 리스트
    :rtype: List[Dict[str, Any]]
    :raises HTTPException: 502 - Google API 오류
    """

    headers = _auth_header(session_id)
    # list 파라미터: 단일 인스턴스로 전개 및 시작시간 기준 정렬
    params: Dict[str, Any] = {"singleEvents": "true", "orderBy": "startTime", "maxResults": 2500}
    if time_min:
        params["timeMin"] = _normalize_rfc3339(time_min)
    if time_max:
        params["timeMax"] = _normalize_rfc3339(time_max)
    if query:
        params["q"] = query
    # include_birthdays=False면 생일 제외를 위해 eventTypes 지정
    if not include_birthdays:
        params["eventTypes"] = _EVENT_TYPES_NO_BIRTHDAY

    r = requests.get(
        f"{GCAL_BASE}/calendars/{_cid(calendar_id)}/events",
        headers=headers,
        params=params,
        timeout=25,
    )

    if not r.ok:
        logger.error("List events failed(%s) cid=%s | %s", r.status_code, calendar_id, r.text)
        raise HTTPException(502, "Google Calendar list failed")
    items = r.json().get("items", [])
    # 혹시라도 섞여 들어온 birthday 타입을 한 번 더 필터링함
    if not include_birthdays:
        items = [it for it in items if it.get("eventType") != "birthday"]
    # 이후 처리에서 캘린더 출처를 알 수 있게 주석 필드 추가
    for it in items:
        it["_calendarId"] = calendar_id
    return items


def gcal_list_events_all(
    session_id: str,
    time_min: Optional[str],
    time_max: Optional[str],
    query: Optional[str] = None,
    include_holidays: bool = False,
    include_birthdays: bool = False,
) -> List[Dict[str, Any]]:
    """
    사용자의 여러 캘린더에서 이벤트를 모아 시작시간 순으로 반환한다.
    time_min/time_max가 모두 없으면 [오늘 00:00 KST ~ 연말 23:59:59 KST]로 기본값을 설정한다.

    :param session_id: 인증용 세션 ID
    :type session_id: str
    :param time_min: 하한(포함, RFC3339)
    :type time_min: Optional[str]
    :param time_max: 상한(제외, RFC3339)
    :type time_max: Optional[str]
    :param query: 자유 텍스트 검색어
    :type query: Optional[str]
    :param include_holidays: 공휴일 캘린더 포함 여부
    :type include_holidays: bool
    :param include_birthdays: 생일 캘린더 포함 여부
    :type include_birthdays: bool
    :return: 통합 이벤트 리스트
    :rtype: List[Dict[str, Any]]
    """

    # 범위가 전혀 없을 때 합리적인 기본창(오늘 ~ 연말)을 잡아준다
    if not time_min and not time_max:
        now_kst = datetime.now(KST)
        today_start_kst = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_year_kst = datetime(now_kst.year, 12, 31, 23, 59, 59, tzinfo=KST)
        time_min = _rfc3339(today_start_kst)
        time_max = _rfc3339(end_of_year_kst)

    logger.info(
        "[GCAL] list all: timeMin=%s, timeMax=%s, q=%s, incHol=%s, incBday=%s",
        time_min, time_max, query, include_holidays, include_birthdays,
    )

    # 내 캘린더들 조회
    calendars = gcal_list_calendar_list(session_id)
    if not calendars:
        logger.warning("[GCAL] calendarList empty")
        return []

    # holiday/birthday 포함 플래그에 따라 필터
    filtered: List[Dict[str, Any]] = []
    for cal in calendars:
        t = _cal_type(cal)
        if t == "holiday" and not include_holidays:
            continue
        if t == "birthday" and not include_birthdays:
            continue
        filtered.append(cal)

    # 각 캘린더에서 이벤트 수집
    all_items: List[Dict[str, Any]] = []
    for cal in filtered:
        cid = cal.get("id") or "primary"
        try:
            items = _list_events_for_calendar(
                session_id, cid, time_min, time_max, query, include_birthdays
            )
            logger.info("[GCAL] %s -> %d items", cid, len(items))
            all_items.extend(items)
        except HTTPException:
            # 일부 캘린더가 실패해도 전체 실패로 보지 않음
            continue

    # 시작 시각 기준으로 정렬(문자열 비교 안전성 위해 dateTime/date 우선순위 준 키 사용)
    def _start_key(e: Dict[str, Any]):
        s = e.get("start", {})
        return s.get("dateTime") or s.get("date") or ""

    all_items.sort(key=_start_key)
    return all_items


def gcal_get_event(session_id: str, calendar_id: str, event_id: str) -> Dict[str, Any]:
    """
    단일 이벤트를 조회한다.

    :param session_id: 인증용 세션 ID
    :type session_id: str
    :param calendar_id: 대상 캘린더 ID
    :type calendar_id: str
    :param event_id: 이벤트 ID
    :type event_id: str
    :return: `_calendarId`가 주석된 이벤트 객체
    :rtype: Dict[str, Any]
    :raises HTTPException: 502 - Google API 오류
    """

    headers = _auth_header(session_id)
    r = requests.get(
        f"{GCAL_BASE}/calendars/{_cid(calendar_id)}/events/{_eid(event_id)}",
        headers=headers,
        timeout=20,
    )
    if not r.ok:
        logger.error("Get event failed(%s) cid=%s eid=%s | %s", r.status_code, calendar_id, event_id, r.text)
        raise HTTPException(502, "Google Calendar get event failed")
    item = r.json()
    item["_calendarId"] = calendar_id
    return item


def _norm_attendees_for_write(v):
    """
    참석자 입력을 Google Calendar API 형식으로 정규화한다.

    :param v: 문자열 이메일/딕셔너리 혼합 리스트 또는 단일 값
    :type v: Any
    :return: {'email': str, 'displayName'?: str} 리스트 또는 None
    :rtype: Optional[List[Dict[str, str]]]
    """

    if v is None:
        return None
    if not isinstance(v, list):
        v = [v]
    out = []
    for x in v:
        if not x:
            continue
        if isinstance(x, str):
            email = x.strip()
            if email:
                out.append({"email": email})
        elif isinstance(x, dict):
            email = (x.get("email") or x.get("value") or x.get("address") or "").strip()
            if email:
                item = {"email": email}
                dn = x.get("displayName") or x.get("name")
                if dn:
                    item["displayName"] = dn
                out.append(item)
    return out


def gcal_insert_event(
    session_id: str,
    body: Dict[str, Any],
    calendar_id: str = "primary",
    send_updates: Optional[str] = None,
) -> Dict[str, Any]:
    """
    새 이벤트를 생성한다.

    :param session_id: 인증용 세션 ID
    :type session_id: str
    :param body: 이벤트 본문(summary/start/end/description/location/attendees)
    :type body: Dict[str, Any]
    :param calendar_id: 대상 캘린더 ID (기본값 'primary')
    :type calendar_id: str
    :param send_updates: 참석자 메일 발송 제어('all' 또는 'none'), None이면 파라미터 생략
    :type send_updates: Optional[str]
    :return: 생성된 이벤트(`_calendarId` 포함)
    :rtype: Dict[str, Any]
    :raises HTTPExeption: 502 - Google API 오류
    """

    headers = _auth_header(session_id)
    # 원본 훼손 방지
    b = dict(body)
    # summary/title 혼용 대응
    summary = b.get("summary") or b.get("title") or "(제목 없음)"
    start = b.get("start")
    end = b.get("end")

    # 문자열로 둘어온 경우 dateTime으로 래핑
    if isinstance(start, str):
        start = {"dateTime": start}
    if isinstance(end, str):
        end = {"dateTime": end}

    # Google API에 맞는 payload 구성(+ TZ 정규화)
    payload = {
        "summary": summary,
        "start": {"dateTime": _normalize_rfc3339((start or {}).get("dateTime") or (start or {}).get("date"))},
        "end":   {"dateTime": _normalize_rfc3339((end   or {}).get("dateTime")   or (end   or {}).get("date"))},
    }
    if b.get("description"):
        payload["description"] = b["description"]
    if b.get("location"):
        payload["location"] = b["location"]
    if b.get("attendees") is not None:
        att = _norm_attendees_for_write(b.get("attendees"))
        if att:
            payload["attendees"] = att

    params = {}
    if send_updates:
        params["sendUpdates"] = send_updates # 'all'|'none'

    r = requests.post(
        f"{GCAL_BASE}/calendars/{_cid(calendar_id)}/events",
        headers=headers,
        params=params,
        json=payload,
        timeout=20,
    )

    if not r.ok:
        logger.error("Insert event failed: %s | %s", r.status_code, r.text)
        raise HTTPException(502, "Google Calendar insert failed")
    item = r.json()
    item["_calendarId"] = calendar_id
    return item


def gcal_patch_event(
    session_id: str,
    event_id: str,
    body: Dict[str, Any],
    calendar_id: str = "primary",
    send_updates: Optional[str] = None,
) -> Dict[str, Any]:
    """
    기존 이벤트를 패치한다.
    주어진 캘린더에서 404가 나면 모든 캘린더를 탐색해 실제 위치를 찾고 재시도한다.

    :param session_id: 인증용 세션 Id
    :type session_id: str
    :param event_id: 이벤트 ID
    :type event_id: str
    :param body: 변경할 필드(부분 업데이트)
    :type body: Dict[str, Any]
    :param calendar_id: 우선 시도할 캘린더 ID
    :type calendar_id: str
    :param send_updates: 참석자 메일 발송 제어('all' 또는 'none')
    :type send_updates: Optional[str]
    :return: 갱신된 이벤트(`_calendarId` 포함)
    :rtype: Dict[str, Any]
    :raises HTTPException: 502 - 복구 불가 API 오류
    """

    headers = _auth_header(session_id)
    b = dict(body)
    payload: Dict[str, Any] = {}

    # 표준 필드 매핑(suammry/title 혼용)
    if "summary" in b or "title" in b:
        payload["summary"] = b.get("summary") or b.get("title")
    # start/end는 문자열/객체 혼용 -> datetime으로 단일화 + TZ 정규화
    if "start" in b and b["start"]:
        start = b["start"]
        if isinstance(start, str):
            start = {"dateTime": start}
        payload["start"] = {"dateTime": _normalize_rfc3339(start.get("dateTime") or start.get("date"))}
    if "end" in b and b["end"]:
        end = b["end"]
        if isinstance(end, str):
            end = {"dateTime": end}
        payload["end"] = {"dateTime": _normalize_rfc3339(end.get("dateTime") or end.get("date"))}
    if "description" in b:
        payload["description"] = b["description"]
    if "location" in b:
        payload["location"] = b["location"]
    if "attendees" in b:
        att = _norm_attendees_for_write(b.get("attendees"))
        if att is not None:
            payload["attendees"] = att

    params = {}
    if send_updates:
        params["sendUpdates"] = send_updates

    url = f"{GCAL_BASE}/calendars/{_cid(calendar_id)}/events/{_eid(event_id)}"
    r = requests.patch(url, headers=headers, params=params, json=payload, timeout=20)
    if r.ok:
        item = r.json()
        item["_calendarId"] = calendar_id
        return item

    # 404일 때: 잘못된 캘린더로 시도했을 가능성 -> 소유 캘린더들에서 위치 탐색
    if r.status_code == 404:
        logger.warning("Patch 404 on %s @ %s. Retrying by probing calendars...", event_id, calendar_id)
        try:
            for cal in gcal_list_calendar_list(session_id):
                cid = cal.get("id") or "primary"
                probe = requests.get(
                    f"{GCAL_BASE}/calendars/{_cid(cid)}/events/{_eid(event_id)}",
                    headers=headers, timeout=12
                )
                if probe.ok:
                    # 실제 위치 발견 -> 그 캘린더에 재패치
                    url2 = f"{GCAL_BASE}/calendars/{_cid(cid)}/events/{_eid(event_id)}"
                    r2 = requests.patch(url2, headers=headers, params=params, json=payload, timeout=20)
                    if r2.ok:
                        item = r2.json()
                        item["_calendarId"] = cid
                        logger.info("Patch succeeded after probing. event=%s calendar=%s", event_id, cid)
                        return item
        except Exception as e:
            logger.exception("Patch probe failed: %s", e)

    # 그 외 에러는 로그만 남기고 502로 던짐
    logger.error("Patch event failed: %s | %s", r.status_code, r.text)
    raise HTTPException(502, "Google Calendar update failed")


def gcal_delete_event(
    session_id: str, event_id: str, calendar_id: str = "primary"
) -> None:
    """
    이벤트를 삭제한다.

    :param session_id: 인증용 세션 ID
    :type session_id: str
    :param event_id: 삭제할 이벤트 ID
    :type event_id: str
    :param calendar_id: 이벤트가 속한 캘린더 ID
    :type calendar_id: str
    :raises HTTPException: 502 - Google API 오류
    """

    headers = _auth_header(session_id)
    r = requests.delete(
        f"{GCAL_BASE}/calendars/{_cid(calendar_id)}/events/{_eid(event_id)}",
        headers=headers,
        timeout=20,
    )
    if not r.ok:
        logger.error("Delete event failed: %s | %s", r.status_code, r.text)
        raise HTTPException(502, "Google Calendar delete failed")


# (테스트용) REST 핸들러
@router.get("/events")
def list_events(
    session_id: str = Query(...),
    timeMin: Optional[str] = Query(None),
    timeMax: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    include_holidays: bool = Query(False),
    include_birthdays: bool = Query(False),
):
    """
    (테스트용) REST로 이벤트 목록을 반환하다.

    :param session_id: 인증용 세션 ID
    :type session_id: str
    :param timeMin: 하한(포함, RFC3339)
    :type timeMin: Optional[str]
    :param timeMax: 상한(제외, RFC3339)
    :type timeMax: Optional[str]
    :param q: 자유 텍스트 검색어
    :type q: Optional[str]
    :param include_holidays: 공휴일 포함
    :type include_holidays: bool
    :param include_birthdays: 생일 포함
    :type include_birthdays: bool
    :return: {"items": [이벤트...]}
    :rtype: Dict[str, Any]
    """

    items = gcal_list_events_all(session_id, timeMin, timeMax, q, include_holidays, include_birthdays)
    logger.info("[GCAL] REST /events -> %d items", len(items))
    return {"items": items}


@router.get("/events/{event_id}")
def get_event(
    event_id: str,
    session_id: str = Query(...),
    calendar_id: str = Query("primary"),
):
    """
    (테스트용) 단일 이벤트를 반환한다.

    :param event_id: 이벤트 ID
    :type event_id: str
    :param session_id: 인증용 세션 ID
    :type session_id: str
    :param calendar_id: 캘린더 ID
    :type calendar_id: str
    :return: 이벤트 JSON
    :rtype: Dict[str, Any]
    """

    return gcal_get_event(session_id, calendar_id, event_id)


@router.post("/events")
def create_event(
    body: Dict[str, Any] = Body(...),
    session_id: str = Query(...),
    calendar_id: str = Query("primary"),
    send_updates: Optional[str] = Query(None, regex="^(all|none)?$"),
):
    """
    (테스트용) 단일 이벤트를 생성한다.

    :param body: 이벤트 본문
    :type body: Dict[str, Any]
    :param session_id: 인증용 세션 ID
    :type session_id: str
    :param calendar_id: 대상 캘린더 ID
    :type calendar_id: str
    :param send_updates:참석자 메일 발송('all'|'None')
    :type send_updates: Optional[str]
    :return: 생성된 이벤트 JSON
    :rtype: Dict[str, Any]
    """

    item = gcal_insert_event(session_id, body, calendar_id, send_updates)
    return item


@router.patch("/events/{event_id}")
def patch_event(
    event_id: str,
    body: Dict[str, Any] = Body(...),
    session_id: str = Query(...),
    calendar_id: str = Query("primary"),
    send_updates: Optional[str] = Query(None, regex="^(all|none)?$"),
):
    """
    (테스트용) 단일 이벤트를 패치한다.

    :param event_id: 이벤트 ID
    :type event_id: str
    :param body: 변경할 필드
    :type body: Dict[str, Any]
    :param session_id: 인증용 세션 ID
    :type session_id: str
    :param calendar_id: 캘린더 ID
    :type calendar_id: str
    :param send_updates: 참석자 메일 발송('all'|'none')
    :type send_updates: Optional[str]
    :return: 갱신된 이벤트 JSON
    :rtype: Dict[str, Any]
    """

    item = gcal_patch_event(session_id, event_id, body, calendar_id, send_updates)
    return item


@router.put("/events/{event_id}")
def put_event(
    event_id: str,
    body: Dict[str, Any] = Body(...),
    session_id: str = Query(...),
    calendar_id: str = Query("primary"),
    send_updates: Optional[str] = Query(None, regex="^(all|none)?$"),
):
    """
    (테스트용) 단일 이벤트를 덮어쓰듯 업데이트한다. (내부적으로는 patch 사용)

    :param event_id: 이벤트 ID
    :type event_id: str
    :param body: 이벤트 전체/부분 본문
    :type body: Dict[str, Any]
    :param session_id: 인증용 세션 ID
    :type session_id: str
    :param calendar_id: 캘린더 ID
    :type calendar_id: str
    :param send_updates: 참석자 메일 발송('all'|'none')
    :type send_updates: Optional[str]
    :return: 갱신된 이벤트 JSON
    :rtype: Dict[str, Any]
    """

    item = gcal_patch_event(session_id, event_id, body, calendar_id, send_updates)
    return item


@router.delete("/events/{event_id}")
def delete_event(
    event_id: str,
    session_id: str = Query(...),
    calendar_id: str = Query("primary"),
):
    """
    (테스트용) 단일 이벤트를 삭제한다.

    :param event_id: 이벤트 ID
    :type event_id: str
    :param session_id: 인증용 세션 ID
    :type session_id: str
    :param calendar_id: 캘린더 ID
    :type calendar_id: str
    :return: {"ok": True}
    :rtype: Dict[str, bool]
    """

    gcal_delete_event(session_id, event_id, calendar_id)
    return {"ok": True}
