# routes/schedule.py
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
    KST, WEEKDAY_KO, _parse_dt, _rfc3339, _iso_str_to_kst_friendly,
    _sanitize_llm_reply_text, _now_kst_iso, _friendly_today,
)
from routes.schedule_render import (
    ZERO, INDENT_ITEM, INDENT_SECTION, _indent_block,
    _fmt_detail_g, _render_list_block, _pack_g
)
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

    data = _openai_chat(msgs)
    choice = data["choices"][0]
    tool_calls = choice.get("message", {}).get("tool_calls") or []

    if not tool_calls:
        reply = choice["message"].get("content") or \
            "일정 관련 요청을 말씀해 주세요.\n\n예) 이번달 내 일정은? / 참석자 있는 일정만 보여줘 / '약'으로 등록된 일정 삭제"
        reply = _sanitize_llm_reply_text(reply, allow_helper=True)
        return ChatOut(reply=reply, tool_result=None)

    replies: List[str] = []
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

            if not filtered:
                replies.append("  조건에 맞는 일정이 없어요.\n\n")
                actions.append({"list": []})
            elif len(filtered) == 1:
                e = filtered[0]
                replies.append("  다음 일정을 찾았어요. \n 이 일정이 맞으신가요? : \n" + "\n" + _indent_block(_fmt_detail_g(e), 2))
                actions.append({"list": [_pack_g(e)]})
            else:
                block = _render_list_block(filtered)
                replies.append("  여러 일정이 있어요. 번호를 선택하시면 상세 정보를 보여드릴게요.\n\n" + _indent_block(block, 1))
                actions.append({"list": [{"idx": i + 1, **_pack_g(e)} for i, e in enumerate(filtered)]})
            continue

        # === create ===
        if name == "create_event":
            attendees_input = args.get("attendees")
            valid_emails, invalids = _split_valid_invalid_attendees(attendees_input)
            if invalids:
                replies.append(
                    "  참석자는 이메일 주소로만 입력할 수 있어요.\n\n"
                    + "\n".join(f"  - {x}" for x in invalids)
                    + "\n\n  올바른 이메일(예: name@example.com)로 다시 알려주세요."
                )
                actions.append({"ok": False, "error": "invalid_attendees", "invalid": invalids})
                continue

            start_dt = _parse_dt(args.get("start"))
            if not start_dt:
                replies.append("  시작 시간을 이해하지 못했어요.\n\n  예: '8월 25일 13:00'처럼 자연어로 말씀해 주세요.")
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

            if not args.get("confirmed", False):
                desc = (body.get("description") or "없음")
                loc = (body.get("location") or "없음")
                atts = ", ".join(valid_emails) if valid_emails else "없음"
                notify = args.get("notify_attendees")
                notify_str = "예" if notify else ("아니오" if notify is not None else "미지정")
                needs_notify = bool(valid_emails) and (notify is None)

                summary = (
                    "    이대로 생성할까요?\n\n"
                    f"    1. 제목: {body['summary']}\n"
                    f"    2. 시작: {_iso_str_to_kst_friendly(body['start']['dateTime'])}\n"
                    f"    3. 종료: {_iso_str_to_kst_friendly(body['end']['dateTime'])}\n"
                    f"    4. 설명: {desc}\n"
                    f"    5. 위치: {loc}\n"
                    f"    6. 참석자: {atts}\n"
                    f"    7. 초대 메일 발송: {notify_str}\n"
                )
                if needs_notify:
                    summary += (
                        "\n"
                        "    ※ 참석자가 있어요. 초대 메일을 보낼까요? (예/아니오)\n"
                        "    → 예라면 다음 호출에서 `notify_attendees=true`, 아니오라면 `notify_attendees=false` 로 보내주세요.\n"
                    )
                summary += "\n    진행할까요? (예/아니오)"

                replies.append(summary)
                action_obj = {"ok": False, "need_confirm": True, "preview": body}
                if needs_notify:
                    action_obj["need_notify_choice"] = True
                actions.append(action_obj)
                continue

            send_updates = None
            if valid_emails:
                notify = args.get("notify_attendees", None)
                send_updates = "all" if notify else "none" if notify is not None else None

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
                    replies.append(
                        "  참석자는 이메일 주소로만 입력할 수 있어요.\n\n"
                        + "\n".join(f"  - {x}" for x in invalids)
                        + "\n\n  올바른 이메일(예: name@example.com)로 다시 알려주세요."
                    )
                    actions.append({"ok": False, "error": "invalid_attendees", "invalid": invalids})
                    continue
                body_base["attendees"] = valid_emails

            matched: List[dict] = []
            if not event_id and args.get("where"):
                matched = _resolve_where(sid, args.get("where") or {})
                if not matched:
                    replies.append("  조건과 일치하는 일정이 없어요.")
                    actions.append({"ok": False, "error": "not_found"})
                    continue
                if len(matched) == 1:
                    target = matched[0]
                    event_id = target.get("id")
                    cal_id = target.get("_calendarId") or "primary"

            if not event_id and matched:
                if not args.get("confirmed", False):
                    patch_lines = []
                    if "summary" in body_base: patch_lines.append(f"- 제목 → {body_base['summary']}")
                    if "start" in body_base:   patch_lines.append(f"- 시작 → {_iso_str_to_kst_friendly(body_base['start']['dateTime'])}")
                    if "end" in body_base:     patch_lines.append(f"- 종료 → {_iso_str_to_kst_friendly(body_base['end']['dateTime'])}")
                    if "description" in body_base: patch_lines.append(f"- 설명 → {body_base['description'] or '없음'}")
                    if "location" in body_base:    patch_lines.append(f"- 위치 → {body_base['location'] or '없음'}")
                    if "attendees" in body_base:
                        atts = ", ".join(body_base["attendees"]) if body_base["attendees"] else "없음"
                        patch_lines.append(f"- 참석자 → {atts}")

                    needs_notify = ("attendees" in body_base) and bool(body_base.get("attendees")) and (args.get("notify_attendees") is None)
                    block = _render_list_block(matched)
                    msg = (
                        "    여러 일정이 발견됐어요.\n\n"
                        "    다음 **모든 일정에 동일 수정**을 적용할까요?\n\n"
                        + _indent_block(block, 2)
                        + ("\n\n    수정 요약:\n" + _indent_block("\n".join(patch_lines) or "- (변경 없음)", 3))
                        + "\n"
                    )
                    if needs_notify:
                        msg += (
                            "\n"
                            "    ※ 참석자 변경(또는 추가)이 있어요. 초대 메일을 보낼까요? (예/아니오)\n"
                            "    → 예라면 다음 호출에서 `notify_attendees=true`, 아니오라면 `notify_attendees=false` 로 보내주세요.\n"
                        )
                    msg += "\n    진행할까요? (예/아니오)\n    (하나만 수정하려면 번호를 선택해 주세요.)"

                    replies.append(msg)
                    action_obj = {
                        "ok": False,
                        "need_confirm": True,
                        "preview_patch": body_base,
                        "candidates": [_pack_g(x) for x in matched],
                    }
                    if needs_notify:
                        action_obj["need_notify_choice"] = True
                    actions.append(action_obj)
                    continue

                if apply_all:
                    send_updates = None
                    if valid_emails is not None:
                        notify = args.get("notify_attendees", None)
                        if notify is not None:
                            send_updates = "all" if notify else "none"

                    for m in matched:
                        eid = m.get("id"); cid = m.get("_calendarId") or "primary"
                        body = dict(body_base)
                        if ("start" in body) and ("end" not in body):
                            cur_end_dt = _parse_dt(m.get("end", {}).get("dateTime") or m.get("end", {}).get("date"))
                            start_dt = _parse_dt(body["start"]["dateTime"])
                            if (cur_end_dt is None) or (cur_end_dt <= start_dt):
                                body.setdefault("end", {})["dateTime"] = _rfc3339(start_dt + timedelta(hours=1))
                        e = gcal_patch_event(sid, eid, body, cid, send_updates=send_updates)
                        updated_events_agg.append(e)
                        actions.append({"updated": _pack_g(e)})
                        did_mutation = True
                    continue

                block = _render_list_block(matched)
                replies.append("    번호를 선택해 주세요.\n\n" + _indent_block(block, 2))
                actions.append({"ok": False, "need_index": True})
                continue

            if not event_id:
                replies.append("  수정할 대상을 찾지 못했어요.")
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

            if not args.get("confirmed", False):
                before_str = _fmt_detail_g(snapshot_before) if snapshot_before else "(이전 정보 조회 불가)"
                after_dummy = snapshot_before.copy() if snapshot_before else {}
                if "summary" in body_base:   after_dummy["summary"] = body_base["summary"]
                if "description" in body_base: after_dummy["description"] = body_base["description"]
                if "location" in body_base:  after_dummy["location"] = body_base["location"]
                if "start" in body_base:     after_dummy.setdefault("start", {})["dateTime"] = body_base["start"]["dateTime"]
                if "end" in body_base:       after_dummy.setdefault("end", {})["dateTime"] = body_base["end"]["dateTime"]
                if "attendees" in body_base: after_dummy["attendees"] = [{"email": x} for x in body_base["attendees"]]

                notify = args.get("notify_attendees", None)
                notify_str = "예" if notify else ("아니오" if notify is not None else "미지정")

                before_set = set([a.get("email") for a in (snapshot_before.get("attendees") or []) if a.get("email")]) if snapshot_before else set()
                after_set  = set(body_base.get("attendees") or [a.get("email") for a in (snapshot_before.get("attendees") or []) if a.get("email")])
                newly_added = after_set - before_set
                needs_notify = (notify is None) and (bool(after_set) or bool(newly_added))

                preview = (
                    "    다음과 같이 수정할까요?\n\n"
                    "    1. 변경 전:\n"
                    f"{_indent_block(before_str, 3)}\n\n"
                    "    2. 변경 후(미리보기):\n"
                    f"{_indent_block(_fmt_detail_g(after_dummy), 3)}\n\n"
                    f"    3. 초대 메일 발송: {notify_str}\n"
                )
                if needs_notify:
                    preview += (
                        "\n"
                        "    ※ 참석자가 존재하거나 새로 추가됩니다. 초대 메일을 보낼까요? (예/아니오)\n"
                        "    → 예라면 다음 호출에서 `notify_attendees=true`, 아니오라면 `notify_attendees=false` 로 보내주세요.\n"
                    )
                preview += "\n    진행할까요? (예/아니오)"

                replies.append(preview)
                action_obj = {"ok": False, "need_confirm": True, "preview_patch": body_base}
                if needs_notify:
                    action_obj["need_notify_choice"] = True
                actions.append(action_obj)
                continue

            send_updates = None
            if valid_emails is not None:
                notify = args.get("notify_attendees", None)
                if notify is not None:
                    send_updates = "all" if notify else "none"

            try:
                e = gcal_patch_event(sid, event_id, body_base, cal_id or "primary", send_updates=send_updates)
                updated_events_agg.append(e)
                actions.append({"updated": _pack_g(e)})
                did_mutation = True
            except HTTPException as ex:
                replies.append(f"  일정 수정 중 오류가 발생했어요.\n\n  사유: {ex.detail}")
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
                    replies.append("  조건과 일치하는 일정이 없어요.")
                    actions.append({"ok": False, "error": "not_found"})
                    continue
                if len(candidates) == 1:
                    c = candidates[0]
                    targets.append((c.get("id"), c.get("_calendarId") or "primary"))
                else:
                    if not args.get("confirmed", False):
                        block = _render_list_block(candidates)
                        replies.append("    아래 후보가 있어요. 모두 삭제할까요?\n\n" + _indent_block(block, 2) + "\n\n    진행할까요? (예/아니오)\n    (하나만 삭제하려면 번호를 알려주세요.)")
                        actions.append({"ok": False, "need_confirm": True, "candidates": [ _pack_g(x) for x in candidates ]})
                        continue
                    if apply_all:
                        for c in candidates:
                            targets.append((c.get("id"), c.get("_calendarId") or "primary"))
                    else:
                        block = _render_list_block(candidates)
                        replies.append("    번호를 선택해 주세요.\n\n" + _indent_block(block, 2))
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
                replies.append("  삭제할 일정을 찾지 못했어요.")
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
                preview_text = ""
                if preview_items: preview_text += _render_list_block(preview_items, indices=idx_list)
                if fallback_lines: preview_text += ("\n" if preview_text else "") + "\n".join(fallback_lines)
                replies.append("    아래 일정을 삭제할까요?\n\n" + _indent_block(preview_text or "(표시할 항목 없음)", 2) + "\n\n" + "    진행할까요? (예/아니오)")
                actions.append({"ok": False, "need_confirm": True, "preview_delete": [list(t) for t in targets]})
                continue

            deleted_events_for_block: List[dict] = []; deleted_indices_for_block: List[int] = []; deleted_fallback_lines: List[str] = []
            for eid, cal in targets:
                snap = _find_snapshot_item(sid, eid, cal)
                fallback = f"- id={eid} (calendar={cal})"
                try:
                    gcal_delete_event(sid, eid, cal or "primary")
                    if snap:
                        actions.append({"deleted": _pack_g(snap)})
                        try: idx_display = pairs_snapshot.index((eid, cal)) + 1
                        except ValueError: idx_display = None
                        deleted_events_for_block.append(snap)
                        deleted_indices_for_block.append(idx_display or len(deleted_events_for_block))
                    else:
                        actions.append({"deleted": {"id": eid, "calendarId": cal}})
                        deleted_fallback_lines.append(fallback)
                    did_mutation = True
                except HTTPException as ex:
                    replies.append(f"  일정 삭제 중 오류가 발생했어요.\n\n  사유: {ex.detail}")
                    actions.append({"ok": False, "error": "not_found"})

            if deleted_events_for_block:
                block = _render_list_block(deleted_events_for_block, indices=deleted_indices_for_block)
                replies.append("    🗑️ 다음 일정을 삭제했어요.\n\n" + _indent_block(block, 1))
            if deleted_fallback_lines:
                replies.append("    🗑️ 스냅샷이 없어 간략히 표시한 항목:\n\n" + _indent_block("\n".join(deleted_fallback_lines), 1))
            continue

        # === detail by index ===
        if name == "get_event_detail_by_index":
            idx = int(args["index"])
            pair = _map_index_to_pair(sid, idx)
            if not pair:
                replies.append("  해당 번호의 일정을 찾을 수 없어요.\n\n  최근 조회 목록을 다시 띄워주세요.")
                actions.append({"ok": False, "error": "index_out_of_range"})
                continue
            event_id, cal_id = pair
            try:
                e = gcal_get_event(sid, cal_id, event_id)
                replies.append(_indent_block(_fmt_detail_g(e), 2))
                actions.append({"detail": _pack_g(e)})
            except HTTPException:
                replies.append("  해당 일정을 찾지 못했어요.\n\n  이미 변경/삭제되었을 수 있어요.")
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
                    replies.append("  해당 조건의 일정을 찾지 못했어요.")
                    actions.append({"ok": False, "error": "not_found"})
                    continue
                if len(matched) == 1:
                    event_id = matched[0].get("id")
                    cal_id = matched[0].get("_calendarId") or "primary"

            if not event_id and matched:
                block = _render_list_block(matched)
                replies.append("  여러 일정이 있어요. 번호를 선택해 주세요.\n\n" + _indent_block(block, 2))
                actions.append({"ok": False, "need_index": True})
                continue

            if not event_id:
                replies.append("  해당 일정을 찾지 못했어요.")
                actions.append({"ok": False, "error": "not_found"})
                continue

            try:
                e = gcal_get_event(sid, cal_id, event_id)
                replies.append(_indent_block(_fmt_detail_g(e), 2))
                actions.append({"detail": _pack_g(e)})
            except HTTPException:
                replies.append("  해당 일정을 찾지 못했어요.\n\n  이미 변경/삭제되었을 수 있어요.")
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
                block = _render_list_block(matched)
                replies.append("  여러 일정이 있어요. 번호를 선택해 주세요.\n\n" + _indent_block(block, 2))
                actions.append({"ok": False, "need_index": True})
                continue

            if not event_id:
                replies.append("  대상을 찾을 수 없어요.\n\n  조건을 다시 알려주세요.")
                actions.append({"ok": False, "error": "not_found"})
            else:
                try:
                    e = gcal_get_event(sid, cal_id or "primary", event_id)
                    replies.append(
                        "    수정할 항목을 알려주세요.\n\n"
                        "    1. 제목\n"
                        "    2. 시간(시작/종료)\n"
                        "    3. 설명\n"
                        "    4. 위치\n"
                        "    5. 참석자(이메일)\n\n"
                        + _indent_block(_fmt_detail_g(e), 2)
                    )
                    actions.append({"detail": _pack_g(e)})
                except HTTPException:
                    replies.append("  대상을 찾을 수 없어요.\n\n  이미 변경/삭제되었을 수 있어요.")
                    actions.append({"ok": False, "error": "not_found"})
            continue

    if created_events_agg:
        block = _render_list_block(created_events_agg)
        replies.append(INDENT_SECTION + "✅ 일정이 생성되었어요.\n\n" + _indent_block(block, 1))

    if updated_events_agg:
        block = _render_list_block(updated_events_agg)
        replies.append(INDENT_SECTION + "🔧 다음 일정을 수정했어요.\n\n" + _indent_block(block, 1))

    if did_mutation:
        items = gcal_list_events_all(sid, None, None, None, False, False)
        SESSION_LAST_LIST[sid] = [(it.get("id"), it.get("_calendarId") or "primary") for it in items]
        SESSION_LAST_ITEMS[sid] = items
        block = _render_list_block(items)
        replies.append(INDENT_SECTION + "\n 변경 이후 최신 목록입니다.\n\n" + _indent_block(block, 2))
        actions.append({"list": [{"idx": i + 1, **_pack_g(e)} for i, e in enumerate(items)]})

    reply = "\n\n".join(replies) if replies else "완료했습니다."
    reply = _sanitize_llm_reply_text(reply, allow_helper=False)
    return ChatOut(reply=reply, tool_result={"actions": actions})
