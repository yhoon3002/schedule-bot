# routes/schedule_state.py (수정된 버전)
# 세션 / 매핑 - 개선된 캐시 관리

from typing import Dict, List, Tuple, Any, Optional
from routes.google_calendar import gcal_list_events_all
import logging

logger = logging.getLogger(__name__)

# 마지막 조회 결과를 세션별로 기억(인덱스 <-> (eventId, calendarId) 매핑)
SESSION_LAST_LIST: Dict[str, List[Tuple[str, str]]] = {}
SESSION_LAST_ITEMS: Dict[str, List[Dict[str, Any]]] = {}

# 캐시 무효화를 위한 버전 관리
SESSION_CACHE_VERSION: Dict[str, int] = {}


def refresh_session_cache(sid: str, force: bool = False) -> List[Dict[str, Any]]:
    """
    세션 캐시를 새로고침

    Args:
        sid: 세션 ID
        force: 강제 새로고침 여부

    Returns:
        업데이트된 이벤트 목록
    """
    try:
        # 전체 이벤트 목록 가져오기
        items = gcal_list_events_all(sid, None, None, None, False, False)

        # 캐시 업데이트
        SESSION_LAST_LIST[sid] = [
            (item.get("id"), item.get("_calendarId", "primary"))
            for item in items
        ]
        SESSION_LAST_ITEMS[sid] = items
        SESSION_CACHE_VERSION[sid] = SESSION_CACHE_VERSION.get(sid, 0) + 1

        logger.debug(f"Refreshed cache for session {sid}: {len(items)} events")
        return items

    except Exception as e:
        logger.error(f"Failed to refresh cache for session {sid}: {e}")
        return []


def get_cached_events(sid: str, auto_refresh: bool = True) -> List[Dict[str, Any]]:
    """
    캐시된 이벤트 목록 반환 (필요시 자동 새로고침)

    Args:
        sid: 세션 ID
        auto_refresh: 캐시가 없을 때 자동 새로고침 여부

    Returns:
        이벤트 목록
    """
    items = SESSION_LAST_ITEMS.get(sid, [])

    if not items and auto_refresh:
        items = refresh_session_cache(sid)

    return items


def invalidate_session_cache(sid: str):
    """세션 캐시 무효화"""
    SESSION_LAST_LIST.pop(sid, None)
    SESSION_LAST_ITEMS.pop(sid, None)
    SESSION_CACHE_VERSION.pop(sid, None)
    logger.debug(f"Invalidated cache for session {sid}")


def _find_snapshot_item(sid: str, event_id: str, cal_id: str) -> Optional[Dict[str, Any]]:
    """
    마지막 리스트 스냅샷에서 미리보기 용도로 이벤트를 찾는다(예: 삭제 미리보기).
    """
    items = get_cached_events(sid, auto_refresh=False)
    for e in items:
        if e.get("id") == event_id and (e.get("_calendarId") or "primary") == (cal_id or "primary"):
            return e
    return None


def _map_index_to_pair(sid: str, idx: int) -> Optional[Tuple[str, str]]:
    """
    마지막 조회 목록의 1-base 인덱스를 (eventId, calendarId)로 변환한다.
    """
    pairs = SESSION_LAST_LIST.get(sid) or []
    if 1 <= idx <= len(pairs):
        return pairs[idx - 1]
    return None


def _find_cal_for_id(sid: str, event_id: str) -> Optional[str]:
    """
    주어진 이벤트 ID가 속한 캘린더 ID를 스냅샷 또는 전체 재조회로 찾는다.
    """
    pairs = SESSION_LAST_LIST.get(sid) or []
    cal = next((c for (eid, c) in pairs if eid == event_id), None)
    if cal:
        return cal

    # 캐시에 없으면 새로고침 후 다시 시도
    refresh_session_cache(sid)
    pairs = SESSION_LAST_LIST.get(sid) or []
    cal = next((c for (eid, c) in pairs if eid == event_id), None)
    if cal:
        return cal

    # 마지막 수단: 직접 API 호출
    try:
        items = gcal_list_events_all(sid, None, None, None, False, False)
        hit = next((x for x in items if x.get("id") == event_id), None)
        return hit.get("_calendarId") if hit else None
    except Exception as e:
        logger.error(f"Failed to find calendar for event {event_id}: {e}")
        return None