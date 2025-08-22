# 시간 / 포맷 / 정규식

import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

KST = timezone(timedelta(hours=9))
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]

ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?")
ISO_PAREN_EXAMPLE_RE = re.compile(
    r"\s*\([^)]*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?[^)]*\)\s*"
)
HELPER_NOTE_PREFIX = "(날짜/시간은 자연어로 적어주세요"
HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

def _now_kst_iso() -> str:
    return datetime.now(KST).isoformat()

def _friendly_today() -> str:
    n = datetime.now(KST)
    return f"{n.strftime('%Y-%m-%d')} ({WEEKDAY_KO[n.weekday()]}) {n.strftime('%H:%M')}"

def _parse_hhmm(s: str) -> Optional[Tuple[int, int]]:
    m = HHMM_RE.match(s.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None

def _strip_tz_keep_wallclock(s: str) -> str:
    return re.sub(r"(Z|[+-]\d{2}:\d{2})$", "", s.strip())

def _get_kst(dt_str: Optional[str]):
    if not dt_str:
        return None
    if len(dt_str) == 10:
        return datetime.fromisoformat(dt_str + "T00:00:00+09:00")
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(KST)

def _fmt_kst_date(dt: Optional[datetime]) -> str:
    return "없음" if not dt else f"{dt.strftime('%Y-%m-%d')} ({WEEKDAY_KO[dt.weekday()]})"

def _fmt_kst_time(dt: Optional[datetime]) -> str:
    return "없음" if not dt else dt.strftime("%H:%M")

def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(KST).isoformat()

def _parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    s = dt_str.strip()
    try:
        if len(s) == 10:
            dt = datetime.fromisoformat(s + "T00:00:00")
            return dt.replace(tzinfo=KST)
        s_no_tz = _strip_tz_keep_wallclock(s)
        dt = datetime.fromisoformat(s_no_tz)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt.replace(tzinfo=KST)
    except Exception:
        return None

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
