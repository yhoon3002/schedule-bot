# 필터 & where

from typing import List, Optional
from datetime import timedelta
from routes.google_calendar import gcal_list_events_all
from routes.schedule_time import _get_kst, _parse_dt, _rfc3339, _parse_hhmm
from routes.schedule_render import _render_list_block  # (사용처가 있으면)

def _ci_contains(text: Optional[str], needle: str) -> bool:
    if text is None: return False
    try: return needle.lower() in text.lower()
    except Exception: return False

def _any_ci_contains(text: Optional[str], needles: List[str]) -> bool:
    return all(_ci_contains(text, n) for n in needles) if needles else True

def _none_ci_contains(text: Optional[str], needles: List[str]) -> bool:
    return not any(_ci_contains(text, n) for n in needles) if needles else True

def _attendee_emails(e: dict) -> List[str]:
    return [a.get("email", "").lower() for a in (e.get("attendees") or []) if a.get("email")]

def _is_all_day_event(e: dict) -> bool:
    s = e.get("start", {})
    return "date" in s and "dateTime" not in s

def _duration_minutes(e: dict):
    st = _get_kst(e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"))
    ed_raw = e.get("end", {}).get("dateTime") or e.get("end", {}).get("date")
    ed = _get_kst(ed_raw) if ed_raw else None
    if st and ed:
        return int((ed - st).total_seconds() // 60)
    return None

def _end_kst(e: dict):
    ed_raw = e.get("end", {}).get("dateTime") or e.get("end", {}).get("date")
    return _get_kst(ed_raw) if ed_raw else None

def _apply_filters(items: List[dict], filters: Optional[dict]) -> List[dict]:
    if not filters: return items

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
        loc  = e.get("location") or ""
        emails = _attendee_emails(e)
        dur = _duration_minutes(e)
        st = (e.get("status") or "").lower()
        cal_id = e.get("_calendarId") or "primary"

        if not _any_ci_contains(title, ti): continue
        if not _none_ci_contains(title, te): continue
        if not _any_ci_contains(desc, di):  continue
        if not _none_ci_contains(desc, de): continue
        if not _any_ci_contains(loc, li):   continue
        if not _none_ci_contains(loc, le):  continue

        if has_at is True and len(emails) == 0: continue
        if has_at is False and len(emails) > 0: continue
        if email_in:
            lower_emails = set(emails)
            if not any(any(em in ae for ae in lower_emails) for em in email_in): continue

        if has_loc is True and not loc.strip(): continue
        if has_loc is False and loc.strip():    continue

        if is_all_day is True and not _is_all_day_event(e): continue
        if is_all_day is False and _is_all_day_event(e):    continue

        if min_d is not None and (dur is None or dur < int(min_d)): continue
        if max_d is not None and (dur is None or dur > int(max_d)): continue

        if status and st != status: continue
        if cals_in and cal_id not in cals_in: continue

        st_dt = _get_kst(e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"))
        ed_dt = _end_kst(e)
        if end_before and (not ed_dt or not (ed_dt < end_before)): continue
        if end_after  and (not ed_dt or not (ed_dt > end_after)): continue
        if end_time_eq:
            if not ed_dt: continue
            hh, mm = end_time_eq
            if not (ed_dt.hour == hh and ed_dt.minute == mm): continue
        if starts_on and (not st_dt or st_dt.strftime("%Y-%m-%d") != starts_on): continue
        if ends_on   and (not ed_dt or ed_dt.strftime("%Y-%m-%d") != ends_on):   continue

        out.append(e)
    return out

def _resolve_where(sid: str, where: Optional[dict]) -> List[dict]:
    if not where:
        return []
    f_raw = where.get("from"); t_raw = where.get("to")
    pf = _parse_dt(f_raw); pt = _parse_dt(t_raw)
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