# 렌더/ 서식

from typing import List, Optional
from routes.schedule_time import _get_kst, _fmt_kst_date, _fmt_kst_time

ZERO = "\u200B"
INDENT_ITEM = "  "
INDENT_SECTION = "    "

def _indent_block(text: str, level: int = 1) -> str:
    prefix = "  " * level
    return "\n".join((prefix + ln) if ln.strip() else ln for ln in text.splitlines())

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
        "attendees": [a.get("email") for a in (e.get("attendees") or []) if a.get("email")],
        "status": e.get("status"),
    }