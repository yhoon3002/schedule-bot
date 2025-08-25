# routes/schedule_utils.py
# 이메일 유틸

import re

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

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