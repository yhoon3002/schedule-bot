# routes/schedule_time.py
# 시간 / 포맷 / 정규식

import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

KST = timezone(timedelta(hours=9))
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]

# ISO 타임스탬프 탐지(ex: 2025-08-26T12:34:56Z / +09:00 등등)
ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?")
# 괄호 안에 ISO 예시가 들어간 도움말 라인 제거용
ISO_PAREN_EXAMPLE_RE = re.compile(
    r"\s*\([^)]*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?[^)]*\)\s*"
)
HELPER_NOTE_PREFIX = "(날짜/시간은 자연어로 적어주세요"
HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

def _now_kst_iso() -> str:
    """
    현재 시각을 KST ISO 8601 문자열로 반환한다.

    :return: +09:00 오프셋을 포함한 ISO 문자열
    :rtype: str
    """

    return datetime.now(KST).isoformat()

def _friendly_today() -> str:
    """
    KST 기준 '오늘'을 사람이 읽기 쉬운 형식으로 반환한다.
    예: "2025-08-25 (월) 13:20

    :return: "YYYY-MM-DD (요일) HH:MM" 형태 문자열
    :rtype: str
    """

    n = datetime.now(KST)
    return f"{n.strftime('%Y-%m-%d')} ({WEEKDAY_KO[n.weekday()]}) {n.strftime('%H:%M')}"

def _parse_hhmm(s: str) -> Optional[Tuple[int, int]]:
    """
    'HH:MM' 형식을 (hour, minute) 튜플로 파싱한다.

    :param s: 시각 문자열
    :type s: str
    :return: (시, 분) 또는 None
    :rtype: Optional[Tuple[int, int]]
    """

    m = HHMM_RE.match(s.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None

def _strip_tz_keep_wallclock(s: str) -> str:
    # 문자열 끝의 타임존(Z/+-HH:MM)을 제거해 벽시계 시간을 유지
    return re.sub(r"(Z|[+-]\d{2}:\d{2})$", "", s.strip())

def _get_kst(dt_str: Optional[str]):
    """
    구글 캘린더의 date/dateTime 문자열을 KST datetime으로 변환한다.
    날짜만 있으면(YYYY-MM-DD) 00:00으로 간주한다.(종일 이벤트)

    :param dt_str: 날짜/날짜시간 문자열
    :type dt_str: Optional[str]
    :return: KST 타임존의 datetime 또는 None
    :rtype: Optional[datetime]
    """

    if not dt_str:
        return None
    if len(dt_str) == 10:
        return datetime.fromisoformat(dt_str + "T00:00:00+09:00")
    # `Z`를 +00:00으로 바꾼 뒤 KST로 변환
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(KST)

def _rfc3339(dt: datetime) -> str:
    """
    datetime을 +09:00 오프셋이 포함된 ISO 8601 문자열로 반환한다.

    :param dt: 기준 datetime
    :type dt: datetime
    :return: ISO 8601 문자열(+09:00)
    :rtype: str
    """

    return dt.astimezone(KST).isoformat()

def _parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
    """
    사용자 입력(YYYY-MM-DD 또는 ISO 유사 형식)을 KST datetime으로 파싱한다.
    입력에 타임존이 있어도 벽시계 시간을 보존하고 KST로 지정한다.

    :param dt_str: 날짜/시간 문자열
    :type dt_str: Optional[str]
    :return: KST datetime 또는 None(파싱 실패)
    :rtype: Optional[datetime]
    """

    if not dt_str:
        return None
    s = dt_str.strip()
    try:
        if len(s) == 10:
            # YYYY-MM-DD -> 00:00 KST
            dt = datetime.fromisoformat(s + "T00:00:00")
            return dt.replace(tzinfo=KST)
        # 끝의 TZ를 지워 벽시계 값을 유지한 뒤 KST 지정
        s_no_tz = _strip_tz_keep_wallclock(s)
        dt = datetime.fromisoformat(s_no_tz)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt.replace(tzinfo=KST)
    except Exception:
        return None

def _iso_str_to_kst_friendly(iso_str: str) -> str:
    """
    ISO 타임스탬프를 한국어 친화 문자열로 바꾼다.

    :param iso_str: ISO 유사 타임스탬프(Z 포함 가능)
    :type iso_str: str
    :return: "YYYY-MM-DD (요일) HH:MM" 문자열
    :rtype: str
    """

    try:
        s = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        dt = (dt if dt.tzinfo else dt.replace(tzinfo=KST)).astimezone(KST)
        w = WEEKDAY_KO[dt.weekday()]
        return f"{dt.strftime('%Y-%m-%d')} ({w}) {dt.strftime('%H:%M')}"
    except Exception:
        return iso_str

def _sanitize_llm_reply_text(text: str, *, allow_helper: bool) -> str:
    """
    모델 생성 텍스트를 사용자 노출용으로 정리한다.
    - ISO 예시가 괄호로 들어간 문구 제거
    - 생 ISO 타임스탬프를 친화 포맷으로 치환
    - '이 형식으로 입력' 같은 가이드라인 문구 제거

    :param text: 모델 원문
    :type text: str
    :param allow_helper: False면 도움말성 라인도 제거
    :type allow_helper: bool
    :return: 정리된 텍스트
    :rtype: str
    """

    if not text:
        return text
    out_lines = []
    for raw in text.splitlines():
        # 괄호 속 ISO 예시 제거
        line = ISO_PAREN_EXAMPLE_RE.sub("", raw).rstrip()
        # ISO 타임스탬프를 KST 친화 포맷으로 치환
        line = ISO_TS_RE.sub(lambda m: _iso_str_to_kst_friendly(m.group(0)), line)
        # 과도한 가이드 문구 제거
        if ("형식으로 입력" in line) or ("정확한 형식" in line) or ("YYYY-" in line):
            continue
        if "일정 생성에 필요한 추가 정보를 요청드립니다" in line:
            continue
        if (not allow_helper) and (HELPER_NOTE_PREFIX in line):
            continue
        # 다중 공백 정리
        line = re.sub(r"\s{2,}", " ", line).rstrip()
        out_lines.append(line)
    cleaned = "\n".join(out_lines).strip()
    return cleaned or text