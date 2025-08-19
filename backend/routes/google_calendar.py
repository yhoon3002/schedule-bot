import logging, requests
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Body
from routes.google_oauth import _refresh


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/google/calendar", tags=["google-calendar"])

GCAL_BASE = "https://www.googleapis.com/calendar/v3"
KST = timezone(timedelta(hours=9))

def _auth_header(session_id: str) -> Dict[str, str]:
    tok = _refresh(session_id)
    return {"Authorization": f"Bearer {tok['access_token']}"}

def _rfc3339(dt: datetime) -> str:
    return (
        dt.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

def _normalize_rfc3339(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    if "Z" in s or "+" in s or "-" in s[11:]:
        return s
    return s + "Z"

# ---- 캘린더 리스트 ----
def gcal_list_calendar_list(session_id: str) -> List[Dict[str, Any]]:
    headers = _auth_header(session_id)
    r = requests.get(f"{GCAL_BASE}/users/me/calendarList", headers=headers, timeout=20)
    if not r.ok:
        logger.error("CalendarList failed: %s | %s", r.status_code, r.text)
        raise HTTPException(502, "Google Calendar list (calendarList) failed")
    items = r.json().get("items", [])
    selected = [c for c in items if c.get("selected")]
    return selected or items

def _cal_type(cal: Dict[str, Any]) -> str:
    cid = (cal.get("id") or "").lower()
    summary = (cal.get("summaryOverride") or cal.get("summary") or "").lower()
    if "holiday" in cid or cid.endswith("holiday@group.v.calendar.google.com") or "holiday" in summary:
        return "holiday"
    if cid.startswith("addressbook#") or "birthday" in cid or "birthdays" in summary:
        return "birthday"
    return "normal"

# ---- 개별 캘린더 이벤트 조회 ----
def _list_events_for_calendar(
    session_id: str,
    calendar_id: str,
    time_min: Optional[str],
    time_max: Optional[str],
    query: Optional[str],
) -> List[Dict[str, Any]]:
    headers = _auth_header(session_id)
    params = {"singleEvents": "true", "orderBy": "startTime", "maxResults": 2500}
    if time_min:
        params["timeMin"] = _normalize_rfc3339(time_min)
    if time_max:
        params["timeMax"] = _normalize_rfc3339(time_max)
    if query:
        params["q"] = query

    r = requests.get(
        f"{GCAL_BASE}/calendars/{calendar_id}/events",
        headers=headers,
        params=params,
        timeout=25,
    )
    if not r.ok:
        logger.error(
            "List events failed(%s) cid=%s | %s", r.status_code, calendar_id, r.text
        )
        raise HTTPException(502, "Google Calendar list failed")
    items = r.json().get("items", [])
    for it in items:
        it["_calendarId"] = calendar_id
    return items

# ---- 모든 캘린더에서 모아오기 (기본: 오늘~연말 KST, 공휴일/생일 제외) ----
def gcal_list_events_all(
    session_id: str,
    time_min: Optional[str],
    time_max: Optional[str],
    query: Optional[str] = None,
    include_holidays: bool = False,
    include_birthdays: bool = False,
) -> List[Dict[str, Any]]:
    if not time_min and not time_max:
        now_kst = datetime.now(KST)
        today_start_kst = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_year_kst = datetime(now_kst.year, 12, 31, 23, 59, 59, tzinfo=KST)
        time_min = _rfc3339(today_start_kst)
        time_max = _rfc3339(end_of_year_kst)

    logger.info(
        "[GCAL] list all: timeMin=%s, timeMax=%s, q=%s, incHol=%s, incBday=%s",
        time_min,
        time_max,
        query,
        include_holidays,
        include_birthdays,
    )

    calendars = gcal_list_calendar_list(session_id)
    if not calendars:
        logger.warning("[GCAL] calendarList empty")
        return []

    filtered: List[Dict[str, Any]] = []
    for cal in calendars:
        t = _cal_type(cal)
        if t == "holiday" and not include_holidays:
            continue
        if t == "birthday" and not include_birthdays:
            continue
        filtered.append(cal)

    all_items: List[Dict[str, Any]] = []
    for cal in filtered:
        cid = cal.get("id") or "primary"
        try:
            items = _list_events_for_calendar(session_id, cid, time_min, time_max, query)
            logger.info("[GCAL] %s -> %d items", cid, len(items))
            all_items.extend(items)
        except HTTPException:
            continue

    def _start_key(e: Dict[str, Any]):
        s = e.get("start", {})
        return s.get("dateTime") or s.get("date") or ""

    all_items.sort(key=_start_key)
    return all_items

# ---- 단건 조회/CRUD ----
def gcal_get_event(session_id: str, calendar_id: str, event_id: str) -> Dict[str, Any]:
    headers = _auth_header(session_id)
    r = requests.get(
        f"{GCAL_BASE}/calendars/{calendar_id}/events/{event_id}",
        headers=headers,
        timeout=20,
    )
    if not r.ok:
        logger.error(
            "Get event failed(%s) cid=%s eid=%s | %s",
            r.status_code,
            calendar_id,
            event_id,
            r.text,
        )
        raise HTTPException(502, "Google Calendar get event failed")
    item = r.json()
    item["_calendarId"] = calendar_id
    return item

def gcal_insert_event(
    session_id: str,
    body: Dict[str, Any],
    calendar_id: str = "primary",
    send_updates: Optional[str] = None,  # "all" | "none" | None
) -> Dict[str, Any]:
    headers = _auth_header(session_id)

    b = dict(body)
    summary = b.get("summary") or b.get("title") or "(제목 없음)"
    start = b.get("start")
    end = b.get("end")
    if isinstance(start, str):
        start = {"dateTime": start}
    if isinstance(end, str):
        end = {"dateTime": end}
    payload = {
        "summary": summary,
        "start": {
            "dateTime": _normalize_rfc3339(
                (start or {}).get("dateTime") or (start or {}).get("date")
            )
        },
        "end": {
            "dateTime": _normalize_rfc3339(
                (end or {}).get("dateTime") or (end or {}).get("date")
            )
        },
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
        params["sendUpdates"] = send_updates

    r = requests.post(
        f"{GCAL_BASE}/calendars/{calendar_id}/events",
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
    send_updates: Optional[str] = None,  # "all" | "none" | None
) -> Dict[str, Any]:
    headers = _auth_header(session_id)
    b = dict(body)
    payload: Dict[str, Any] = {}

    if "summary" in b or "title" in b:
        payload["summary"] = b.get("summary") or b.get("title")
    if "start" in b and b["start"]:
        start = b["start"]
        if isinstance(start, str):
            start = {"dateTime": start}
        payload["start"] = {
            "dateTime": _normalize_rfc3339(start.get("dateTime") or start.get("date"))
        }
    if "end" in b and b["end"]:
        end = b["end"]
        if isinstance(end, str):
            end = {"dateTime": end}
        payload["end"] = {
            "dateTime": _normalize_rfc3339(end.get("dateTime") or end.get("date"))
        }
    if "description" in b:
        payload["description"] = b["description"]
    if "location" in b:
        payload["location"] = b["location"]
    if "attendees" in b:
        att = _norm_attendees_for_write(b.get("attendees"))
        # None -> 변경안함, [] -> 전부 제거, [{email..}] -> 설정
        if att is not None:
            payload["attendees"] = att

    params = {}
    if send_updates:
        params["sendUpdates"] = send_updates

    r = requests.patch(
        f"{GCAL_BASE}/calendars/{calendar_id}/events/{event_id}",
        headers=headers,
        params=params,
        json=payload,
        timeout=20,
    )
    if not r.ok:
        logger.error("Patch event failed: %s | %s", r.status_code, r.text)
        raise HTTPException(502, "Google Calendar update failed")
    item = r.json()
    item["_calendarId"] = calendar_id
    return item

def gcal_delete_event(
    session_id: str, event_id: str, calendar_id: str = "primary"
) -> None:
    headers = _auth_header(session_id)
    r = requests.delete(
        f"{GCAL_BASE}/calendars/{calendar_id}/events/{event_id}",
        headers=headers,
        timeout=20,
    )
    if not r.ok:
        logger.error("Delete event failed: %s | %s", r.status_code, r.text)
        raise HTTPException(502, "Google Calendar delete failed")

# ================== REST (옵션) ==================
@router.get("/events")
def list_events(
    session_id: str = Query(...),
    timeMin: Optional[str] = Query(None),
    timeMax: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    include_holidays: bool = Query(False),
    include_birthdays: bool = Query(False),
):
    items = gcal_list_events_all(session_id, timeMin, timeMax, q, include_holidays, include_birthdays)
    logger.info("[GCAL] REST /events -> %d items", len(items))
    return {"items": items}

@router.get("/events/{event_id}")
def get_event(
    event_id: str,
    session_id: str = Query(...),
    calendar_id: str = Query("primary"),
):
    return gcal_get_event(session_id, calendar_id, event_id)

@router.post("/events")
def create_event(
    body: Dict[str, Any] = Body(...),
    session_id: str = Query(...),
    calendar_id: str = Query("primary"),
    send_updates: Optional[str] = Query(None, regex="^(all|none)?$"),
):
    """
    body 예시:
    {
      "summary": "회의",
      "start": {"dateTime":"2025-08-19T13:00:00+09:00"},
      "end":   {"dateTime":"2025-08-19T14:00:00+09:00"},
      "description": "...", "location": "...",
      "attendees": ["a@b.com","c@d.com"]  # 문자열 or {"email":...} 형식
    }
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
    body: 부분 업데이트(같은 필드 키 그대로)
    예) {"summary":"제목변경"} 또는 {"attendees":["a@b.com"]}
    """
    item = gcal_patch_event(session_id, event_id, body, calendar_id, send_updates)
    return item

# 프런트 일부가 PUT을 쓰는 경우를 대비해 동일 동작 제공(선택)
@router.put("/events/{event_id}")
def put_event(
    event_id: str,
    body: Dict[str, Any] = Body(...),
    session_id: str = Query(...),
    calendar_id: str = Query("primary"),
    send_updates: Optional[str] = Query(None, regex="^(all|none)?$"),
):
    item = gcal_patch_event(session_id, event_id, body, calendar_id, send_updates)
    return item

@router.delete("/events/{event_id}")
def delete_event(
    event_id: str,
    session_id: str = Query(...),
    calendar_id: str = Query("primary"),
):
    gcal_delete_event(session_id, event_id, calendar_id)
    return {"ok": True}


# ---- helpers ----
def _norm_attendees_for_write(v):
    """
    attendees 입력을 일관된 형태로 정규화한다.
    - "a@b.com" -> [{"email": "a@b.com"}]
    - {"email": "a@b.com", "displayName": "..."} -> 그대로 반영
    - 리스트면 각 원소에 대해 위 규칙 적용
    - None => None (패치 시 '변경하지 않음')
    - []   => []   (패치 시 '모두 제거')
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
