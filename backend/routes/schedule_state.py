# 세션 / 매핑

from typing import Dict, List, Tuple, Any, Optional
from routes.google_calendar import gcal_list_events_all

SESSION_LAST_LIST: Dict[str, List[Tuple[str, str]]] = {}
SESSION_LAST_ITEMS: Dict[str, List[Dict[str, Any]]] = {}

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