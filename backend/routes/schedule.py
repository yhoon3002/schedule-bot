import json, logging
from typing import Optional, Dict, Any, List, Tuple
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from routes.google_oauth import TOKENS
from routes.google_calendar import (
    gcal_list_events_all, gcal_insert_event, gcal_patch_event,
    gcal_delete_event, gcal_get_event,
)

# === 기능별 모듈 import (기존 함수/상수 이름 그대로 사용) ===
from routes.schedule_spec import TOOLS_SPEC, SYSTEM_POLICY_TEMPLATE, ALLOWED_TOOLS
from routes.schedule_openai import _openai_chat
from routes.schedule_utils import _split_valid_invalid_attendees
from routes.schedule_time import (
    _parse_dt, _rfc3339, _sanitize_llm_reply_text, _now_kst_iso, _friendly_today,
)
from routes.schedule_render import _pack_g, _fmt_detail_g
from routes.schedule_filters import _apply_filters, _resolve_where
from routes.schedule_state import (
    SESSION_LAST_LIST, SESSION_LAST_ITEMS,
    _find_snapshot_item, _map_index_to_pair, _find_cal_for_id
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/schedules", tags=["schedules"])
CAL_SCOPE = "https://www.googleapis.com/auth/calendar"

def _must_google_connected(session_id: str):
    tok = TOKENS.get(session_id or "")
    scope = (tok.get("scope") if tok else "") or ""
    ok = bool(tok and CAL_SCOPE in scope)
    if not ok:
        raise HTTPException(status_code=401, detail="Google 로그인/캘린더 연동이 필요합니다.")

class ChatIn(BaseModel):
    user_message: str
    history: Optional[list] = None
    session_id: Optional[str] = None

class ChatOut(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    reply: str
    tool_result: Optional[Any] = None

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

    # 1차 호출: 모델이 어떤 도구를 쓸지 결정
    data = _openai_chat(msgs)
    choice = data["choices"][0]
    tool_calls = choice.get("message", {}).get("tool_calls") or []

    # LLM이 만든 자연어(있을 수도, 없을 수도)
    llm_narration = _sanitize_llm_reply_text(choice["message"].get("content") or "", allow_helper=True)

    # 도구 호출이 없으면 LLM의 자연어만 그대로 반환
    if not tool_calls:
        return ChatOut(reply=llm_narration, tool_result=None)

    # 도구 실행: 고정 문구를 만들지 않고 actions만 쌓는다.
    actions: List[Dict[str, Any]] = []
    did_mutation = False
    created_events_agg: List[dict] = []
    updated_events_agg: List[dict] = []

    for tc in tool_calls:
        name = tc["function"]["name"]
        raw_args = tc["function"].get("arguments") or "{}"
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

        # === list ===
        if name == "list_events":
            items = gcal_list_events_all(
                sid,
                args.get("from"),
                args.get("to"),
                args.get("query") or None,
                bool(args.get("include_holidays", False)),
                bool(args.get("include_birthdays", False)),
            )
            filtered = _apply_filters(items, args.get("filters") or {})
            SESSION_LAST_LIST[sid] = [(it.get("id"), it.get("_calendarId") or "primary") for it in filtered]
            SESSION_LAST_ITEMS[sid] = filtered
            actions.append({"list": [{"idx": i + 1, **_pack_g(e)} for i, e in enumerate(filtered)]})
            continue

        # === create ===
        if name == "create_event":
            attendees_input = args.get("attendees")
            valid_emails, invalids = _split_valid_invalid_attendees(attendees_input)
            if invalids:
                actions.append({"ok": False, "error": "invalid_attendees", "invalid": invalids})
                continue

            start_dt = _parse_dt(args.get("start"))
            if not start_dt:
                actions.append({"ok": False, "error": "invalid_start"})
                continue

            end_dt = _parse_dt(args.get("end"))
            if (end_dt is None) or (end_dt <= start_dt):
                from datetime import timedelta
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

            # 1) 항상 확인부터: 텍스트 없이 신호만
            if not args.get("confirmed", False):
                actions.append({"ok": False, "need_confirm": True, "preview": body})
                continue

            # 2) 확인 이후: 참석자가 존재하고 notify 미지정 → 메일 여부만 신호
            has_attendees_after = bool(body.get("attendees"))
            notify = args.get("notify_attendees", None)
            if has_attendees_after and notify is None:
                actions.append({
                    "ok": False,
                    "need_notify_choice": True,
                    "pending_create": body
                })
                continue

            # 3) 실행
            send_updates = None
            if has_attendees_after:
                send_updates = "all" if args.get("notify_attendees") else "none"

            e = gcal_insert_event(sid, body, send_updates=send_updates)
            created_events_agg.append(e)
            actions.append({"created": _pack_g(e)})
            did_mutation = True
            continue

        # === update ===
        if name == "update_event":
            from datetime import timedelta
            event_id = None
            cal_id = None
            apply_all = bool(args.get("apply_to_all", False))

            if "index" in args and args["index"]:
                pair = _map_index_to_pair(sid, int(args["index"]))
                if pair: event_id, cal_id = pair
            if not event_id and args.get("id"):
                raw_id = str(args.get("id")).strip()
                if raw_id.isdigit() and len(raw_id) < 6:
                    pair = _map_index_to_pair(sid, int(raw_id))
                    if pair: event_id, cal_id = pair
                else:
                    event_id = raw_id
                    cal_id = _find_cal_for_id(sid, event_id) or "primary"

            p = args.get("patch") or {}
            body_base: Dict[str, Any] = {}
            if "title" in p: body_base["summary"] = p["title"]

            new_start_dt = _parse_dt(p.get("start"))
            new_end_dt   = _parse_dt(p.get("end"))
            if new_start_dt: body_base.setdefault("start", {})["dateTime"] = _rfc3339(new_start_dt)
            if new_end_dt:   body_base.setdefault("end", {})["dateTime"]   = _rfc3339(new_end_dt)
            if "description" in p: body_base["description"] = p["description"]
            if "location" in p:    body_base["location"]    = p["location"]

            valid_emails = None
            if "attendees" in p:
                from routes.schedule_utils import _split_valid_invalid_attendees as _split
                valid_emails, invalids = _split(p.get("attendees"))
                if invalids:
                    actions.append({"ok": False, "error": "invalid_attendees", "invalid": invalids})
                    continue
                body_base["attendees"] = valid_emails

            matched: List[dict] = []
            if not event_id and args.get("where"):
                matched = _resolve_where(sid, args.get("where") or {})
                if not matched:
                    actions.append({"ok": False, "error": "not_found"})
                    continue
                if len(matched) == 1:
                    target = matched[0]
                    event_id = target.get("id")
                    cal_id = target.get("_calendarId") or "primary"

            if not event_id and matched:
                actions.append({"ok": False, "need_index": True, "candidates": [_pack_g(x) for x in matched], "preview_patch": body_base})
                continue

            if not event_id:
                actions.append({"ok": False, "error": "not_found"})
                continue

            snapshot_before = None
            try:
                snapshot_before = gcal_get_event(sid, cal_id or "primary", event_id)
            except HTTPException:
                pass

            if new_start_dt and (not new_end_dt):
                cur_end_dt = _parse_dt(snapshot_before.get("end", {}).get("dateTime") or snapshot_before.get("end", {}).get("date")) if snapshot_before else None
                if (cur_end_dt is None) or (cur_end_dt <= new_start_dt):
                    body_base.setdefault("end", {})["dateTime"] = _rfc3339(new_start_dt + timedelta(hours=1))

            # 1) 확인 단계 (텍스트 없음)
            if not args.get("confirmed", False):
                actions.append({
                    "ok": False,
                    "need_confirm": True,
                    "preview_patch": body_base,
                    "before": _pack_g(snapshot_before) if snapshot_before else None,
                })
                continue

            # 2) 확인 이후: "새로 추가된 참석자"가 있고 notify 미지정이면 질문 신호
            send_updates = None
            need_notify_query = False
            if valid_emails is not None:
                before_set = set([a.get("email") for a in (snapshot_before.get("attendees") or []) if a.get("email")]) if snapshot_before else set()
                after_set  = set(valid_emails or [])
                newly_added = after_set - before_set
                if newly_added and (args.get("notify_attendees") is None):
                    need_notify_query = True

            if need_notify_query:
                actions.append({
                    "ok": False,
                    "need_notify_choice": True,
                    "pending_update": {
                        "event_id": event_id,
                        "calendar_id": cal_id or "primary",
                        "body": body_base,
                    }
                })
                continue

            if valid_emails is not None and args.get("notify_attendees") is not None:
                send_updates = "all" if args.get("notify_attendees") else "none"

            try:
                e = gcal_patch_event(sid, event_id, body_base, cal_id or "primary", send_updates=send_updates)
                updated_events_agg.append(e)
                actions.append({"updated": _pack_g(e)})
                did_mutation = True
            except HTTPException as ex:
                actions.append({"ok": False, "error": ex.detail})
            continue

        # === delete ===
        if name == "delete_event":
            pairs_snapshot: List[Tuple[str, str]] = list(SESSION_LAST_LIST.get(sid) or [])
            apply_all = bool(args.get("apply_to_all", False))

            def idx_to_pair_local(i: int):
                if 1 <= i <= len(pairs_snapshot): return pairs_snapshot[i - 1]
                return None

            targets: List[Tuple[str, str]] = []

            if args.get("where"):
                candidates = _resolve_where(sid, args.get("where"))
                if not candidates:
                    actions.append({"ok": False, "error": "not_found"})
                    continue
                if len(candidates) == 1:
                    c = candidates[0]
                    targets.append((c.get("id"), c.get("_calendarId") or "primary"))
                else:
                    if not args.get("confirmed", False):
                        actions.append({"ok": False, "need_confirm": True, "candidates": [ _pack_g(x) for x in candidates ]})
                        continue
                    if apply_all:
                        for c in candidates:
                            targets.append((c.get("id"), c.get("_calendarId") or "primary"))
                    else:
                        actions.append({"ok": False, "need_index": True})
                        continue

            if args.get("indexes"):
                for i in args["indexes"]:
                    p = idx_to_pair_local(int(i))
                    if p: targets.append(p)
            elif args.get("index"):
                p = idx_to_pair_local(int(args["index"]))
                if p: targets.append(p)
            elif args.get("ids"):
                for eid in args["ids"]:
                    cal = _find_cal_for_id(sid, str(eid))
                    if cal: targets.append((str(eid), cal))
            elif args.get("id"):
                eid = str(args["id"])
                cal = _find_cal_for_id(sid, eid)
                if cal: targets.append((eid, cal))

            if not targets:
                actions.append({"ok": False, "error": "not_found"})
                continue

            if not args.get("confirmed", False):
                preview_items: List[dict] = []; idx_list: List[int] = []; fallback_lines: List[str] = []
                for eid, cal in targets:
                    snap = _find_snapshot_item(sid, eid, cal)
                    if snap:
                        try: idx_display = pairs_snapshot.index((eid, cal)) + 1
                        except ValueError: idx_display = len(idx_list) + 1
                        preview_items.append(snap); idx_list.append(idx_display)
                    else:
                        fallback_lines.append(f"- id={eid} (calendar={cal})")
                actions.append({
                    "ok": False,
                    "need_confirm": True,
                    "preview_delete": [list(t) for t in targets],
                    "preview_items": [ _pack_g(x) for x in preview_items ]
                })
                continue

            for eid, cal in targets:
                try:
                    gcal_delete_event(sid, eid, cal or "primary")
                    snap = _find_snapshot_item(sid, eid, cal)
                    actions.append({"deleted": _pack_g(snap) if snap else {"id": eid, "calendarId": cal}})
                    did_mutation = True
                except HTTPException:
                    actions.append({"ok": False, "error": "not_found"})
            continue

        # === detail by index ===
        if name == "get_event_detail_by_index":
            idx = int(args["index"])
            pair = _map_index_to_pair(sid, idx)
            if not pair:
                actions.append({"ok": False, "error": "index_out_of_range"})
                continue
            event_id, cal_id = pair
            try:
                e = gcal_get_event(sid, cal_id, event_id)
                actions.append({"detail": _pack_g(e)})
            except HTTPException:
                actions.append({"ok": False, "error": "not_found"})
            continue

        # === detail by id/where ===
        if name == "get_event_detail":
            event_id = None; cal_id = None; matched: List[dict] = []
            if "index" in args and args["index"]:
                pair = _map_index_to_pair(sid, int(args["index"]))
                if pair: event_id, cal_id = pair
            if not event_id and args.get("id"):
                event_id = str(args["id"]); cal_id = _find_cal_for_id(sid, event_id) or "primary"
            if not event_id and args.get("where"):
                matched = _resolve_where(sid, args.get("where"))
                if not matched:
                    actions.append({"ok": False, "error": "not_found"})
                    continue
                if len(matched) == 1:
                    event_id = matched[0].get("id")
                    cal_id = matched[0].get("_calendarId") or "primary"

            if not event_id and matched:
                actions.append({"ok": False, "need_index": True, "candidates": [_pack_g(x) for x in matched]})
                continue

            if not event_id:
                actions.append({"ok": False, "error": "not_found"})
                continue

            try:
                e = gcal_get_event(sid, cal_id, event_id)
                actions.append({"detail": _pack_g(e)})
            except HTTPException:
                actions.append({"ok": False, "error": "not_found"})
            continue

        # === start_edit ===
        if name == "start_edit":
            event_id = None; cal_id = None; matched: List[dict] = []
            if args.get("index"):
                pair = _map_index_to_pair(sid, int(args["index"]))
                if pair: event_id, cal_id = pair
            elif args.get("id"):
                event_id = str(args["id"]); cal_id = _find_cal_for_id(sid, event_id)
            elif args.get("where"):
                matched = _resolve_where(sid, args.get("where"))
                if len(matched) == 1:
                    event_id = matched[0].get("id")
                    cal_id = matched[0].get("_calendarId") or "primary"

            if not event_id and matched:
                actions.append({"ok": False, "need_index": True, "candidates": [_pack_g(x) for x in matched]})
                continue

            if not event_id:
                actions.append({"ok": False, "error": "not_found"})
            else:
                try:
                    e = gcal_get_event(sid, cal_id or "primary", event_id)
                    actions.append({"detail": _pack_g(e), "ok": True})
                except HTTPException:
                    actions.append({"ok": False, "error": "not_found"})
            continue

    if created_events_agg:
        actions.append({"created_list": [_pack_g(e) for e in created_events_agg]})
    if updated_events_agg:
        actions.append({"updated_list": [_pack_g(e) for e in updated_events_agg]})
    if did_mutation:
        items = gcal_list_events_all(sid, None, None, None, False, False)
        SESSION_LAST_LIST[sid] = [(it.get("id"), it.get("_calendarId") or "primary") for it in items]
        SESSION_LAST_ITEMS[sid] = items
        actions.append({"list": [{"idx": i + 1, **_pack_g(e)} for i, e in enumerate(items)]})

    # ---------- NEW: 2차 호출로 '자연어 최종 답변' 생성 ----------
    # 도구 실행 결과(actions)만 넘겨, 모델이 자체적으로 자연어 답변을 쓰게 한다.
    # (서버는 고정 문장 생성 금지)
    def _second_pass_summarize(user_msg: str, tool_actions: List[dict]) -> str:
        try:
            summarize_system = (
                "너는 일정 비서다. 다음 JSON은 도구 실행 결과(actions) 목록이다.\n"
                "- 사용자의 질문에 맞춰 한국어로 자연스럽게 한 번만 답하라.\n"
                "- 숫자나 개수는 actions 내용을 기반으로 정확히 계산해라.\n"
                "- ISO 문자열이나 내부 키 이름을 노출하지 말라.\n"
                "- 너무 장황하게 말하지 말고, 의도에 맞춰 간결하게 답하라.\n"
            )
            summarize_msgs = [
                {"role": "system", "content": summarize_system},
                {"role": "user", "content": f"사용자 질문: {user_msg}\n\n도구 결과(JSON):\n{json.dumps({'actions': tool_actions}, ensure_ascii=False)}"}
            ]
            out = _openai_chat(summarize_msgs)
            c = out["choices"][0]["message"].get("content") or ""
            return _sanitize_llm_reply_text(c, allow_helper=True)
        except Exception:
            return ""

    # 기본적으로 2차 요약을 시도한다.
    final_reply = _second_pass_summarize(input.user_message, actions)

    # 안전장치: 2차 요약이 비면, 1차 내러티브(있다면) 사용
    if not final_reply.strip():
        final_reply = llm_narration

    return ChatOut(reply=final_reply or "", tool_result={"actions": actions})
