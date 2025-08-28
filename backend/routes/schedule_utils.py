# routes/schedule_utils.py
# 이메일 유틸

import re

# 간단한 이메일 정규식(로컬/도메인 포함)
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

def _split_valid_invalid_attendees(v):
    """
    참석자 입력 값들을 유효 이메일과 무효 토큰으로 분리한다.
    문자열/딕셔너리 혼용을 지원하며, 딕셔너리일 경우 emial|value|address 키를 읽는다.

    :param v: 단일 값 또는 리스트(문자열/딕셔너리 혼합 가능)
    :type v: any
    :return: (valid_emails, invalid_tokens)
    :rtype: Tuple[List[str], List[str]]
    """

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