# routes/schedule_spec.py
# 툴 스펙 / 시스템 프롬프트

ALLOWED_TOOLS = {
    "create_event",
    "list_events",
    "update_event",
    "delete_event",
    "get_event_detail",
    "get_event_detail_by_index",
    "start_edit",
}

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": (
                "Google Calendar 이벤트 생성.\n"
                "- KST 기준.\n"
                "- 종료가 없거나 시작보다 빠르면 시작+1h로 보정.\n"
                "- 참석자가 있고 notify_attendees가 명시되지 않았다면, 확인 단계에서 메일 발송 여부를 묻는다.\n"
                "- confirmed=true 일 때만 실제 생성한다(요약 확인 1회 원칙)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "string", "format": "date-time"},
                    "end": {"type": "string", "format": "date-time"},
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                    "attendees": {"type": "array", "items": {"type": "string"}},
                    "notify_attendees": {
                        "type": "boolean",
                        "description": "true면 참석자 초대메일 발송, false면 발송 안함",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "요약 확인 후 실제 실행하려면 true로 보낸다.",
                    },
                    "session_id": {"type": "string"},
                },
                "required": ["title", "start"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": (
                "사용자 일정 조회. 모델이 자연어를 해석하여 시간 범위와 세부 필터를 설정해 호출한다.\n"
                "- from/to는 ISO 8601 문자열(KST)로 전달.\n"
                "- 공휴일/생일 포함 여부도 제어 가능.\n"
                "- filters로 일정 항목(제목/설명/위치/참석자 유무/참석자 이메일/종일 여부/상태/기간/종료시각/종료날짜 등)을 세밀하게 필터링한다.\n"
                "- 반환은 서버가 번호(1) 스타일로 렌더링한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from": {"type": "string", "format": "date-time"},
                    "to": {"type": "string", "format": "date-time"},
                    "query": {"type": "string"},
                    "include_holidays": {"type": "boolean", "default": False},
                    "include_birthdays": {"type": "boolean", "default": False},
                    "filters": {
                        "type": "object",
                        "description": "세부 필터(모두 선택적)",
                        "properties": {
                            "title_includes": {"type": "array", "items": {"type": "string"}},
                            "title_excludes": {"type": "array", "items": {"type": "string"}},
                            "description_includes": {"type": "array", "items": {"type": "string"}},
                            "description_excludes": {"type": "array", "items": {"type": "string"}},
                            "location_includes": {"type": "array", "items": {"type": "string"}},
                            "location_excludes": {"type": "array", "items": {"type": "string"}},
                            "has_attendees": {"type": "boolean"},
                            "attendee_emails_includes": {"type": "array", "items": {"type": "string"}},
                            "has_location": {"type": "boolean"},
                            "is_all_day": {"type": "boolean"},
                            "min_duration_minutes": {"type": "integer"},
                            "max_duration_minutes": {"type": "integer"},
                            "status": {"type": "string", "description": "confirmed/tentative/cancelled 등"},
                            "calendar_ids_includes": {"type": "array", "items": {"type": "string"}},
                            "end_before": {"type": "string", "format": "date-time"},
                            "end_after": {"type": "string", "format": "date-time"},
                            "end_time_equals": {"type": "string", "description": "HH:MM 형식"},
                            "starts_on_date": {"type": "string", "description": "YYYY-MM-DD"},
                            "ends_on_date": {"type": "string", "description": "YYYY-MM-DD"},
                        },
                        "additionalProperties": False,
                    },
                    "session_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_event",
            "description": (
                "Google Calendar 이벤트 수정. id/인덱스 또는 where(필터)로 대상 선택 가능.\n"
                "- start만 변경되고 end가 없거나 start>=end면 start+1h로 보정.\n"
                "- 참석자 변경 시 notify_attendees가 명시되지 않았다면 확인 단계에서 묻는다.\n"
                "- 여러 개가 매칭되면 번호 선택을 유도하거나 apply_to_all=true로 모두 수정.\n"
                "- confirmed=true 일 때만 실제 수정(요약 확인 1회 원칙)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "index": {"type": "integer", "minimum": 1},
                    "where": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string", "format": "date-time"},
                            "to": {"type": "string", "format": "date-time"},
                            "query": {"type": "string"},
                            "include_holidays": {"type": "boolean"},
                            "include_birthdays": {"type": "boolean"},
                            "filters": {"type": "object"},
                        },
                        "additionalProperties": False,
                    },
                    "apply_to_all": {"type": "boolean"},
                    "patch": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "start": {"type": "string", "format": "date-time"},
                            "end": {"type": "string", "format": "date-time"},
                            "description": {"type": "string"},
                            "location": {"type": "string"},
                            "attendees": {"type": "array", "items": {"type": "string"}},
                        },
                        "additionalProperties": False,
                    },
                    "notify_attendees": {
                        "type": "boolean",
                        "description": "true면 참석자 초대메일 발송, false면 발송 안함",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "요약 확인 후 실제 실행하려면 true로 보낸다.",
                    },
                    "session_id": {"type": "string"},
                },
                "required": ["patch"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_event",
            "description": (
                "이벤트 삭제. indexes/index/ids/id 또는 where(필터) 사용 가능.\n"
                "- where로 여러 개가 매칭되면 번호 선택을 유도하거나 apply_to_all=true로 모두 삭제.\n"
                "- confirmed=true 일 때만 실제 삭제(요약 확인 1회 원칙)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "ids": {"type": "array", "items": {"type": "string"}},
                    "index": {"type": "integer", "minimum": 1},
                    "indexes": {"type": "array", "items": {"type": "integer"}},
                    "where": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string", "format": "date-time"},
                            "to": {"type": "string", "format": "date-time"},
                            "query": {"type": "string"},
                            "include_holidays": {"type": "boolean"},
                            "include_birthdays": {"type": "boolean"},
                            "filters": {"type": "object"},
                        },
                        "additionalProperties": False,
                    },
                    "apply_to_all": {"type": "boolean"},
                    "confirmed": {
                        "type": "boolean",
                        "description": "요약 확인 후 실제 실행하려면 true로 보낸다.",
                    },
                    "session_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_event_detail",
            "description": "id/인덱스 또는 where(필터)로 상세 보기(참석자 포함). 두 개 이상이면 번호 선택 유도.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "index": {"type": "integer", "minimum": 1},
                    "where": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string", "format": "date-time"},
                            "to": {"type": "string", "format": "date-time"},
                            "query": {"type": "string"},
                            "include_holidays": {"type": "boolean"},
                            "include_birthdays": {"type": "boolean"},
                            "filters": {"type": "object"},
                        },
                        "additionalProperties": False,
                    },
                    "session_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_event_detail_by_index",
            "description": "마지막 조회 인덱스(1-base)로 상세 보기.",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "minimum": 1},
                    "session_id": {"type": "string"},
                },
                "required": ["index"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_edit",
            "description": "편집 시작. id/인덱스 또는 where(필터)로 대상 선택. 여러 개면 번호 선택 유도.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "index": {"type": "integer", "minimum": 1},
                    "where": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string", "format": "date-time"},
                            "to": {"type": "string", "format": "date-time"},
                            "query": {"type": "string"},
                            "include_holidays": {"type": "boolean"},
                            "include_birthdays": {"type": "boolean"},
                            "filters": {"type": "object"},
                        },
                        "additionalProperties": False,
                    },
                    "session_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
]

# routes/schedule_openai_v2.py
# 개선된 OpenAI 호출 - 다중 도구 호출 지원

import os, requests, logging, json
from typing import Dict, List, Any, Optional, Tuple
from fastapi import HTTPException
from routes.schedule_spec import TOOLS_SPEC

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE = os.getenv("OPENAI_BASE", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

logger = logging.getLogger(__name__)


class MultiStepToolExecutor:
    """복합 작업을 위한 다단계 도구 실행기"""

    def __init__(self, session_id: str, tool_handler):
        self.session_id = session_id
        self.tool_handler = tool_handler
        self.conversation_history = []
        self.max_iterations = 10  # 무한 루프 방지

    def execute_conversation(self, messages: List[Dict[str, Any]]) -> Tuple[str, Optional[Any]]:
        """
        대화형 방식으로 여러 도구를 순차 실행

        Returns:
            (final_reply, tool_result)
        """
        self.conversation_history = messages.copy()
        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1

            # OpenAI 호출
            response = self._call_openai(self.conversation_history)
            choice = response["choices"][0]
            message = choice["message"]

            # 응답을 대화 히스토리에 추가
            self.conversation_history.append(message)

            tool_calls = message.get("tool_calls", [])

            if not tool_calls:
                # 도구 호출이 없으면 최종 응답
                content = message.get("content", "")
                return self._sanitize_reply(content), None

            # 도구들을 순차적으로 실행
            all_results = []
            has_mutation = False

            for tool_call in tool_calls:
                result = self._execute_single_tool(tool_call)
                all_results.append(result)

                # 변경 작업인지 확인
                tool_name = tool_call["function"]["name"]
                if tool_name in ["create_event", "update_event", "delete_event"]:
                    if result.get("actions", [{}])[0].get("created") or \
                            result.get("actions", [{}])[0].get("updated") or \
                            result.get("actions", [{}])[0].get("deleted"):
                        has_mutation = True

                # 도구 실행 결과를 대화 히스토리에 추가
                self.conversation_history.append({
                    "tool_call_id": tool_call["id"],
                    "role": "tool",
                    "name": tool_call["function"]["name"],
                    "content": json.dumps(result, ensure_ascii=False)
                })

            # 변경이 있었다면 자동으로 목록 새로고침
            if has_mutation:
                self._auto_refresh_list()

            # 확인이 필요한 경우 (need_confirm, need_notify_choice) 즉시 종료
            for result in all_results:
                actions = result.get("actions", [])
                for action in actions:
                    if action.get("need_confirm") or action.get("need_notify_choice") or action.get("need_index"):
                        return self._generate_final_response(), {"actions": self._collect_all_actions(all_results)}

            # 모든 도구가 성공적으로 완료되었는지 확인
            all_completed = all(
                any(action.get("created") or action.get("updated") or action.get("deleted") or action.get(
                    "list") or action.get("detail")
                    for action in result.get("actions", []))
                for result in all_results
            )

            if all_completed:
                return self._generate_final_response(), {"actions": self._collect_all_actions(all_results)}

        # 최대 반복 횟수 초과
        logger.warning(f"Max iterations ({self.max_iterations}) exceeded for session {self.session_id}")
        return "작업이 복잡해서 완료하지 못했습니다. 단계별로 나누어 요청해 주세요.", None

    def _call_openai(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """OpenAI API 호출"""
        if not OPENAI_API_KEY:
            raise HTTPException(500, "OPENAI_API_KEY not set")

        # 디버깅용 로깅
        try:
            last_user = next((m for m in messages[::-1] if m["role"] == "user"), {})
            logger.debug(f"[LLM] req: iteration, user='{last_user.get('content', '')[:80]}...'")
        except Exception:
            pass

        r = requests.post(
            f"{OPENAI_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "temperature": 0.2,
                "messages": messages,
                "tools": TOOLS_SPEC,
                "tool_choice": "auto",
            },
            timeout=45,  # 더 긴 타임아웃
        )

        if not r.ok:
            logger.error(f"OpenAI API error: {r.status_code} {r.text}")
            raise HTTPException(500, "LLM call failed")

        data = r.json()

        # 응답 로깅
        try:
            msg = data.get("choices", [{}])[0].get("message", {})
            tools = msg.get("tool_calls", [])
            logger.debug(f"[LLM] res: tool_calls={len(tools)}, content='{msg.get('content', '')[:80]}...'")
            for i, tc in enumerate(tools, 1):
                fn_name = tc.get("function", {}).get("name")
                logger.debug(f"  tool[{i}]={fn_name}")
        except Exception:
            pass

        return data

    def _execute_single_tool(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """단일 도구 실행"""
        try:
            function_name = tool_call["function"]["name"]
            raw_args = tool_call["function"].get("arguments", "{}")

            # JSON 파싱
            if isinstance(raw_args, str):
                args = json.loads(raw_args)
            else:
                args = raw_args

            # session_id 자동 추가
            args["session_id"] = self.session_id

            # 도구 핸들러 호출
            result = self.tool_handler(function_name, args)

            logger.debug(f"Tool {function_name} executed successfully")
            return result

        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            return {
                "actions": [{
                    "ok": False,
                    "error": str(e)
                }]
            }

    def _auto_refresh_list(self):
        """변경 작업 후 자동으로 목록 새로고침"""
        try:
            refresh_result = self.tool_handler("list_events", {
                "session_id": self.session_id,
                "from": None,
                "to": None
            })
            logger.debug("Auto-refreshed event list after mutation")
        except Exception as e:
            logger.warning(f"Failed to auto-refresh list: {e}")

    def _collect_all_actions(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """모든 결과에서 액션들을 수집"""
        all_actions = []
        for result in results:
            all_actions.extend(result.get("actions", []))
        return all_actions

    def _generate_final_response(self) -> str:
        """최종 응답 생성을 위해 한 번 더 OpenAI 호출"""
        try:
            # 요약 요청 메시지 추가
            summary_messages = self.conversation_history.copy()
            summary_messages.append({
                "role": "user",
                "content": "위 작업들의 결과를 사용자에게 친근하고 자연스럽게 요약해서 알려주세요."
            })

            response = self._call_openai(summary_messages)
            content = response["choices"][0]["message"].get("content", "")
            return self._sanitize_reply(content)

        except Exception as e:
            logger.error(f"Failed to generate final response: {e}")
            return "작업이 완료되었습니다."

    def _sanitize_reply(self, text: str) -> str:
        """응답 텍스트 정제"""
        # 기본적인 정제만 수행
        return text.strip()


def _openai_chat_multi_step(messages: List[Dict[str, Any]], session_id: str, tool_handler) -> Tuple[str, Optional[Any]]:
    """
    다단계 도구 실행을 지원하는 OpenAI 채팅 인터페이스

    Args:
        messages: 대화 히스토리
        session_id: 세션 ID
        tool_handler: 도구 실행 함수 (function_name, args) -> result

    Returns:
        (reply_text, tool_result)
    """
    executor = MultiStepToolExecutor(session_id, tool_handler)
    return executor.execute_conversation(messages)


# 기존 호환성을 위한 래퍼
def _openai_chat(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """기존 단일 호출 방식 (호환성용)"""
    if not OPENAI_API_KEY:
        raise HTTPException(500, "OPENAI_API_KEY not set")

    r = requests.post(
        f"{OPENAI_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENAI_MODEL,
            "temperature": 0.2,
            "messages": messages,
            "tools": TOOLS_SPEC,
            "tool_choice": "auto",
        },
        timeout=30,
    )

    if not r.ok:
        raise HTTPException(500, "LLM call failed")

    return r.json()


# routes/schedule_state_v2.py
# 개선된 상태 관리

from typing import Dict, List, Tuple, Any, Optional
from routes.google_calendar import gcal_list_events_all
import logging

logger = logging.getLogger(__name__)

# 세션별 상태 저장소
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
    """캐시에서 이벤트 찾기 (기존과 동일)"""
    items = get_cached_events(sid, auto_refresh=False)
    for e in items:
        if (e.get("id") == event_id and
                (e.get("_calendarId") or "primary") == (cal_id or "primary")):
            return e
    return None


def _map_index_to_pair(sid: str, idx: int) -> Optional[Tuple[str, str]]:
    """인덱스를 이벤트 ID 쌍으로 변환 (기존과 동일)"""
    pairs = SESSION_LAST_LIST.get(sid, [])
    if 1 <= idx <= len(pairs):
        return pairs[idx - 1]
    return None


def _find_cal_for_id(sid: str, event_id: str, auto_refresh: bool = True) -> Optional[str]:
    """이벤트 ID로 캘린더 ID 찾기 (개선된 버전)"""
    # 먼저 캐시에서 찾기
    pairs = SESSION_LAST_LIST.get(sid, [])
    cal = next((c for (eid, c) in pairs if eid == event_id), None)

    if cal:
        return cal

    if auto_refresh:
        # 캐시 새로고침 후 다시 시도
        refresh_session_cache(sid)
        pairs = SESSION_LAST_LIST.get(sid, [])
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


# routes/schedule_spec_v2.py
# 개선된 시스템 프롬프트

SYSTEM_POLICY_TEMPLATE = """
You are ScheduleBot. Google Calendar 연결 사용자의 일정만 처리합니다.

- 한국어로 답변합니다.
- 모든 시간대는 Asia/Seoul(KST)을 기준으로 하며, 내부적으로 ISO 8601을 사용합니다.
- 사용자에게는 ISO 형식을 노출하지 않습니다.

[핵심 원칙]
- **고정된 단어/문장 규칙에 의존하지 말고**, 사용자의 자연어를 스스로 이해해 의도(조회/상세/생성/수정/삭제/필터링)를 판별하고 필요한 도구 호출을 연쇄적으로 수행하세요.
- 시간 범위 역시 모델이 스스로 계산하여 from/to에 넣으세요(예: "이번달", "내일 오전", "다음 주말" 등). 서버는 별도 키워드 매칭을 하지 않습니다.
- 생성/수정/삭제는 반드시 **요약 → (1) 변경내용 확인(예/아니오) → (2)참여자가 있거나 추가되었을 경우 필요 시 '초대 메일 발송 여부' 확인(예/아니오) → 실행** 순서로 진행합니다.
- 사용자의 일정과 관련된 내용이면 **절대로 자체 판단**을 하지 말고, 그 외의 내용이라면 **자체 판단 및 자체 답변** 가능합니다.

[자연어 필터링 처리 - 핵심]
- **사용자의 모든 자연어 표현을 분석해서 적절한 filters 조합을 생성하세요**:

[응답 규칙]
- **"잠시만 기다려 주세요", "처리 중입니다", "생성 중..." 등의 진행 상황 멘트는 절대 사용하지 마세요**
- **작업이 완료되면 결과만 간결하게 알려주세요**
- **확인이 필요한 경우에만 질문하고, 그 외에는 바로 실행 결과를 보여주세요**

**시간 관련 표현:**
- "오전" → end_before: "{오늘날짜}T12:00:00+09:00"
- "오후" → start_after: "{오늘날짜}T12:00:00+09:00" 
- "새벽" → end_before: "{오늘날짜}T06:00:00+09:00"
- "밤", "저녁" → start_after: "{오늘날짜}T18:00:00+09:00"
- "10시부터 11시까지" → from/to로 정확한 시간 범위 설정

**장소 관련 표현:**
- "회의실에서", "집에서", "카페에서" → location_includes: ["회의실"] 등
- "~에서 하는" → location_includes로 처리

**참석자 관련 표현:**
- "김씨와", "팀장님과", "동료들과" → has_attendees: true 또는 가능하면 attendee_emails_includes
- "혼자", "개인" → has_attendees: false

**내용 관련 표현:**
- "프로젝트 관련", "업무", "개인적인" → description_includes 또는 title_includes
- 구체적 키워드가 있으면 title_includes에 포함

**복합 조건 예시:**
"오전에 회의실에서 하는 프로젝트 미팅 삭제해줘"
→ filters: {
  end_before: "2025-08-27T12:00:00+09:00",
  location_includes: ["회의실"],
  title_includes: ["프로젝트", "미팅"]
}

[강제 규칙]
- **생성/수정/삭제의 `확인 단계`도 반드시 해당 도구를 호출하여 `need_confirm` 또는 `need_index` 액션을 만들어야 합니다. 자연어로만 확인을 묻지 마세요.**
- **사용자가 긍정으로 답하면 직전 단계에서 만든 후보/캐시를 사용해 같은 도구를 `confirmed=true`로 다시 호출하세요.**

[일정 생성/수정 규칙]
- 참석자는 **이메일 주소만** 유효하다.
- '@'가 없거나 한글/이름만 온 경우, **도구를 호출하지 말고** 즉시 정정 요청을 하라.
- '홍길동 <hong@ex.com'처럼 이름+이메일이면 **이메일만 추출**해 사용.
- 사용자가 요청하지 않은 부분에 대해서는 **절대 스스로 판단하지마세요.**

[시간/타임존 규칙]
- 도구 인자(start/end/from/to)는 **반드시 KST(+09:00) 오프셋을 포함한 ISO 문자열**로 작성하세요.
- 사용자 표현("오늘/내일/오전/오후…")은 모두 **KST 기준 벽시계 시간**으로 해석하세요.

현재 시각(KST): {NOW_ISO}
Today: {TODAY_FRIENDLY}
"""