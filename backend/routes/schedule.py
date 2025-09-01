# 일정(스케줄) 관련 라우터. LLM이 도구(tool)를 호출하면 여기의 핸들러들이 실제 Google Calendar API 호출을 수행함.
import logging
from datetime import timedelta
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
from routes.schedule_spec import SYSTEM_POLICY_TEMPLATE
from routes.schedule_openai import _openai_chat_multi_step
from routes.schedule_utils import _split_valid_invalid_attendees
from routes.schedule_time import (
    _parse_dt,
    _rfc3339,
    _now_kst_iso,
    _friendly_today,
)
from routes.schedule_render import _pack_g
from routes.schedule_filters import _apply_filters, _resolve_where
from routes.schedule_state import (
    refresh_session_cache,
    _find_snapshot_item,
    _map_index_to_pair,
    _find_cal_for_id,
    SESSION_LAST_LIST,
    SESSION_LAST_ITEMS,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/schedules", tags=["schedules"])
# Google Calendar 권한 범위. 사용자가 이 스코프로 로그인되어 있어야 함.
CAL_SCOPE = "https://www.googleapis.com/auth/calendar"

# 세션 단위 캐시(메모리 저장소)
# - LLM이 '미리보기 -> 확인(confirmed) -> 실행' 흐름을 쓰기에, 확인 대기 상태를 세션 별로 잠시 저장해 둠.
SESSION_PENDING_DELETE: Dict[str, List[Tuple[str, str]]] = {}   # 삭제 후보 목록
SESSION_PENDING_UPDATE_NOTIFY: Dict[str, Dict[str, Any]] = {}   # 업데이트(알림 선택 대기)
SESSION_PENDING_CREATE: Dict[str, Dict[str, Any]] = {}  # 생성(미리보기/알림 선택 대기)


# IO 모델(요청/응답 스키마)
class ChatIn(BaseModel):
    """
    /schedules/chat 엔드포인트 입력 스키마
    """
    user_message: str
    history: Optional[list] = None
    session_id: Optional[str] = None


class ChatOut(BaseModel):
    """
    /schedules/chat 엔드포인트 출력 스키마
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)
    reply: str
    tool_result: Optional[Any] = None


# 헬퍼
def _must_google_connected(session_id: str):
    """
    현재 세션이 Google Calendar 권한으로 연결되어 있는지 확인함.
    연결되어 있지 않으면 401 에러 발생.

    :param session_id: 세션 ID(토큰 조회 키)
    :type session_id: str
    :raises HTTPException: 캘린더 권한이 없으면 401
    """
    tok = TOKENS.get(session_id or "")
    scope = (tok.get("scope") if tok else "") or ""
    if not (tok and CAL_SCOPE in scope):
        raise HTTPException(status_code=401, detail="Google 로그인/캘린더 연동이 필요합니다.")


def _dedupe_emails(emails: Optional[List[str]]) -> List[str]:
    """
    이메일 목록에서 중복/공백을 제거하고 소문자로 정규화함.

    :param emails: 이메일 문자열 리스트(또는 None)
    :type emails: Optional[List[str]]
    :return: 중복 제거된 이메일 리스트
    :rtype: List[str]
    """
    out: List[str] = []
    seen: set = set()
    for e in emails or []:
        ee = (e or "").strip().lower()
        if ee and ee not in seen:
            seen.add(ee)
            out.append(ee)
    return out


def create_tool_handler(sid: str):
    """
    세션별 도구 핸들러 팩토리.
    LLM이 호출하는 function tool 이름에 따라 해당 핸들러로 라우팅함.

    :param sid: 세션 ID
    :type sid: str
    :return: (function_name, args) -> result(dict) 형태의 Callable
    :rtype: callable
    """

    def handle_tool(function_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        LLM의 개별 도구 호출을 실제 핸들러로 연결함

        :param function_name: 도구(함수) 이름 (예: "create_event")
        :type function_name: str
        :param args: 도구 인수(JSON)
        :type args: Dict[str, Any]
        :return: 실행 결과(보통 {"actions": [...]} 형태
        :rtype: Dict[str, Any]
        """
        try:
            if function_name == "list_events":
                return handle_list_events(sid, args)
            elif function_name == "create_event":
                return handle_create_event(sid, args)
            elif function_name == "update_event":
                return handle_update_event(sid, args)
            elif function_name == "delete_event":
                return handle_delete_event(sid, args)
            elif function_name == "get_event_detail":
                return handle_get_event_detail(sid, args)
            elif function_name == "get_event_detail_by_index":
                return handle_get_event_detail_by_index(sid, args)
            elif function_name == "start_edit":
                return handle_start_edit(sid, args)
            else:
                return {"actions": [{"ok": False, "error": f"Unknown function: {function_name}"}]}

        except Exception as e:
            logger.error(f"Error in tool handler {function_name}: {e}")
            return {"actions": [{"ok": False, "error": str(e)}]}

    return handle_tool


# 개별 도구 핸들러들(LLM아 호출함)
def handle_list_events(sid: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    일정 목록 조회
    1. 구글 캘린더에서 범위/검색어 기준으로 일정 가져오기
    2. 후처리 필터(_apply_filters) 적용
    3. 최신 결과를 세션 캐시에 저장(목록/아이템)
    4. LLM이 읽기 좋은 형태로 포장(_pack_g) 후 반환

    :param sid: 세션 ID
    :type sid: str
    :param args: from/to/query/filters/include_holidays/include_birthdays 등 검색 인자
    :type args: Dict[str, Any]
    :return: {"actions": [{"list": [...]}]}
    :rtype: Dict[str, Any]
    """
    items = gcal_list_events_all(
        sid,
        args.get("from"),
        args.get("to"),
        args.get("query") or None,
        bool(args.get("include_holidays", False)),
        bool(args.get("include_birthdays", False)),
    )
    filtered = _apply_filters(items, args.get("filters") or {})

    # 인덱스 선택 기능을 위해 최근 결과를 캐시에 저장
    SESSION_LAST_LIST[sid] = [(it.get("id"), it.get("_calendarId") or "primary") for it in filtered]
    SESSION_LAST_ITEMS[sid] = filtered

    return {
        "actions": [{
            "list": [{"idx": i + 1, **_pack_g(e)} for i, e in enumerate(filtered)]
        }]
    }


def handle_create_event(sid: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    일정 생성

    첫번째 호출(confirmed=False): '미리보기' 제공 + 세션캐시에 저장
    두번째 호출(confirmed=True): '알림 여부' 결정 후 실제 생성

    :param sid: 세션 ID
    :type sid: str
    :param args: title/start/end/description/location/attendees/confirmed/notify_attendees ... 등
    :type args: Dict[str, Any]
    :return: actions 배열(미리보기 or created 결과)
    :rtype: Dict[str, Any]
    """

    # 1) (확정단계) 이전에 미리보기로 저장한 내용이 있으면 그것을 기반으로 생성
    pending_create = SESSION_PENDING_CREATE.get(sid)
    if args.get("confirmed", False) and pending_create:
        if pending_create.get("has_attendees") and args.get("notify_attendees") is None:
            return {
                "actions": [{
                    "ok": False,
                    "need_notify_choice": True,
                    "pending_create": pending_create["body"]
                }]
            }

        # 실제 생성 실행
        body = pending_create["body"]
        send_updates = None
        if pending_create.get("has_attendees"):
            send_updates = "all" if args.get("notify_attendees") else "none"

        try:
            e = gcal_insert_event(sid, body, send_updates=send_updates)
            refresh_session_cache(sid)  # 캐시 새로고침
            return {"actions": [{"created": _pack_g(e)}]}
        except HTTPException as ex:
            return {"actions": [{"ok": False, "error": ex.detail}]}
        finally:
            SESSION_PENDING_CREATE.pop(sid, None)

    # 2) (미리보기 단계) 새로 입력된 내용으로 유효성 검사
    attendees_input = args.get("attendees")
    valid_emails, invalids = _split_valid_invalid_attendees(attendees_input)
    if invalids:
        return {"actions": [{"ok": False, "error": "invalid_attendees", "invalid": invalids}]}

    if attendees_input is not None:
        valid_emails = _dedupe_emails(valid_emails)

    start_dt = _parse_dt(args.get("start"))
    if not start_dt:
        return {"actions": [{"ok": False, "error": "invalid_start"}]}

    end_dt = _parse_dt(args.get("end"))
    if (end_dt is None) or (end_dt <= start_dt):
        # 종료가 없거나 / 시작보다 빠르면 +1시간으로 보정함
        end_dt = start_dt + timedelta(hours=1)

    # Google Calendar insert body 구성
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
        body["attendees"] = [{"email": email} for email in valid_emails]

    has_attendees = bool(valid_emails)

    # 2-1) 미리보기만 요청된 경우 -> 캐시에 저장 후 미리보기 반환
    if not args.get("confirmed", False):
        SESSION_PENDING_CREATE[sid] = {
            "body": body,
            "has_attendees": has_attendees
        }
        return {
            "actions": [{
                "ok": False,
                "need_confirm": True,
                "preview": body
            }]
        }

    # 2-2) 확정인데 참석자 있고 알림 여부가 미정이면 알림 선택부터
    if has_attendees and args.get("notify_attendees") is None:
        SESSION_PENDING_CREATE[sid] = {
            "body": body,
            "has_attendees": has_attendees
        }
        return {
            "actions": [{
                "ok": False,
                "need_notify_choice": True,
                "pending_create": body
            }]
        }

    # 2-3) 모든 정보가 준비되면 즉시 생성
    send_updates = None
    if has_attendees:
        send_updates = "all" if args.get("notify_attendees") else "none"

    try:
        e = gcal_insert_event(sid, body, send_updates=send_updates)
        refresh_session_cache(sid)  # 캐시 새로고침
        return {"actions": [{"created": _pack_g(e)}]}
    except HTTPException as ex:
        return {"actions": [{"ok": False, "error": ex.detail}]}


def handle_update_event(sid: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    일정 수정

    - 첫 번째 호출(confirmed=False): 대상 탐색/미리보기/알림 선택 유도
    - 두 번째 호출(confirmed=True): 실제 patch 수행

    :param sid: 세션 ID
    :type sid: str
    :param args: id/index/where/patch/confirmed/notify_attendees emd
    :type args: Dict[str, Any]
    :return: actions 배열(need_confirm/need_index/updated 등)
    :rtype: Dict[str, Any]
    """

    logger.debug(f"[UPDATE] Called with args: {args}")

    # 1) (확정단계) 이전에 저장된 알림 여부 대기가 있으면 그걸로 적용
    pending = SESSION_PENDING_UPDATE_NOTIFY.get(sid)
    if args.get("confirmed", False) and pending:
        if pending.get("has_new_attendees") and args.get("notify_attendees") is None:
            return {
                "actions": [{
                    "ok": False,
                    "need_notify_choice": True,
                    "pending_update": {
                        "event_id": pending["event_id"],
                        "calendar_id": pending["calendar_id"],
                        "body": pending["body"],
                    }
                }]
            }

        # 실제 수정 실행
        send_updates = None
        if pending.get("has_new_attendees"):
            send_updates = "all" if args.get("notify_attendees") else "none"

        try:
            logger.debug(f"[UPDATE] Calling gcal_patch_event with event_id={pending['event_id']}, body={pending['body']}")
            e = gcal_patch_event(
                sid,
                pending["event_id"],
                pending["body"],
                pending["calendar_id"],
                send_updates=send_updates
            )
            logger.debug(f"[UPDATE] gcal_patch_event succeeded: {e.get('id')}")
            refresh_session_cache(sid)  # 캐시 새로고침
            return {"actions": [{"updated": _pack_g(e)}]}
        except HTTPException as ex:
            logger.error(f"[UPDATE] gcal_patch_event failed: {ex}")
            return {"actions": [{"ok": False, "error": ex.detail}]}
        finally:
            SESSION_PENDING_UPDATE_NOTIFY.pop(sid, None)

    # 2) 수정 대상 이벤트 식별(id/index/where 지원)
    event_id = None
    cal_id = None

    if "index" in args and args["index"]:
        pair = _map_index_to_pair(sid, int(args["index"]))
        if pair:
            event_id, cal_id = pair

    if not event_id and args.get("id"):
        raw_id = str(args.get("id")).strip()
        if raw_id.isdigit() and len(raw_id) < 6:
            # 사용자가 인덱스를 id 필드로 준 경우(관용 처리)
            pair = _map_index_to_pair(sid, int(raw_id))
            if pair:
                event_id, cal_id = pair
        else:
            event_id = raw_id
            cal_id = _find_cal_for_id(sid, event_id) or "primary"

    # 2-1) patch 데이터 구성(부분 수정 허용)
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
    has_new_attendees = False
    if "attendees" in p:
        emails, invalids = _split_valid_invalid_attendees(p.get("attendees"))
        if invalids:
            return {"actions": [{"ok": False, "error": "invalid_attendees", "invalid": invalids}]}
        valid_emails = _dedupe_emails(emails or [])
        body_base["attendees"] = [{"email": email} for email in valid_emails]

    # 2-2) where 조건으로 대상 찾기(모호하면 need_index로 분기)
    matched: List[dict] = []
    if not event_id and args.get("where"):
        matched = _resolve_where(sid, args.get("where") or {})
        if not matched:
            return {"actions": [{"ok": False, "error": "not_found"}]}
        if len(matched) == 1:
            target = matched[0]
            event_id = target.get("id")
            cal_id = target.get("_calendarId") or "primary"

    if not event_id and matched:
        return {
            "actions": [{
                "ok": False,
                "need_index": True,
                "candidates": [_pack_g(x) for x in matched],
                "preview_patch": body_base
            }]
        }

    if not event_id:
        return {"actions": [{"ok": False, "error": "not_found"}]}

    # 2-3) 기존 스냅샷을 가져와 새 참석자 추가 여부 및 시간 보정 판단
    snapshot_before = None
    try:
        snapshot_before = gcal_get_event(sid, cal_id or "primary", event_id)
    except HTTPException:
        pass

    # 참석자 비교 -> 새 참석자가 추가되었는지 판단(알림 필요 여부때문에)
    if valid_emails is not None and snapshot_before:
        before_emails = set([a.get("email", "").strip().lower()
                             for a in (snapshot_before.get("attendees") or [])])
        after_emails = set([e.strip().lower() for e in valid_emails])
        newly_added = after_emails - before_emails
        has_new_attendees = bool(newly_added)

    # 시작만 바뀌고 종료가 비어있으면 기존 종료를 기준으로 최소 1시간으로 보정
    if new_start_dt and (not new_end_dt) and snapshot_before:
        cur_end_dt = _parse_dt(
            snapshot_before.get("end", {}).get("dateTime") or
            snapshot_before.get("end", {}).get("date")
        )
        if (cur_end_dt is None) or (cur_end_dt <= new_start_dt):
            body_base.setdefault("end", {})["dateTime"] = _rfc3339(new_start_dt + timedelta(hours=1))

    # 3) 미리보기 단계
    if not args.get("confirmed", False):
        SESSION_PENDING_UPDATE_NOTIFY[sid] = {
            "event_id": event_id,
            "calendar_id": cal_id or "primary",
            "body": body_base,
            "has_new_attendees": has_new_attendees,
        }
        return {
            "actions": [{
                "ok": False,
                "need_confirm": True,
                "preview_patch": body_base,
                "before": _pack_g(snapshot_before) if snapshot_before else None
            }]
        }

    # 4) 확정인데 새 참석자 + 알림 여부 미지정 -> 알림 선택 요청
    if has_new_attendees and args.get("notify_attendees") is None:
        SESSION_PENDING_UPDATE_NOTIFY[sid] = {
            "event_id": event_id,
            "calendar_id": cal_id or "primary",
            "body": body_base,
            "has_new_attendees": has_new_attendees,
        }
        return {
            "actions": [{
                "ok": False,
                "need_notify_choice": True,
                "pending_update": {
                    "event_id": event_id,
                    "calendar_id": cal_id or "primary",
                    "body": body_base,
                }
            }]
        }

    # 5) 모든 정보 준비되면 -> 즉시 patch
    send_updates = None
    if has_new_attendees:
        send_updates = "all" if args.get("notify_attendees") else "none"
    try:
        e = gcal_patch_event(sid, event_id, body_base, cal_id or "primary", send_updates=send_updates)
        refresh_session_cache(sid)  # 캐시 새로고침
        return {"actions": [{"updated": _pack_g(e)}]}
    except HTTPException as ex:
        return {"actions": [{"ok": False, "error": ex.detail}]}
    finally:
        SESSION_PENDING_UPDATE_NOTIFY.pop(sid, None)


def handle_delete_event(sid: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    일정 삭제

    - 첫번째 호출(confirmed=False): 삭제 대상 미리보기 + 확인 유도
    - 두번째 호출(confirmed=True): 실제 삭제 진행(캐시에 저장된 타겟 기준)

    :param sid: 세션 ID
    :type sid: str
    :param args: where/index/indexes/id/ids/confirmed 등
    :type args: Dict[str, Any]
    :return: actions 배열(need_confirm/deleted 등)
    :rtype: Dict[str, Any]
    """

    # 1) (확정단계) 캐시에 삭제 후보가 있으면 실제 삭제 진행
    if args.get("confirmed", False):
        cached = SESSION_PENDING_DELETE.get(sid) or []
        if cached:
            # 실제 삭제 실행
            actions = []
            for eid, cal in cached:
                try:
                    gcal_delete_event(sid, eid, cal or "primary")
                    snap = _find_snapshot_item(sid, eid, cal)
                    actions.append({"deleted": _pack_g(snap) if snap else {"id": eid, "calendarId": cal}})
                except HTTPException:
                    actions.append({"ok": False, "error": "not_found"})

            SESSION_PENDING_DELETE.pop(sid, None)
            refresh_session_cache(sid)  # 캐시 새로고침
            return {"actions": actions}

    # 세션에 저장된 마지막 목록의 N번째를 (id, cal_id)로 변환하는 헬퍼
    def idx_to_pair_local(i: int):
        pairs = SESSION_LAST_LIST.get(sid) or []
        if 1 <= i <= len(pairs):
            return pairs[i - 1]
        return None

    targets: List[Tuple[str, str]] = []

    # 2) where 조건으로 찾기(여러 개면 확인부터함)
    candidates: List[dict] = []
    if args.get("where"):
        w = args.get("where") or {}
        candidates = _resolve_where(sid, w)

        if not candidates:
            return {"actions": [{"ok": False, "error": "not_found"}]}

        if len(candidates) == 1:
            c = candidates[0]
            targets.append((c.get("id"), c.get("_calendarId") or "primary"))
        else:
            SESSION_PENDING_DELETE[sid] = [(c.get("id"), c.get("_calendarId") or "primary") for c in candidates]
            return {
                "actions": [{
                    "ok": False,
                    "need_confirm": True,
                    "candidates": [_pack_g(x) for x in candidates]
                }]
            }

    # 3) index/id로 지정된 경우
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
        return {"actions": [{"ok": False, "error": "not_found"}]}

    # 4) 미리보기 단계 -> 삭제 후보들을 캐시에 저장하고 확인 요청
    if not args.get("confirmed", False):
        preview_items: List[dict] = []
        for eid, cal in targets:
            snap = _find_snapshot_item(sid, eid, cal)
            if snap:
                preview_items.append(snap)
        SESSION_PENDING_DELETE[sid] = targets  # 캐시에 저장
        return {
            "actions": [{
                "ok": False,
                "need_confirm": True,
                "preview_delete": [list(t) for t in targets],
                "preview_items": [_pack_g(x) for x in preview_items]
            }]
        }

    # confirmed=True는 함수 초반에서 처리하므로 여기까지 도달하면 비정상이라는 것임
    return {"actions": [{"ok": False, "error": "unexpected_state"}]}


def handle_get_event_detail(sid: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    일정 상세 조회

    :param sid: 세션 ID
    :type sid: str
    :param args: id/index/where 중 하나로 대상을 지정함
    :type args: Dict[str, Any]
    :return: {"actions": [{"detail": {...}}]} 또는 에러
    :rtype: Dict[str, Any]
    """

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
            return {"actions": [{"ok": False, "error": "not_found"}]}
        if len(matched) == 1:
            event_id = matched[0].get("id")
            cal_id = matched[0].get("_calendarId") or "primary"

    if not event_id and matched:
        return {"actions": [{"ok": False, "need_index": True, "candidates": [_pack_g(x) for x in matched]}]}

    if not event_id:
        return {"actions": [{"ok": False, "error": "not_found"}]}

    try:
        e = gcal_get_event(sid, cal_id, event_id)
        return {"actions": [{"detail": _pack_g(e)}]}
    except HTTPException:
        return {"actions": [{"ok": False, "error": "not_found"}]}


def handle_get_event_detail_by_index(sid: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    인덱스 기반 일정 상세 조회

    :param sid: 세션 ID
    :type sid: str
    :param args: {"index": N}
    :type args: Dict[str, Any]
    :return: {"actions": [{"detail": {...}}]} 또는 에러
    :rtype: Dict[str, Any]
    """

    idx = int(args["index"])
    pair = _map_index_to_pair(sid, idx)
    if not pair:
        return {"actions": [{"ok": False, "error": "index_out_of_range"}]}
    event_id, cal_id = pair
    try:
        e = gcal_get_event(sid, cal_id, event_id)
        return {"actions": [{"detail": _pack_g(e)}]}
    except HTTPException:
        return {"actions": [{"ok": False, "error": "not_found"}]}


def handle_start_edit(sid: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    편집 시작(클라이언트에서 상세를 띄우기 위한 사전 조회)

    :param sid: 세션 ID
    :type sid: str
    :param args: index/id/where 중 하나로 대상 지정
    :type args: Dict[str, Any]
    :return: {"actions": [{"detail": {...}, "ok": True}]} 또는 에러
    :rtype: Dict[str, Any]
    """

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
        return {"actions": [{"ok": False, "need_index": True, "candidates": [_pack_g(x) for x in matched]}]}

    if not event_id:
        return {"actions": [{"ok": False, "error": "not_found"}]}
    else:
        try:
            e = gcal_get_event(sid, cal_id or "primary", event_id)
            return {"actions": [{"detail": _pack_g(e), "ok": True}]}
        except HTTPException:
            return {"actions": [{"ok": False, "error": "not_found"}]}


# 라우터 엔드포인트
@router.post("/chat", response_model=ChatOut)
def chat(input: ChatIn):
    """
    개선된 채팅 엔드포인트(다단계 도구 실행 지원)

    동작 개요
    - 시스템 프롬프트 구성(정책/현재시각/자연어 날짜 등 포함)
    - 대화 히스토리에 유저 메시지 추가
    - 세션별 도구 핸들러 생성
    - LLM을 다단계 실행기로 호출(_openai_chat_multi_step) -> 도구 호출/미리보기/확정/알림선택 등 플로우 자동 처리

    :param input: 사용자 입력(문장/이전 히스토리/세션 ID)
    :type input: ChatIn
    :raises HTTPException: Google Calendar 미연동 시 401(내부 _must_google_connected)
    :return: (reply: 사용자용 요약 멘트, tool_result: 프론트용 액션 번들)
    :rtype: ChatOut
    """
    sid = (input.session_id or "").strip()
    _must_google_connected(sid)

    # 시스템 프롬프트(정책 + 현재 시각/날짜 표현치환)
    system_prompt = (
        SYSTEM_POLICY_TEMPLATE
        .replace("{NOW_ISO}", _now_kst_iso())
        .replace("{TODAY_FRIENDLY}", _friendly_today())
    )

    # LLM 대화 히스토리 구성
    msgs = [{"role": "system", "content": system_prompt}]
    if input.history:
        msgs += input.history
    msgs.append({"role": "user", "content": input.user_message})

    # 도구 핸들러 생성
    tool_handler = create_tool_handler(sid)

    # 다단계 실행(도구 호출 -> 미리보기 -> 확인 -> 실제 반영)
    try:
        reply, tool_result = _openai_chat_multi_step(msgs, sid, tool_handler)
        return ChatOut(reply=reply, tool_result=tool_result)
    except Exception as e:
        logger.error(f"Chat processing error: {e}")
        return ChatOut(
            reply="죄송합니다. 요청을 처리하는 중 오류가 발생했습니다.",
            tool_result=None
        )