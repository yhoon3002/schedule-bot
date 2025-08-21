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

# 영어 요일을 한글로 바꿀때 사용
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]

# 이메일 검증
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# 입력을 [valid_emails], [invalid_values]로 분리.
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

ALLOWED_TOOLS = {
    "create_event",
    "list_events",
    "update_event",
    "delete_event",
    "get_event_detail",
    "get_event_detail_by_index",
    "start_edit",
}

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": (
                "Create a Google Calendar event. If attendees are provided and user didn't specify email sending, ask first.\n"
                "Use KST. If end is omitted or <= start, treat as start+1h."
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
                "List events in the user's calendars.\n"
                "-> Use this to implement natural-language filters like '오늘', '이번달', '이번 주', '내일', 특정 제목 키워드 등.\n"
                "-> Fill 'from' and 'to' as ISO 8601 (KST). Examples of mapping: 오늘=[오늘 00:00, 내일 00:00), 이번달=[이달 1일 00:00, 다음달 1일 00:00).\n"
                "-> For title/keyword filters, set 'query' to the phrase (e.g., '약먹어', '회의').\n"
                "Do NOT include holidays/birthdays unless the user asks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from": {"type": "string", "format": "date-time"},
                    "to": {"type": "string", "format": "date-time"},
                    "query": {"type": "string"},
                    "include_holidays": {"type": "boolean", "default": False},
                    "include_birthdays": {"type": "boolean", "default": False},
                    "session_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_event",
            "description": (
                "Update a Google Calendar event. Pass id or last-list 1-based index.\n"
                "When modifying attendees and user didn't specify email sending, ask first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "index": {"type": "integer", "minimum": 1},
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
                "Delete events. Use exactly one of: indexes, index, ids, id.\n"
                "For natural-language like '오늘 약먹어 일정 삭제', first call list_events with from/to+query, then call delete_event with resulting indexes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "ids": {"type": "array", "items": {"type": "string"}},
                    "index": {"type": "integer", "minimum": 1},
                    "indexes": {"type": "array", "items": {"type": "integer"}},
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
            "description": (
                "Get event detail by id or 1-based index from the last list.\n"
                "Use this after filtering (e.g., when user asks '~~일정 참석자 알려줘')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "index": {"type": "integer", "minimum": 1},
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
            "description": "Get event detail by last-list index (1-based).",
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
            "description": "User wants to edit but didn’t specify fields. Pass id or index.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "index": {"type": "integer", "minimum": 1},
                    "session_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
]

SESSION_LAST_LIST: Dict[str, List[Tuple[str, str]]] = {}
SESSION_LAST_ITEMS: Dict[str, List[Dict[str, Any]]] = {}

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

# ---- Time helpers ----

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

# ---- Render helpers ----

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
    return (
        "📄 일정 상세 정보:\n"
        f"- 제목: {title}\n- 시작 날짜: {s_date}\n- 시작 시간: {s_time}\n"
        f"- 종료 날짜: {e_date}\n- 종료 시간: {e_time}\n"
        f"- 설명: {desc}\n- 위치: {loc}\n- 참석자: {atts}"
    )

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
    }

# Snapshot helpers

def _find_snapshot_item(sid: str, event_id: str, cal_id: str) -> Optional[Dict[str, Any]]:
    items = SESSION_LAST_ITEMS.get(sid) or []
    for e in items:
        if e.get("id") == event_id and (e.get("_calendarId") or "primary") == (cal_id or "primary"):
            return e
    return None

# 시간 파싱 유틸

def _parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    s = dt_str.strip()
    try:
        if len(s) == 10:
            dt = datetime.fromisoformat(s + "T00:00:00+09:00")
        else:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST)
    except Exception:
        return None

def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(KST).isoformat()

# ---------------- System policy for the LLM ----------------
SYSTEM_POLICY_TEMPLATE = """
You are ScheduleBot. Google Calendar 연결 사용자의 일정만 처리합니다.
- Respond in Korean.
- 시간대는 Asia/Seoul (KST). ISO 8601 사용.

[핵심 원칙]
- 사용자의 자연어를 스스로 해석해 필요한 도구 호출을 **연쇄적으로** 수행합니다.
- 서버 측 키워드 매칭은 사용하지 않습니다. (LLM이 판단)
- 가능하면 재질문하지 말고, 합리적으로 가정하여 진행하세요.
- 참석자가 1명 이상인 생성/수정 작업은 확정 후 한 번만 초대 메일 여부(예/아니오)를 물어봅니다.
- **사용자에게 ISO 예시를 보여주지 마세요.** (도구 호출에만 사용)

[의도 판별]
- 생성 / 목록 / 상세 / 참석자 조회 / 수정 / 삭제.
- "~일정 참석자 알려줘" ⇒ 필터링해서 단일 후보면 상세 조회(get_event_detail)로 참석자까지 보여주기.
- "오늘/이번달/이번 주/내일/어제" 등 기간 지시어와 제목/키워드(예: "약먹어")를 함께 해석.

[자연어 기간 → from/to (모두 KST, ISO 8601)]
- 오늘: [오늘 00:00, 내일 00:00)
- 내일: [내일 00:00, 모레 00:00)
- 어제: [어제 00:00, 오늘 00:00)
- 이번 주: [이번 주 월요일 00:00, 다음 주 월요일 00:00)  ※ 주 시작은 월요일
- 다음 주: [다음 주 월요일 00:00, 다다음 주 월요일 00:00)
- 이번달: [이번달 1일 00:00, 다음달 1일 00:00)
- 다음달: [다음달 1일 00:00, 다다음달 1일 00:00)

[도구 사용 지침]
- 목록(list_events): 위 기간 규칙에 따라 from/to를 채우고, 제목/키워드는 query에 입력. 공휴일/생일은 요청 있을 때만 포함.
- 삭제(delete_event): 자연어로 범위+키워드가 오면 (1) list_events로 필터링 → (2) 결과 인덱스로 delete_event 호출. 결과가 0개면 친절히 안내.
- 참석자 요청: (1) list_events로 필터링 → 후보가 1개면 get_event_detail, 여러 개면 번호 선택 유도.
- 생성(create_event): 종료 누락 또는 종료<=시작이면 시작+1시간으로 도구 호출.
- 수정(update_event): start만 변경이고 end가 없거나 start>=end면 start+1시간으로 보정.

[샘플 시나리오 (도구 호출 예)]
1) "오늘 약먹어 일정 삭제해줘"
   - list_events {from=오늘 00:00, to=내일 00:00, query="약먹어"}
   - delete_event {indexes=[1,2,...]}  (목록 결과 기준)

2) "이번달 일정 알려줘"
   - list_events {from=이번달 1일 00:00, to=다음달 1일 00:00}

3) "프로젝트 킥오프 일정 참석자 알려줘"
   - list_events {query="프로젝트 킥오프"}
   - (후보 1개면) get_event_detail {index=1}

현재 시각(KST): {NOW_ISO}, Today: {TODAY_FRIENDLY}.
"""

# 출력 후처리(ISO -> 한국식 변환)
ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?")
ISO_PAREN_EXAMPLE_RE = re.compile(
    r"\s*\([^)]*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?[^)]*\)\s*"
)
HELPER_NOTE_PREFIX = "(날짜/시간은 자연어로 적어주세요"

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

# 목록 블록(번호/줄바꿈 일관화)
ZERO = "\u200B"  # 한 줄 공백 효과

def _render_list_block(items: List[dict], *, indices: Optional[List[int]] = None) -> str:
    out: List[str] = []
    for idx, e in enumerate(items, start=1):
        no = (indices[idx - 1] if indices and len(indices) >= idx else idx)
        two = _line_required_g(e)
        title, time_range = (two.split("\n", 1) + [""])[:2]
        out.append(f"{no}\\) {title}")
        if time_range:
            out.append(time_range)
        if idx != len(items):
            out.append(ZERO)
    return "\n".join(out)

# 입출력 모델
class ChatIn(BaseModel):
    user_message: str
    history: Optional[list] = None
    session_id: Optional[str] = None

class ChatOut(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    reply: str
    tool_result: Optional[Any] = None

# Snapshot mapping helpers

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
    items = gcal_list_events_all(sid, None, None, None)
    hit = next((x for x in items if x.get("id") == event_id), None)
    return (hit.get("_calendarId") if hit else None)

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
        reply = choice["message"].get("content") or "일정 관련 요청을 말씀해 주세요."
        # 첫 질문에만 헬퍼 사용
        reply = _sanitize_llm_reply_text(reply, allow_helper=True)
        return ChatOut(reply=reply, tool_result=None)

    replies: List[str] = []
    actions: List[Dict[str, Any]] = []
    did_mutation = False

    for tc in tool_calls:
        name = tc["function"]["name"]
        raw_args = tc["function"].get("arguments") or "{}"
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

        # 목록
        if name == "list_events":
            items = gcal_list_events_all(
                sid,
                args.get("from"),
                args.get("to"),
                args.get("query") or None,
                bool(args.get("include_holidays", False)),
                bool(args.get("include_birthdays", False)),
            )
            SESSION_LAST_LIST[sid] = [(it.get("id"), it.get("_calendarId") or "primary") for it in items]
            SESSION_LAST_ITEMS[sid] = items

            if not items:
                replies.append("해당 조건에 맞는 일정이 없어요.")
                actions.append({"list": []})
            elif len(items) == 1:
                e = items[0]
                replies.append("다음 일정을 찾았어요:\n" + _fmt_detail_g(e))
                actions.append({"list": [_pack_g(e)]})
            else:
                block = _render_list_block(items)
                replies.append(
                    "여러 개가 있어요. 번호를 선택하시면 상세 정보를 알려드릴게요:\n"
                    + ZERO + "\n" + block
                )
                actions.append({"list": [{"idx": i + 1, **_pack_g(e)} for i, e in enumerate(items)]})
            continue

        # 생성
        if name == "create_event":
            attendees_input = args.get("attendees")
            valid_emails, invalids = _split_valid_invalid_attendees(attendees_input)
            if invalids:
                replies.append(
                    "참석자는 이메일 주소로만 입력할 수 있어요.\n"
                    + "\n".join(f"- {x}" for x in invalids)
                    + "\n올바른 이메일(예: name@example.com)로 다시 입력해 주세요."
                )
                actions.append({"ok": False, "error": "invalid_attendees", "invalid": invalids})
                continue

            start_dt = _parse_dt(args.get("start"))
            if not start_dt:
                replies.append("시작 시간을 이해하지 못했어요. 예: '8월 25일 13:00'처럼 알려주세요.")
                actions.append({"ok": False, "error": "bad_start"})
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

            notify = args.get("notify_attendees", None)
            send_updates = None
            if valid_emails and notify is not None:
                send_updates = "all" if notify else "none"

            e = gcal_insert_event(sid, body, send_updates=send_updates)
            replies.append("✅ 일정 등록:\n(참석자는 이메일 주소로 입력해주세요)\n" + _fmt_detail_g(e))
            actions.append({"created": _pack_g(e)})
            did_mutation = True
            continue

        # 수정
        if name == "update_event":
            event_id = None
            cal_id = None
            if "index" in args and args["index"]:
                pair = _map_index_to_pair(sid, int(args["index"]))
                if pair:
                    event_id, cal_id = pair
            if not event_id and args.get("id"):
                raw_id = str(args["id"]).strip()
                if raw_id.isdigit() and len(raw_id) < 6:
                    pair = _map_index_to_pair(sid, int(raw_id))
                    if pair:
                        event_id, cal_id = pair
                else:
                    event_id = raw_id
                    cal_id = _find_cal_for_id(sid, event_id) or "primary"

            if not event_id:
                replies.append("수정할 대상을 찾지 못했어요. 먼저 '전체 일정'으로 목록을 띄워주세요.")
                actions.append({"ok": False, "error": "not_found"})
                continue

            p = args.get("patch") or {}
            body: Dict[str, Any] = {}
            if "title" in p:
                body["summary"] = p["title"]

            new_start_dt = _parse_dt(p.get("start"))
            new_end_dt = _parse_dt(p.get("end"))

            if new_start_dt:
                body.setdefault("start", {})["dateTime"] = _rfc3339(new_start_dt)
            if new_end_dt:
                body.setdefault("end", {})["dateTime"] = _rfc3339(new_end_dt)

            # start만 바뀌고 end가 없거나 start>=end면 start+1h로 보정
            if new_start_dt and (not new_end_dt):
                cur = gcal_get_event(sid, cal_id or "primary", event_id)
                cur_end_dt = _parse_dt(cur.get("end", {}).get("dateTime") or cur.get("end", {}).get("date"))
                if (cur_end_dt is None) or (cur_end_dt <= new_start_dt):
                    body.setdefault("end", {})["dateTime"] = _rfc3339(new_start_dt + timedelta(hours=1))

            if "description" in p:
                body["description"] = p["description"]
            if "location" in p:
                body["location"] = p["location"]

            send_updates = None
            if "attendees" in p:
                valid_emails, invalids = _split_valid_invalid_attendees(p.get("attendees"))
                if invalids:
                    replies.append(
                        "참석자는 이메일 주소로만 입력할 수 있어요.\n"
                        + "\n".join(f"- {x}" for x in invalids)
                        + "\n올바른 이메일(예: name@example.com)로 다시 알려주세요."
                    )
                    actions.append({"ok": False, "error": "invalid_attendees", "invalid": invalids})
                    continue
                body["attendees"] = valid_emails
                notify = args.get("notify_attendees", None)
                if valid_emails and notify is not None:
                    send_updates = "all" if notify else "none"

            try:
                e = gcal_patch_event(
                    sid, event_id, body, cal_id or "primary", send_updates=send_updates
                )
                replies.append("🔧 일정 수정 완료:\n" + _fmt_detail_g(e))
                actions.append({"updated": _pack_g(e)})
                did_mutation = True
            except HTTPException as ex:
                replies.append(f"일정 수정 중 오류가 발생했어요: {ex.detail}")
                actions.append({"ok": False, "error": ex.detail})
            continue

        # 삭제
        if name == "delete_event":
            pairs_snapshot: List[Tuple[str, str]] = list(SESSION_LAST_LIST.get(sid) or [])

            def idx_to_pair_local(i: int) -> Optional[Tuple[str, str]]:
                if 1 <= i <= len(pairs_snapshot):
                    return pairs_snapshot[i - 1]
                return None

            targets: List[Tuple[str, str]] = []
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
            else:
                replies.append("삭제할 일정을 찾지 못했어요.")
                actions.append({"ok": False, "error": "not_found"})
                continue

            seen = set()
            uniq_targets: List[Tuple[str, str]] = []
            for t in targets:
                if t and t not in seen:
                    seen.add(t)
                    uniq_targets.append(t)

            if not uniq_targets:
                replies.append("삭제할 일정을 찾지 못했어요.")
                actions.append({"ok": False, "error": "not_found"})
                continue

            deleted_events_for_block: List[dict] = []
            deleted_indices_for_block: List[int] = []
            deleted_fallback_lines: List[str] = []

            for eid, cal in uniq_targets:
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
                    replies.append(f"일정 삭제 중 오류가 발생했어요: {ex.detail}")
                    actions.append({"ok": False, "error": "not_found"})

            if deleted_events_for_block:
                block = _render_list_block(deleted_events_for_block, indices=deleted_indices_for_block)
                replies.append("🗑️ 다음 일정을 삭제했어요:\n" + ZERO + "\n" + block)
            if deleted_fallback_lines:
                replies.append("🗑️ 다음 항목은 스냅샷이 없어 간략히 표시했어요:\n" + "\n".join(deleted_fallback_lines))
            continue

        # 상세 by 인덱스
        if name == "get_event_detail_by_index":
            idx = int(args["index"])
            pair = _map_index_to_pair(sid, idx)
            if not pair:
                replies.append("해당 번호의 일정을 찾을 수 없어요.")
                actions.append({"ok": False, "error": "index_out_of_range"})
                continue
            event_id, cal_id = pair
            try:
                e = gcal_get_event(sid, cal_id, event_id)
                replies.append(_fmt_detail_g(e))
                actions.append({"detail": _pack_g(e)})
            except HTTPException:
                replies.append("해당 일정을 찾지 못했어요.")
                actions.append({"ok": False, "error": "not_found"})
            continue

        # 상세 by 아이디/인덱스
        if name == "get_event_detail":
            event_id = None
            cal_id = None
            if "index" in args and args["index"]:
                pair = _map_index_to_pair(sid, int(args["index"]))
                if pair:
                    event_id, cal_id = pair
            if not event_id and args.get("id"):
                event_id = str(args["id"])
                cal_id = _find_cal_for_id(sid, event_id) or "primary"

            if not event_id:
                replies.append("해당 일정을 찾지 못했어요.")
                actions.append({"ok": False, "error": "not_found"})
                continue

            try:
                e = gcal_get_event(sid, cal_id, event_id)
                replies.append(_fmt_detail_g(e))
                actions.append({"detail": _pack_g(e)})
            except HTTPException:
                replies.append("해당 일정을 찾지 못했어요.")
                actions.append({"ok": False, "error": "not_found"})
            continue

        # 편집 시작
        if name == "start_edit":
            event_id = None
            cal_id = None
            if args.get("index"):
                pair = _map_index_to_pair(sid, int(args["index"]))
                if pair:
                    event_id, cal_id = pair
            elif args.get("id"):
                event_id = str(args["id"])
                cal_id = _find_cal_for_id(sid, event_id)

            if not event_id:
                replies.append("대상을 찾을 수 없어요. 먼저 '전체 일정 보여줘'로 목록을 띄워주세요.")
                actions.append({"ok": False, "error": "not_found"})
            else:
                try:
                    e = gcal_get_event(sid, cal_id or "primary", event_id)
                    replies.append(
                        "수정할 항목을 알려주세요. (제목/시간/설명/위치/참석자)\n"
                        "(참석자는 이메일 주소로 입력해주세요)\n\n" + _fmt_detail_g(e)
                    )
                    actions.append({"detail": _pack_g(e)})
                except HTTPException:
                    replies.append("대상을 찾을 수 없어요.")
                    actions.append({"ok": False, "error": "not_found"})
            continue

    # After any mutation, refresh the latest snapshot list so follow-up indexes reflect the new state.
    if did_mutation:
        items = gcal_list_events_all(sid, None, None, None)
        SESSION_LAST_LIST[sid] = [(it.get("id"), it.get("_calendarId") or "primary") for it in items]
        SESSION_LAST_ITEMS[sid] = items
        block = _render_list_block(items)
        replies.append("\n변경 후 최신 목록입니다:\n" + ZERO + "\n" + (block if block else "남아있는 일정이 없어요."))
        actions.append({"list": [{"idx": i + 1, **_pack_g(e)} for i, e in enumerate(items)]})

    reply = "\n\n".join(replies) if replies else "완료했습니다."
    reply = _sanitize_llm_reply_text(reply, allow_helper=False)
    return ChatOut(reply=reply, tool_result={"actions": actions})
