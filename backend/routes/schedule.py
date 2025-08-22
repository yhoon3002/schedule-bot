import os, re, json, logging, requests
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from routes.google_oauth import TOKENS
from routes.google_calendar import (
    gcal_list_events_all,
    gcal_insert_event,
    gcal_patch_event,
    gcal_delete_event,
    gcal_get_event,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/schedules", tags=["schedules"])

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE = os.getenv("OPENAI_BASE", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

KST = timezone(timedelta(hours=9))
CAL_SCOPE = "https://www.googleapis.com/auth/calendar"

# 요일 표기(한글)
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]

# 이메일 검증
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# 입력을 [valid_emails], [invalid_values]로 분리
def _split_valid_invalid_attendees(v):
    if v is None:
        return [], []
    if not isinstance(v, list):
        v = [v]
    valid, invalid = [], []
    for x in v:
        if not x:
            continue
        if isinstance(x, str):
            s = x.strip()
            (valid if EMAIL_RE.match(s) else invalid).append(s)
        elif isinstance(x, dict):
            s = (x.get("email") or x.get("value") or x.get("address") or "").strip()
            (valid if EMAIL_RE.match(s) else invalid).append(s or str(x))
        else:
            invalid.append(str(x))
    return valid, invalid

def _now_kst_iso() -> str:
    return datetime.now(KST).isoformat()

def _friendly_today() -> str:
    n = datetime.now(KST)
    return f"{n.strftime('%Y-%m-%d')} ({WEEKDAY_KO[n.weekday()]}) {n.strftime('%H:%M')}"

def _must_google_connected(session_id: str):
    tok = TOKENS.get(session_id or "")
    scope = (tok.get("scope") if tok else "") or ""
    ok = bool(tok and CAL_SCOPE in scope)
    if not ok:
        raise HTTPException(status_code=401, detail="Google 로그인/캘린더 연동이 필요합니다.")

# -------------------------- 도구 스펙 --------------------------

ALLOWED_TOOLS = {
    "create_event",
    "list_events",
    "update_event",
    "delete_event",
    "get_event_detail",
    "get_event_detail_by_index",
    "start_edit",
}

# 고정 키워드에 의존하지 않도록, 모델이 스스로 자연어를 해석해 from/to 및 filters를 구성하도록 설계
TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": (
                "Google Calendar 이벤트 생성.\n"
                "- KST 기준.\n"
                "- 종료가 없거나 시작보다 빠르면 시작+1h로 보정.\n"
                "- 참석자가 있고 notify_attendees가 명시되지 않았다면, 확인 단계에서 메일 발송 여부를 묻는다.\n"
                "- confirmed=true 일 때만 실제 생성한다(요약 확인 1회 원칙)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "string", "format": "date-time"},
                    "end": {"type": "string", "format": "date-time"},
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                    "attendees": {"type": "array", "items": {"type": "string"}},
                    "notify_attendees": {
                        "type": "boolean",
                        "description": "true면 참석자 초대메일 발송, false면 발송 안함",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "요약 확인 후 실제 실행하려면 true로 보낸다.",
                    },
                    "session_id": {"type": "string"},
                },
                "required": ["title", "start"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": (
                "사용자 일정 조회. 모델이 자연어를 해석하여 시간 범위와 세부 필터를 설정해 호출한다.\n"
                "- from/to는 ISO 8601 문자열(KST)로 전달.\n"
                "- 공휴일/생일 포함 여부도 제어 가능.\n"
                "- filters로 일정 항목(제목/설명/위치/참석자 유무/참석자 이메일/종일 여부/상태/기간/종료시각/종료날짜 등)을 세밀하게 필터링한다.\n"
                "- 반환은 서버가 번호(1) 스타일로 렌더링한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from": {"type": "string", "format": "date-time"},
                    "to": {"type": "string", "format": "date-time"},
                    "query": {"type": "string"},
                    "include_holidays": {"type": "boolean", "default": False},
                    "include_birthdays": {"type": "boolean", "default": False},
                    "filters": {
                        "type": "object",
                        "description": "세부 필터(모두 선택적)",
                        "properties": {
                            "title_includes": {"type": "array", "items": {"type": "string"}},
                            "title_excludes": {"type": "array", "items": {"type": "string"}},
                            "description_includes": {"type": "array", "items": {"type": "string"}},
                            "description_excludes": {"type": "array", "items": {"type": "string"}},
                            "location_includes": {"type": "array", "items": {"type": "string"}},
                            "location_excludes": {"type": "array", "items": {"type": "string"}},
                            "has_attendees": {"type": "boolean"},
                            "attendee_emails_includes": {"type": "array", "items": {"type": "string"}},
                            "has_location": {"type": "boolean"},
                            "is_all_day": {"type": "boolean"},
                            "min_duration_minutes": {"type": "integer"},
                            "max_duration_minutes": {"type": "integer"},
                            "status": {"type": "string", "description": "confirmed/tentative/cancelled 등"},
                            "calendar_ids_includes": {"type": "array", "items": {"type": "string"}},
                            # 새로 추가된 종료/날짜 관련 필터
                            "end_before": {"type": "string", "format": "date-time"},
                            "end_after": {"type": "string", "format": "date-time"},
                            "end_time_equals": {"type": "string", "description": "HH:MM 형식"},
                            "starts_on_date": {"type": "string", "description": "YYYY-MM-DD"},
                            "ends_on_date": {"type": "string", "description": "YYYY-MM-DD"},
                        },
                        "additionalProperties": False,
                    },
                    "session_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    # where / apply_to_all 추가
    {
        "type": "function",
        "function": {
            "name": "update_event",
            "description": (
                "Google Calendar 이벤트 수정. id/인덱스 또는 where(필터)로 대상 선택 가능.\n"
                "- start만 변경되고 end가 없거나 start>=end면 start+1h로 보정.\n"
                "- 참석자 변경 시 notify_attendees가 명시되지 않았다면 확인 단계에서 묻는다.\n"
                "- 여러 개가 매칭되면 번호 선택을 유도하거나 apply_to_all=true로 모두 수정.\n"
                "- confirmed=true 일 때만 실제 수정(요약 확인 1회 원칙)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "index": {"type": "integer", "minimum": 1},
                    "where": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string", "format": "date-time"},
                            "to": {"type": "string", "format": "date-time"},
                            "query": {"type": "string"},
                            "include_holidays": {"type": "boolean"},
                            "include_birthdays": {"type": "boolean"},
                            "filters": {"type": "object"},
                        },
                        "additionalProperties": False,
                    },
                    "apply_to_all": {"type": "boolean"},
                    "patch": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "start": {"type": "string", "format": "date-time"},
                            "end": {"type": "string", "format": "date-time"},
                            "description": {"type": "string"},
                            "location": {"type": "string"},
                            "attendees": {"type": "array", "items": {"type": "string"}},
                        },
                        "additionalProperties": False,
                    },
                    "notify_attendees": {
                        "type": "boolean",
                        "description": "true면 참석자 초대메일 발송, false면 발송 안함",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "요약 확인 후 실제 실행하려면 true로 보낸다.",
                    },
                    "session_id": {"type": "string"},
                },
                "required": ["patch"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_event",
            "description": (
                "이벤트 삭제. indexes/index/ids/id 또는 where(필터) 사용 가능.\n"
                "- where로 여러 개가 매칭되면 번호 선택을 유도하거나 apply_to_all=true로 모두 삭제.\n"
                "- confirmed=true 일 때만 실제 삭제(요약 확인 1회 원칙)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "ids": {"type": "array", "items": {"type": "string"}},
                    "index": {"type": "integer", "minimum": 1},
                    "indexes": {"type": "array", "items": {"type": "integer"}},
                    "where": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string", "format": "date-time"},
                            "to": {"type": "string", "format": "date-time"},
                            "query": {"type": "string"},
                            "include_holidays": {"type": "boolean"},
                            "include_birthdays": {"type": "boolean"},
                            "filters": {"type": "object"},
                        },
                        "additionalProperties": False,
                    },
                    "apply_to_all": {"type": "boolean"},
                    "confirmed": {
                        "type": "boolean",
                        "description": "요약 확인 후 실제 실행하려면 true로 보낸다.",
                    },
                    "session_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_event_detail",
            "description": "id/인덱스 또는 where(필터)로 상세 보기(참석자 포함). 두 개 이상이면 번호 선택 유도.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "index": {"type": "integer", "minimum": 1},
                    "where": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string", "format": "date-time"},
                            "to": {"type": "string", "format": "date-time"},
                            "query": {"type": "string"},
                            "include_holidays": {"type": "boolean"},
                            "include_birthdays": {"type": "boolean"},
                            "filters": {"type": "object"},
                        },
                        "additionalProperties": False,
                    },
                    "session_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_event_detail_by_index",
            "description": "마지막 조회 인덱스(1-base)로 상세 보기.",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "minimum": 1},
                    "session_id": {"type": "string"},
                },
                "required": ["index"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_edit",
            "description": "편집 시작. id/인덱스 또는 where(필터)로 대상 선택. 여러 개면 번호 선택 유도.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "index": {"type": "integer", "minimum": 1},
                    "where": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string", "format": "date-time"},
                            "to": {"type": "string", "format": "date-time"},
                            "query": {"type": "string"},
                            "include_holidays": {"type": "boolean"},
                            "include_birthdays": {"type": "boolean"},
                            "filters": {"type": "object"},
                        },
                        "additionalProperties": False,
                    },
                    "session_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
]

# 세션 상태
SESSION_LAST_LIST: Dict[str, List[Tuple[str, str]]] = {}
SESSION_LAST_ITEMS: Dict[str, List[Dict[str, Any]]] = {}


# -------------------------- OpenAI 호출 --------------------------
def _openai_chat(messages):
    if not OPENAI_API_KEY:
        raise HTTPException(500, "OPENAI_API_KEY not set")
    r = requests.post(
        f"{OPENAI_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENAI_MODEL,
            "temperature": 0.2,
            "messages": messages,
            "tools": TOOLS_SPEC,
            "tool_choice": "auto",
        },
        timeout=30,
    )
    if not r.ok:
        raise HTTPException(500, "LLM call failed")
    return r.json()


# -------------------------- 시간/포맷 유틸 --------------------------
ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?")
ISO_PAREN_EXAMPLE_RE = re.compile(
    r"\s*\([^)]*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?[^)]*\)\s*"
)
HELPER_NOTE_PREFIX = "(날짜/시간은 자연어로 적어주세요"

HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

def _parse_hhmm(s: str) -> Optional[Tuple[int, int]]:
    m = HHMM_RE.match(s.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))

def _strip_tz_keep_wallclock(s: str) -> str:
    # ISO 끝의 Z 또는 ±HH:MM 오프셋 제거 -> 벽시계 유지
    return re.sub(r"(Z|[+-]\d{2}:\d{2})$", "", s.strip())

def _get_kst(dt_str: Optional[str]):
    if not dt_str:
        return None
    if len(dt_str) == 10:
        return datetime.fromisoformat(dt_str + "T00:00:00+09:00")
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(KST)

def _fmt_kst_date(dt: Optional[datetime]) -> str:
    if not dt:
        return "없음"
    return f"{dt.strftime('%Y-%m-%d')} ({WEEKDAY_KO[dt.weekday()]})"

def _fmt_kst_time(dt: Optional[datetime]) -> str:
    if not dt:
        return "없음"
    return dt.strftime("%H:%M")

def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(KST).isoformat()

def _parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
    """
    어떤 오프셋/UTC가 오더라도 '벽시계' 그대로 KST로 해석.
    예) '2025-08-22T17:00:00Z' -> 2025-08-22 17:00:00+09:00
    """
    if not dt_str:
        return None
    s = dt_str.strip()
    try:
        if len(s) == 10:
            # YYYY-MM-DD
            dt = datetime.fromisoformat(s + "T00:00:00")
            return dt.replace(tzinfo=KST)
        # ISO with or without tz: 제거 후 KST 부여
        s_no_tz = _strip_tz_keep_wallclock(s)
        dt = datetime.fromisoformat(s_no_tz)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt.replace(tzinfo=KST)
    except Exception:
        return None


# -------------------------- 출력 포맷/후처리 --------------------------
def _iso_str_to_kst_friendly(iso_str: str) -> str:
    try:
        s = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        dt = (dt if dt.tzinfo else dt.replace(tzinfo=KST)).astimezone(KST)
        w = WEEKDAY_KO[dt.weekday()]
        return f"{dt.strftime('%Y-%m-%d')} ({w}) {dt.strftime('%H:%M')}"
    except Exception:
        return iso_str

def _sanitize_llm_reply_text(text: str, *, allow_helper: bool) -> str:
    if not text:
        return text
    out_lines = []
    for raw in text.splitlines():
        line = ISO_PAREN_EXAMPLE_RE.sub("", raw).rstrip()
        line = ISO_TS_RE.sub(lambda m: _iso_str_to_kst_friendly(m.group(0)), line)
        if ("형식으로 입력" in line) or ("정확한 형식" in line) or ("YYYY-" in line):
            continue
        if "일정 생성에 필요한 추가 정보를 요청드립니다" in line:
            continue
        if (not allow_helper) and (HELPER_NOTE_PREFIX in line):
            continue
        line = re.sub(r"\s{2,}", " ", line).rstrip()
        out_lines.append(line)
    cleaned = "\n".join(out_lines).strip()
    return cleaned or text

# 공백/들여쓰기 도우미
ZERO = "\u200B"  # 한 줄 공백 효과
INDENT_ITEM = "  "        # 목록용(한 번)
INDENT_SECTION = "    "   # 문단용(두 번)

def _indent_block(text: str, level: int = 1) -> str:
    prefix = "  " * level
    return "\n".join((prefix + ln) if ln.strip() else ln for ln in text.splitlines())

# 목록 블록(항상 1. 2. 3. ...으로 나타내기)
def _line_required_g(e: dict) -> str:
    title = e.get("summary") or "(제목 없음)"
    st = _get_kst(e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"))
    ed_raw = e.get("end", {}).get("dateTime") or e.get("end", {}).get("date")
    ed = _get_kst(ed_raw) if ed_raw else None
    s = f"{_fmt_kst_date(st)} {_fmt_kst_time(st)}" if st else "없음"
    e_ = f"{_fmt_kst_date(ed)} {_fmt_kst_time(ed)}" if ed else "없음"
    return f"{title}\n{s} ~ {e_}"

def _fmt_detail_g(e: dict) -> str:
    title = e.get("summary") or "(제목 없음)"
    st = _get_kst(e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"))
    ed_raw = e.get("end", {}).get("dateTime") or e.get("end", {}).get("date")
    ed = _get_kst(ed_raw) if ed_raw else None
    s_date = _fmt_kst_date(st)
    s_time = _fmt_kst_time(st)
    e_date = _fmt_kst_date(ed)
    e_time = _fmt_kst_time(ed)
    desc = (e.get("description") or "").strip() or "없음"
    loc = (e.get("location") or "").strip() or "없음"
    attendees = e.get("attendees") or []
    atts = ", ".join([a.get("email") for a in attendees if a.get("email")]) or "없음"
    # 상태/종일은 사용자에게 불필요하므로 표시하지 않음
    return (
        "📄 일정 상세 정보:\n"
        f"- 제목: {title}\n"
        f"- 시작 날짜: {s_date}\n"
        f"- 시작 시간: {s_time}\n"
        f"- 종료 날짜: {e_date}\n"
        f"- 종료 시간: {e_time}\n"
        f"- 설명: {desc}\n"
        f"- 위치: {loc}\n"
        f"- 참석자: {atts}"
    )

def _render_list_block(items: List[dict], *, indices: Optional[List[int]] = None) -> str:
    out: List[str] = []
    for idx, e in enumerate(items, start=1):
        no = (indices[idx - 1] if indices and len(indices) >= idx else idx)
        two = _line_required_g(e)
        title, time_range = (two.split("\n", 1) + [""])[:2]
        out.append(f"{no}. {title}")
        if time_range:
            out.append(time_range)
        if idx != len(items):
            out.append(ZERO)
    return "\n".join(out)

def _pack_g(e: dict) -> dict:
    start = e.get("start", {})
    end = e.get("end", {})
    return {
        "id": e.get("id"),
        "calendarId": e.get("_calendarId"),
        "title": e.get("summary") or "(제목 없음)",
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "description": e.get("description"),
        "location": e.get("location"),
        "attendees": [
            a.get("email") for a in (e.get("attendees") or []) if a.get("email")
        ],
        "status": e.get("status"),
    }


# -------------------------- 필터링/WHERE 유틸 --------------------------
def _ci_contains(text: Optional[str], needle: str) -> bool:
    if text is None:
        return False
    try:
        return needle.lower() in text.lower()
    except Exception:
        return False

def _any_ci_contains(text: Optional[str], needles: List[str]) -> bool:
    return all(_ci_contains(text, n) for n in needles) if needles else True

def _none_ci_contains(text: Optional[str], needles: List[str]) -> bool:
    return not any(_ci_contains(text, n) for n in needles) if needles else True

def _attendee_emails(e: dict) -> List[str]:
    return [a.get("email", "").lower() for a in (e.get("attendees") or []) if a.get("email")]

def _is_all_day_event(e: dict) -> bool:
    s = e.get("start", {})
    return "date" in s and "dateTime" not in s

def _duration_minutes(e: dict) -> Optional[int]:
    st = _get_kst(e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"))
    ed_raw = e.get("end", {}).get("dateTime") or e.get("end", {}).get("date")
    ed = _get_kst(ed_raw) if ed_raw else None
    if st and ed:
        return int((ed - st).total_seconds() // 60)
    return None

def _end_kst(e: dict) -> Optional[datetime]:
    ed_raw = e.get("end", {}).get("dateTime") or e.get("end", {}).get("date")
    return _get_kst(ed_raw) if ed_raw else None

def _apply_filters(items: List[dict], filters: Optional[dict]) -> List[dict]:
    if not filters:
        return items

    ti = filters.get("title_includes") or []
    te = filters.get("title_excludes") or []
    di = filters.get("description_includes") or []
    de = filters.get("description_excludes") or []
    li = filters.get("location_includes") or []
    le = filters.get("location_excludes") or []
    has_at = filters.get("has_attendees", None)
    email_in = [x.lower() for x in (filters.get("attendee_emails_includes") or [])]
    has_loc = filters.get("has_location", None)
    is_all_day = filters.get("is_all_day", None)
    min_d = filters.get("min_duration_minutes", None)
    max_d = filters.get("max_duration_minutes", None)
    status = (filters.get("status") or "").lower().strip()
    cals_in = [x for x in (filters.get("calendar_ids_includes") or [])]

    # 새로 추가된 종료/날짜 필터
    end_before = _parse_dt(filters.get("end_before")) if filters.get("end_before") else None
    end_after  = _parse_dt(filters.get("end_after"))  if filters.get("end_after")  else None
    end_time_eq_str = filters.get("end_time_equals")
    end_time_eq = _parse_hhmm(end_time_eq_str) if end_time_eq_str else None
    starts_on = (filters.get("starts_on_date") or "").strip()
    ends_on   = (filters.get("ends_on_date") or "").strip()

    out = []
    for e in items:
        title = e.get("summary") or ""
        desc = e.get("description") or ""
        loc = e.get("location") or ""
        emails = _attendee_emails(e)
        dur = _duration_minutes(e)
        st = (e.get("status") or "").lower()
        cal_id = e.get("_calendarId") or "primary"

        # ---- 기존 텍스트/조건 필터들 ----
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

        if has_at is True and len(emails) == 0:
            continue
        if has_at is False and len(emails) > 0:
            continue
        if email_in:
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

        if min_d is not None:
            if dur is None or dur < int(min_d):
                continue
        if max_d is not None:
            if dur is None or dur > int(max_d):
                continue

        if status and st != status:
            continue

        if cals_in and cal_id not in cals_in:
            continue

        # ---- 새로 추가: 종료 시각/날짜 필터 ----
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

        if starts_on:
            if not st_dt or st_dt.strftime("%Y-%m-%d") != starts_on:
                continue

        if ends_on:
            if not ed_dt or ed_dt.strftime("%Y-%m-%d") != ends_on:
                continue

        out.append(e)

    return out

def _resolve_where(sid: str, where: Optional[dict]) -> List[dict]:
    if not where:
        return []
    f_raw = where.get("from")
    t_raw = where.get("to")
    # ±1일 패딩 (cross-day를 잡기 위함)
    pf = _parse_dt(f_raw)
    pt = _parse_dt(t_raw)
    f_pad = _rfc3339((pf - timedelta(days=1))) if pf else None
    t_pad = _rfc3339((pt + timedelta(days=1))) if pt else None

    items = gcal_list_events_all(
        sid,
        f_pad if (pf or pt) else where.get("from"),
        t_pad if (pf or pt) else where.get("to"),
        where.get("query") or None,
        bool(where.get("include_holidays", False)),
        bool(where.get("include_birthdays", False)),
    )
    return _apply_filters(items, where.get("filters") or {})

# -------------------------- 스냅샷/매핑 --------------------------

def _find_snapshot_item(sid: str, event_id: str, cal_id: str) -> Optional[Dict[str, Any]]:
    items = SESSION_LAST_ITEMS.get(sid) or []
    for e in items:
        if e.get("id") == event_id and (e.get("_calendarId") or "primary") == (cal_id or "primary"):
            return e
    return None

def _map_index_to_pair(sid: str, idx: int) -> Optional[Tuple[str, str]]:
    pairs = SESSION_LAST_LIST.get(sid) or []
    if 1 <= idx <= len(pairs):
        return pairs[idx - 1]
    return None

def _find_cal_for_id(sid: str, event_id: str) -> Optional[str]:
    pairs = SESSION_LAST_LIST.get(sid) or []
    cal = next((c for (eid, c) in pairs if eid == event_id), None)
    if cal:
        return cal
    items = gcal_list_events_all(sid, None, None, None, False, False)
    hit = next((x for x in items if x.get("id") == event_id), None)
    return (hit.get("_calendarId") if hit else None)


# -------------------------- 시스템 프롬프트 --------------------------
SYSTEM_POLICY_TEMPLATE = """
You are ScheduleBot. Google Calendar 연결 사용자의 일정만 처리합니다.

- 한국어로 답변합니다.
- 모든 시간대는 Asia/Seoul(KST)을 기준으로 하며, 내부적으로 ISO 8601을 사용합니다.
- 사용자에게는 ISO 형식을 노출하지 않습니다.

[핵심 원칙]
- **고정된 단어/문장 규칙에 의존하지 말고**, 사용자의 자연어를 스스로 이해해 의도(조회/상세/생성/수정/삭제/필터링)를 판별하고 필요한 도구 호출을 연쇄적으로 수행하세요.
- 시간 범위 역시 모델이 스스로 계산하여 from/to에 넣으세요(예: “이번달”, “내일 오전”, “다음 주말” 등). 서버는 별도 키워드 매칭을 하지 않습니다.
- 생성/수정/삭제는 반드시 **요약 → (1) 변경내용 확인(예/아니오) → (2) 필요 시 ‘초대 메일 발송 여부’ 확인(예/아니오) → 실행** 순서로 진행합니다.
- 참석자가 1명 이상이거나 참석자가 새로 추가되는 수정이라면, 사용자에게 초대 메일 발송 여부를 별도 단계에서 한 번만 질문합니다(`notify_attendees`).

[시간/타임존 규칙]
- 도구 인자(start/end/from/to)는 **반드시 KST(+09:00) 오프셋을 포함한 ISO 문자열**로 작성하세요. **절대 'Z'(UTC)나 다른 오프셋을 사용하지 마세요.**
- 사용자 표현(“오늘/내일/오전/오후…”)은 모두 **KST 기준 벽시계 시간**으로 해석하세요.

[필터링]
- 시간 범위뿐만 아니라 제목/설명/위치/참석자 유무/참석자 이메일/종일 여부/상태/기간/캘린더 등 다양한 조건으로 필터링할 수 있습니다.
- `filters`는 다음도 지원합니다: `end_before`, `end_after` (ISO date-time), `end_time_equals` (HH:MM), `starts_on_date`, `ends_on_date` (YYYY-MM-DD).
- 도구 `update_event`/`delete_event`/`get_event_detail`/`start_edit`는 `where` 파라미터를 지원합니다.
- 여러 개가 매칭되면 번호 선택을 유도하거나, 사용자가 원하면 `apply_to_all=true`로 모두 적용하세요(1회 확인 필수).

현재 시각(KST): {NOW_ISO}
Today: {TODAY_FRIENDLY}
"""


# -------------------------- 입출력 모델 --------------------------
class ChatIn(BaseModel):
    user_message: str
    history: Optional[list] = None
    session_id: Optional[str] = None

class ChatOut(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    reply: str
    tool_result: Optional[Any] = None


# -------------------------- 엔드포인트 --------------------------
@router.post("/chat", response_model=ChatOut)
def chat(input: ChatIn):
    sid = (input.session_id or "").strip()
    _must_google_connected(sid)

    system_prompt = (
        SYSTEM_POLICY_TEMPLATE
        .replace("{NOW_ISO}", _now_kst_iso())
        .replace("{TODAY_FRIENDLY}", _friendly_today())
    )
    msgs = [{"role": "system", "content": system_prompt}]
    if input.history:
        msgs += input.history
    msgs.append({"role": "user", "content": input.user_message})

    data = _openai_chat(msgs)
    choice = data["choices"][0]
    tool_calls = choice.get("message", {}).get("tool_calls") or []

    if not tool_calls:
        reply = choice["message"].get("content") or "일정 관련 요청을 말씀해 주세요.\n\n예) 이번달 내 일정은? / 참석자 있는 일정만 보여줘 / '약'으로 등록된 일정 삭제"
        reply = _sanitize_llm_reply_text(reply, allow_helper=True)
        return ChatOut(reply=reply, tool_result=None)

    replies: List[str] = []
    actions: List[Dict[str, Any]] = []
    did_mutation = False

    # 여러 개 생성/수정이 한 턴에 발생하면 마지막에 번호 붙여 묶어서 보여주기
    created_events_agg: List[dict] = []
    updated_events_agg: List[dict] = []

    for tc in tool_calls:
        name = tc["function"]["name"]
        raw_args = tc["function"].get("arguments") or "{}"
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

        # ---------------- 조회(리스트) ----------------
        if name == "list_events":
            items = gcal_list_events_all(
                sid,
                args.get("from"),
                args.get("to"),
                args.get("query") or None,
                bool(args.get("include_holidays", False)),
                bool(args.get("include_birthdays", False)),
            )

            # 서버 측 세부 필터 후처리(모델이 보낸 filters 반영)
            filtered = _apply_filters(items, args.get("filters") or {})

            SESSION_LAST_LIST[sid] = [(it.get("id"), it.get("_calendarId") or "primary") for it in filtered]
            SESSION_LAST_ITEMS[sid] = filtered

            if not filtered:
                replies.append("  조건에 맞는 일정이 없어요.\n\n")
                actions.append({"list": []})
            elif len(filtered) == 1:
                e = filtered[0]
                replies.append("  다음 일정을 찾았어요. \n 이 일정이 맞으신가요? : \n" + "\n" + _indent_block(_fmt_detail_g(e), 2))
                actions.append({"list": [_pack_g(e)]})
            else:
                block = _render_list_block(filtered)
                replies.append("  여러 일정이 있어요. 번호를 선택하시면 상세 정보를 보여드릴게요.\n\n" + _indent_block(block, 1))
                actions.append({"list": [{"idx": i + 1, **_pack_g(e)} for i, e in enumerate(filtered)]})
            continue

        # ---------------- 생성 ----------------
        if name == "create_event":
            attendees_input = args.get("attendees")
            valid_emails, invalids = _split_valid_invalid_attendees(attendees_input)
            if invalids:
                replies.append(
                    "  참석자는 이메일 주소로만 입력할 수 있어요.\n\n"
                    + "\n".join(f"  - {x}" for x in invalids)
                    + "\n\n  올바른 이메일(예: name@example.com)로 다시 알려주세요."
                )
                actions.append({"ok": False, "error": "invalid_attendees", "invalid": invalids})
                continue

            start_dt = _parse_dt(args.get("start"))
            if not start_dt:
                replies.append("  시작 시간을 이해하지 못했어요.\n\n  예: '8월 25일 13:00'처럼 자연어로 말씀해 주세요.")
                actions.append({"ok": False, "error": "invalid_start"})
                continue

            end_dt = _parse_dt(args.get("end"))
            if (end_dt is None) or (end_dt <= start_dt):
                end_dt = start_dt + timedelta(hours=1)

            body = {
                "summary": args.get("title") or "(제목 없음)",
                "start": {"dateTime": _rfc3339(start_dt)},
                "end": {"dateTime": _rfc3339(end_dt)},
            }
            if args.get("description"):
                body["description"] = args["description"]
            if args.get("location"):
                body["location"] = args["location"]
            if attendees_input is not None:
                body["attendees"] = valid_emails

            # 확인 단계(한 번만) — 참석자 있으면 초대 메일 여부 질문 포함
            if not args.get("confirmed", False):
                desc = (body.get("description") or "없음")
                loc = (body.get("location") or "없음")
                atts = ", ".join(valid_emails) if valid_emails else "없음"
                notify = args.get("notify_attendees")
                notify_str = "예" if notify else ("아니오" if notify is not None else "미지정")

                # 참석자가 있고 notify_attendees 미지정이면 질문 유도
                needs_notify = bool(valid_emails) and (notify is None)

                summary = (
                    "    이대로 생성할까요?\n\n"
                    f"    1. 제목: {body['summary']}\n"
                    f"    2. 시작: {_iso_str_to_kst_friendly(body['start']['dateTime'])}\n"
                    f"    3. 종료: {_iso_str_to_kst_friendly(body['end']['dateTime'])}\n"
                    f"    4. 설명: {desc}\n"
                    f"    5. 위치: {loc}\n"
                    f"    6. 참석자: {atts}\n"
                    f"    7. 초대 메일 발송: {notify_str}\n"
                )
                if needs_notify:
                    summary += (
                        "\n"
                        "    ※ 참석자가 있어요. 초대 메일을 보낼까요? (예/아니오)\n"
                        "    → 예라면 다음 호출에서 `notify_attendees=true`, 아니오라면 `notify_attendees=false` 로 보내주세요.\n"
                    )
                summary += "\n    진행할까요? (예/아니오)"

                replies.append(summary)
                action_obj = {"ok": False, "need_confirm": True, "preview": body}
                if needs_notify:
                    action_obj["need_notify_choice"] = True
                actions.append(action_obj)
                continue

            send_updates = None
            if valid_emails:
                notify = args.get("notify_attendees", None)
                send_updates = "all" if notify else "none" if notify is not None else None

            e = gcal_insert_event(sid, body, send_updates=send_updates)
            created_events_agg.append(e)
            actions.append({"created": _pack_g(e)})
            did_mutation = True
            continue

        # ---------------- 수정 (id/index or where) ----------------
        if name == "update_event":
            event_id = None
            cal_id = None
            apply_all = bool(args.get("apply_to_all", False))

            # 1) id/index 우선
            if "index" in args and args["index"]:
                pair = _map_index_to_pair(sid, int(args["index"]))
                if pair:
                    event_id, cal_id = pair
            if not event_id and args.get("id"):
                raw_id = str(args.get("id")).strip()
                if raw_id.isdigit() and len(raw_id) < 6:
                    pair = _map_index_to_pair(sid, int(raw_id))
                    if pair:
                        event_id, cal_id = pair
                else:
                    event_id = raw_id
                    cal_id = _find_cal_for_id(sid, event_id) or "primary"

            p = args.get("patch") or {}
            body_base: Dict[str, Any] = {}
            if "title" in p:
                body_base["summary"] = p["title"]

            new_start_dt = _parse_dt(p.get("start"))
            new_end_dt = _parse_dt(p.get("end"))
            if new_start_dt:
                body_base.setdefault("start", {})["dateTime"] = _rfc3339(new_start_dt)
            if new_end_dt:
                body_base.setdefault("end", {})["dateTime"] = _rfc3339(new_end_dt)
            if "description" in p:
                body_base["description"] = p["description"]
            if "location" in p:
                body_base["location"] = p["location"]

            valid_emails = None
            if "attendees" in p:
                valid_emails, invalids = _split_valid_invalid_attendees(p.get("attendees"))
                if invalids:
                    replies.append(
                        "  참석자는 이메일 주소로만 입력할 수 있어요.\n\n"
                        + "\n".join(f"  - {x}" for x in invalids)
                        + "\n\n  올바른 이메일(예: name@example.com)로 다시 알려주세요."
                    )
                    actions.append({"ok": False, "error": "invalid_attendees", "invalid": invalids})
                    continue
                body_base["attendees"] = valid_emails

            # 2) where로 대상 찾기
            matched: List[dict] = []
            if not event_id and args.get("where"):
                matched = _resolve_where(sid, args.get("where") or {})
                if not matched:
                    replies.append("  조건과 일치하는 일정이 없어요.")
                    actions.append({"ok": False, "error": "not_found"})
                    continue
                # 단일 매칭이면 바로 진행
                if len(matched) == 1:
                    target = matched[0]
                    event_id = target.get("id")
                    cal_id = target.get("_calendarId") or "primary"

            # 3) 아직도 대상이 없고 여러 개면: 전체 적용 or 번호 선택 유도
            if not event_id and matched:
                # 확인/미리보기 — 참석자 변경 시 초대 메일 질문 추가
                if not args.get("confirmed", False):
                    # 미리보기 패치 요약
                    patch_lines = []
                    if "summary" in body_base: patch_lines.append(f"- 제목 → {body_base['summary']}")
                    if "start" in body_base: patch_lines.append(f"- 시작 → {_iso_str_to_kst_friendly(body_base['start']['dateTime'])}")
                    if "end" in body_base: patch_lines.append(f"- 종료 → {_iso_str_to_kst_friendly(body_base['end']['dateTime'])}")
                    if "description" in body_base: patch_lines.append(f"- 설명 → {body_base['description'] or '없음'}")
                    if "location" in body_base: patch_lines.append(f"- 위치 → {body_base['location'] or '없음'}")
                    if "attendees" in body_base:
                        atts = ", ".join(body_base["attendees"]) if body_base["attendees"] else "없음"
                        patch_lines.append(f"- 참석자 → {atts}")

                    # attendees를 패치에 넣었고 notify_attendees 미지정이면 질문 유도
                    needs_notify = ("attendees" in body_base) and bool(body_base.get("attendees")) and (args.get("notify_attendees") is None)

                    block = _render_list_block(matched)
                    msg = (
                        "    여러 일정이 발견됐어요.\n\n"
                        "    다음 **모든 일정에 동일 수정**을 적용할까요?\n\n"
                        + _indent_block(block, 2)
                        + ("\n\n    수정 요약:\n" + _indent_block("\n".join(patch_lines) or "- (변경 없음)", 3))
                        + "\n"
                    )
                    if needs_notify:
                        msg += (
                            "\n"
                            "    ※ 참석자 변경(또는 추가)이 있어요. 초대 메일을 보낼까요? (예/아니오)\n"
                            "    → 예라면 다음 호출에서 `notify_attendees=true`, 아니오라면 `notify_attendees=false` 로 보내주세요.\n"
                        )
                    msg += "\n    진행할까요? (예/아니오)\n    (하나만 수정하려면 번호를 선택해 주세요.)"

                    replies.append(msg)
                    action_obj = {
                        "ok": False,
                        "need_confirm": True,
                        "preview_patch": body_base,
                        "candidates": [_pack_g(x) for x in matched],
                    }
                    if needs_notify:
                        action_obj["need_notify_choice"] = True
                    actions.append(action_obj)
                    continue

                # confirmed이고 apply_to_all=true 인 경우 모두 수정
                if apply_all:
                    send_updates = None
                    if valid_emails is not None:
                        notify = args.get("notify_attendees", None)
                        if notify is not None:
                            send_updates = "all" if notify else "none"

                    for m in matched:
                        eid = m.get("id"); cid = m.get("_calendarId") or "primary"
                        # start만 바뀌고 end 미지정 보정
                        body = dict(body_base)
                        if ("start" in body) and ("end" not in body):
                            cur_end_dt = _parse_dt(m.get("end", {}).get("dateTime") or m.get("end", {}).get("date"))
                            start_dt = _parse_dt(body["start"]["dateTime"])
                            if (cur_end_dt is None) or (cur_end_dt <= start_dt):
                                body.setdefault("end", {})["dateTime"] = _rfc3339(start_dt + timedelta(hours=1))
                        e = gcal_patch_event(sid, eid, body, cid, send_updates=send_updates)
                        updated_events_agg.append(e)
                        actions.append({"updated": _pack_g(e)})
                        did_mutation = True
                    continue

                # confirmed인데 apply_to_all이 아니면 번호 선택 필요
                block = _render_list_block(matched)
                replies.append("    번호를 선택해 주세요.\n\n" + _indent_block(block, 2))
                actions.append({"ok": False, "need_index": True})
                continue

            # 4) 단일 대상 수정 로직
            if not event_id:
                replies.append("  수정할 대상을 찾지 못했어요.")
                actions.append({"ok": False, "error": "not_found"})
                continue

            # 현재 이벤트 조회
            snapshot_before = None
            try:
                snapshot_before = gcal_get_event(sid, cal_id or "primary", event_id)
            except HTTPException:
                pass

            # start만 바뀌고 end가 없거나 start>=end면 start+1h 보정
            if new_start_dt and (not new_end_dt):
                cur_end_dt = _parse_dt(snapshot_before.get("end", {}).get("dateTime") or snapshot_before.get("end", {}).get("date")) if snapshot_before else None
                if (cur_end_dt is None) or (cur_end_dt <= new_start_dt):
                    body_base.setdefault("end", {})["dateTime"] = _rfc3339(new_start_dt + timedelta(hours=1))

            # 확인 단계(한 번만) — 참석자 존재/추가 시 초대 메일 질문 포함
            if not args.get("confirmed", False):
                before_str = _fmt_detail_g(snapshot_before) if snapshot_before else "(이전 정보 조회 불가)"
                after_dummy = snapshot_before.copy() if snapshot_before else {}
                if "summary" in body_base:   after_dummy["summary"] = body_base["summary"]
                if "description" in body_base: after_dummy["description"] = body_base["description"]
                if "location" in body_base:  after_dummy["location"] = body_base["location"]
                if "start" in body_base:     after_dummy.setdefault("start", {})["dateTime"] = body_base["start"]["dateTime"]
                if "end" in body_base:       after_dummy.setdefault("end", {})["dateTime"] = body_base["end"]["dateTime"]
                if "attendees" in body_base: after_dummy["attendees"] = [{"email": x} for x in body_base["attendees"]]

                notify = args.get("notify_attendees", None)
                notify_str = "예" if notify else ("아니오" if notify is not None else "미지정")

                # 참석자 존재/추가 여부
                before_set = set([a.get("email") for a in (snapshot_before.get("attendees") or []) if a.get("email")]) if snapshot_before else set()
                after_set  = set(body_base.get("attendees") or [a.get("email") for a in (snapshot_before.get("attendees") or []) if a.get("email")])
                newly_added = after_set - before_set

                needs_notify = (notify is None) and (bool(after_set) or bool(newly_added))

                preview = (
                    "    다음과 같이 수정할까요?\n\n"
                    "    1. 변경 전:\n"
                    f"{_indent_block(before_str, 3)}\n\n"
                    "    2. 변경 후(미리보기):\n"
                    f"{_indent_block(_fmt_detail_g(after_dummy), 3)}\n\n"
                    f"    3. 초대 메일 발송: {notify_str}\n"
                )
                if needs_notify:
                    preview += (
                        "\n"
                        "    ※ 참석자가 존재하거나 새로 추가됩니다. 초대 메일을 보낼까요? (예/아니오)\n"
                        "    → 예라면 다음 호출에서 `notify_attendees=true`, 아니오라면 `notify_attendees=false` 로 보내주세요.\n"
                    )
                preview += "\n    진행할까요? (예/아니오)"

                replies.append(preview)
                action_obj = {"ok": False, "need_confirm": True, "preview_patch": body_base}
                if needs_notify:
                    action_obj["need_notify_choice"] = True
                actions.append(action_obj)
                continue

            send_updates = None
            if valid_emails is not None:
                notify = args.get("notify_attendees", None)
                if notify is not None:
                    send_updates = "all" if notify else "none"

            try:
                e = gcal_patch_event(sid, event_id, body_base, cal_id or "primary", send_updates=send_updates)
                updated_events_agg.append(e)
                actions.append({"updated": _pack_g(e)})
                did_mutation = True
            except HTTPException as ex:
                replies.append(f"  일정 수정 중 오류가 발생했어요.\n\n  사유: {ex.detail}")
                actions.append({"ok": False, "error": ex.detail})
            continue

        # ---------------- 삭제 (id/index or where) ----------------
        if name == "delete_event":
            pairs_snapshot: List[Tuple[str, str]] = list(SESSION_LAST_LIST.get(sid) or [])
            apply_all = bool(args.get("apply_to_all", False))

            def idx_to_pair_local(i: int) -> Optional[Tuple[str, str]]:
                if 1 <= i <= len(pairs_snapshot):
                    return pairs_snapshot[i - 1]
                return None

            targets: List[Tuple[str, str]] = []

            # where → 후보 찾기
            if args.get("where"):
                candidates = _resolve_where(sid, args.get("where"))
                if not candidates:
                    replies.append("  조건과 일치하는 일정이 없어요.")
                    actions.append({"ok": False, "error": "not_found"})
                    continue
                if len(candidates) == 1:
                    c = candidates[0]
                    targets.append((c.get("id"), c.get("_calendarId") or "primary"))
                else:
                    if not args.get("confirmed", False):
                        block = _render_list_block(candidates)
                        replies.append("    아래 후보가 있어요. 모두 삭제할까요?\n\n" + _indent_block(block, 2) + "\n\n    진행할까요? (예/아니오)\n    (하나만 삭제하려면 번호를 알려주세요.)")
                        actions.append({"ok": False, "need_confirm": True, "candidates": [ _pack_g(x) for x in candidates ]})
                        continue
                    if apply_all:
                        for c in candidates:
                            targets.append((c.get("id"), c.get("_calendarId") or "primary"))
                    else:
                        block = _render_list_block(candidates)
                        replies.append("    번호를 선택해 주세요.\n\n" + _indent_block(block, 2))
                        actions.append({"ok": False, "need_index": True})
                        continue

            # 기존 방식(id/index/ids/indexes)
            if args.get("indexes"):
                for i in args["indexes"]:
                    p = idx_to_pair_local(int(i))
                    if p:
                        targets.append(p)
            elif args.get("index"):
                p = idx_to_pair_local(int(args["index"]))
                if p:
                    targets.append(p)
            elif args.get("ids"):
                for eid in args["ids"]:
                    cal = _find_cal_for_id(sid, str(eid))
                    if cal:
                        targets.append((str(eid), cal))
            elif args.get("id"):
                eid = str(args["id"])
                cal = _find_cal_for_id(sid, eid)
                if cal:
                    targets.append((eid, cal))

            if not targets:
                replies.append("  삭제할 일정을 찾지 못했어요.")
                actions.append({"ok": False, "error": "not_found"})
                continue

            # 확인 단계(한 번만) — where 경로가 아닌 경우에도 동일 처리
            if not args.get("confirmed", False):
                preview_items: List[dict] = []
                idx_list: List[int] = []
                fallback_lines: List[str] = []
                for eid, cal in targets:
                    snap = _find_snapshot_item(sid, eid, cal)
                    if snap:
                        try:
                            idx_display = pairs_snapshot.index((eid, cal)) + 1
                        except ValueError:
                            idx_display = len(idx_list) + 1
                        preview_items.append(snap)
                        idx_list.append(idx_display)
                    else:
                        fallback_lines.append(f"- id={eid} (calendar={cal})")
                preview_text = ""
                if preview_items:
                    preview_text += _render_list_block(preview_items, indices=idx_list)
                if fallback_lines:
                    preview_text += ("\n" if preview_text else "") + "\n".join(fallback_lines)
                replies.append("    아래 일정을 삭제할까요?\n\n" + _indent_block(preview_text or "(표시할 항목 없음)", 2) + "\n\n" + "    진행할까요? (예/아니오)")
                actions.append({"ok": False, "need_confirm": True, "preview_delete": [list(t) for t in targets]})
                continue

            deleted_events_for_block: List[dict] = []
            deleted_indices_for_block: List[int] = []
            deleted_fallback_lines: List[str] = []

            for eid, cal in targets:
                snap = _find_snapshot_item(sid, eid, cal)
                fallback = f"- id={eid} (calendar={cal})"
                try:
                    gcal_delete_event(sid, eid, cal or "primary")
                    if snap:
                        actions.append({"deleted": _pack_g(snap)})
                        try:
                            idx_display = pairs_snapshot.index((eid, cal)) + 1
                        except ValueError:
                            idx_display = None
                        deleted_events_for_block.append(snap)
                        deleted_indices_for_block.append(idx_display or len(deleted_events_for_block))
                    else:
                        actions.append({"deleted": {"id": eid, "calendarId": cal}})
                        deleted_fallback_lines.append(fallback)
                    did_mutation = True
                except HTTPException as ex:
                    replies.append(f"  일정 삭제 중 오류가 발생했어요.\n\n  사유: {ex.detail}")
                    actions.append({"ok": False, "error": "not_found"})

            if deleted_events_for_block:
                block = _render_list_block(deleted_events_for_block, indices=deleted_indices_for_block)
                replies.append("    🗑️ 다음 일정을 삭제했어요.\n\n" + _indent_block(block, 1))
            if deleted_fallback_lines:
                replies.append("    🗑️ 스냅샷이 없어 간략히 표시한 항목:\n\n" + _indent_block("\n".join(deleted_fallback_lines), 1))
            continue

        # ---------------- 상세(인덱스) ----------------
        if name == "get_event_detail_by_index":
            idx = int(args["index"])
            pair = _map_index_to_pair(sid, idx)
            if not pair:
                replies.append("  해당 번호의 일정을 찾을 수 없어요.\n\n  최근 조회 목록을 다시 띄워주세요.")
                actions.append({"ok": False, "error": "index_out_of_range"})
                continue
            event_id, cal_id = pair
            try:
                e = gcal_get_event(sid, cal_id, event_id)
                replies.append(_indent_block(_fmt_detail_g(e), 2))
                actions.append({"detail": _pack_g(e)})
            except HTTPException:
                replies.append("  해당 일정을 찾지 못했어요.\n\n  이미 변경/삭제되었을 수 있어요.")
                actions.append({"ok": False, "error": "not_found"})
            continue

        # ---------------- 상세(아이디/인덱스/where) ----------------
        if name == "get_event_detail":
            event_id = None
            cal_id = None
            matched: List[dict] = []

            if "index" in args and args["index"]:
                pair = _map_index_to_pair(sid, int(args["index"]))
                if pair:
                    event_id, cal_id = pair
            if not event_id and args.get("id"):
                event_id = str(args["id"])
                cal_id = _find_cal_for_id(sid, event_id) or "primary"
            if not event_id and args.get("where"):
                matched = _resolve_where(sid, args.get("where"))
                if not matched:
                    replies.append("  해당 조건의 일정을 찾지 못했어요.")
                    actions.append({"ok": False, "error": "not_found"})
                    continue
                if len(matched) == 1:
                    event_id = matched[0].get("id")
                    cal_id = matched[0].get("_calendarId") or "primary"

            if not event_id and matched:
                block = _render_list_block(matched)
                replies.append("  여러 일정이 있어요. 번호를 선택해 주세요.\n\n" + _indent_block(block, 2))
                actions.append({"ok": False, "need_index": True})
                continue

            if not event_id:
                replies.append("  해당 일정을 찾지 못했어요.")
                actions.append({"ok": False, "error": "not_found"})
                continue

            try:
                e = gcal_get_event(sid, cal_id, event_id)
                replies.append(_indent_block(_fmt_detail_g(e), 2))
                actions.append({"detail": _pack_g(e)})
            except HTTPException:
                replies.append("  해당 일정을 찾지 못했어요.\n\n  이미 변경/삭제되었을 수 있어요.")
                actions.append({"ok": False, "error": "not_found"})
            continue

        # ---------------- 편집 시작 ----------------
        if name == "start_edit":
            event_id = None
            cal_id = None
            matched: List[dict] = []

            if args.get("index"):
                pair = _map_index_to_pair(sid, int(args["index"]))
                if pair:
                    event_id, cal_id = pair
            elif args.get("id"):
                event_id = str(args["id"])
                cal_id = _find_cal_for_id(sid, event_id)
            elif args.get("where"):
                matched = _resolve_where(sid, args.get("where"))
                if len(matched) == 1:
                    event_id = matched[0].get("id")
                    cal_id = matched[0].get("_calendarId") or "primary"

            if not event_id and matched:
                block = _render_list_block(matched)
                replies.append("  여러 일정이 있어요. 번호를 선택해 주세요.\n\n" + _indent_block(block, 2))
                actions.append({"ok": False, "need_index": True})
                continue

            if not event_id:
                replies.append("  대상을 찾을 수 없어요.\n\n  조건을 다시 알려주세요.")
                actions.append({"ok": False, "error": "not_found"})
            else:
                try:
                    e = gcal_get_event(sid, cal_id or "primary", event_id)
                    replies.append(
                        "    수정할 항목을 알려주세요.\n\n"
                        "    1. 제목\n"
                        "    2. 시간(시작/종료)\n"
                        "    3. 설명\n"
                        "    4. 위치\n"
                        "    5. 참석자(이메일)\n\n"
                        + _indent_block(_fmt_detail_g(e), 2)
                    )
                    actions.append({"detail": _pack_g(e)})
                except HTTPException:
                    replies.append("  대상을 찾을 수 없어요.\n\n  이미 변경/삭제되었을 수 있어요.")
                    actions.append({"ok": False, "error": "not_found"})
            continue

    # 여러 개 생성/수정 결과를 번호 매겨 요약 표시 (항상 별도 문단으로 분리)
    if created_events_agg:
        block = _render_list_block(created_events_agg)
        replies.append(INDENT_SECTION + "✅ 일정이 생성되었어요.\n\n" + _indent_block(block, 1))

    if updated_events_agg:
        block = _render_list_block(updated_events_agg)
        replies.append(INDENT_SECTION + "🔧 다음 일정을 수정했어요.\n\n" + _indent_block(block, 1))

    # 변경이 있었다면 최신 스냅샷 갱신 및 최신 목록 노출(번호/문단 분리/추가 들여쓰기)
    if did_mutation:
        items = gcal_list_events_all(sid, None, None, None, False, False)
        SESSION_LAST_LIST[sid] = [(it.get("id"), it.get("_calendarId") or "primary") for it in items]
        SESSION_LAST_ITEMS[sid] = items
        block = _render_list_block(items)
        replies.append(INDENT_SECTION + "\n 변경 이후 최신 목록입니다.\n\n" + _indent_block(block, 2))
        actions.append({"list": [{"idx": i + 1, **_pack_g(e)} for i, e in enumerate(items)]})

    reply = "\n\n".join(replies) if replies else "완료했습니다."
    reply = _sanitize_llm_reply_text(reply, allow_helper=False)
    return ChatOut(reply=reply, tool_result={"actions": actions})
