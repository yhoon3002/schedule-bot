# routes/schedule_openai.py
# OpenAI 호출 - 다중 도구 호출 지원

import os, requests, logging, json
from typing import Dict, List, Any, Optional, Tuple
from fastapi import HTTPException
from routes.schedule_spec import TOOLS_SPEC

###############################################
# OPENAI_API_KEY : OPENAPI API 인증키          #
# OPENAI_BASE : OPENAI API 엔드포인트 기본 URL   #
# OPENAI_MODEL : 사용할 모델 이름                #
##############################################
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE = os.getenv("OPENAI_BASE", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# 전역 로거 레벨을 DEBUG로 설정
logging.getLogger().setLevel(logging.DEBUG)

class MultiStepToolExecutor:
    """
    복합 작업을 위한 '다단계 도구 실행기' 클래스.

    모델이 필요하다고 판단한 '도구(function tool)'들을 실제로 호출하고,
    그 결과를 다시 대화 히스토리에 추가하여 연쇄적인 처리(예: A 삭제 -> B 생성)를 한 번의 사용자 요청으로 수행할 수 있게 도움

    :param session_id: 세션 식별자(사용자/세션별 상태 식별에 사용)
    :type session_id: str
    :param tool_handler: 실제 도구를 실행하는 콜백 함수. (function_name, args) -> dict
    :type tool_handler: Callable[[str, Dict[str, Any]], Dict[str, Any]]
    """

    def __init__(self, session_id: str, tool_handler):
        """
        다단계 도구 실행기를 초기화합니다.

        :param session_id: 세션 식별자(사용자별 상태 전파용)
        :type session_id: str
        :param tool_handler: 도구 실행 콜백 함수. (function_name, args) -> dict 형태로 결과 반환
        :type tool_handler: Callable[[str, Dict[str, Any]], Dict[str, Any]]
        """
        self.session_id = session_id
        self.tool_handler = tool_handler
        self.conversation_history = [] # LLM에게 보낼 대화 히스토리
        self.max_iterations = 10  # 무한 루프 방지

    def execute_conversation(self, messages: List[Dict[str, Any]]) -> Tuple[str, Optional[Any]]:
        """
        대화형 방식으로 여러 도구를 순차 실행 :
        1) 현재 히스토리를 LLM에 전달하여 응답을 받음
        2) 응답에 포함된 tool_calls를 실제로 실행
        3) 결과를 히스토리에 'tool' 역할로 추가
        4) 사용자 확인 필요(need_confirm 등) 시 그 지점에서 종료 & 요약 응답 생성
        5) 추가 확인이 없다면 다음 루프로 넘어가 연쇄 도구 호출을 이어감

        Returns:
            (final_reply, tool_result)
        """
        self.conversation_history = messages.copy()
        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1

            response = self._call_openai(self.conversation_history)
            choice = response["choices"][0]
            message = choice["message"]

            self.conversation_history.append(message)

            tool_calls = message.get("tool_calls", [])

            if not tool_calls:
                content = message.get("content", "")
                return self._sanitize_reply(content), None

            all_results = []
            has_mutation = False
            need_user_confirmation = False

            for tool_call in tool_calls:
                result = self._execute_single_tool(tool_call)
                all_results.append(result)

                # 변경 작업인지 확인
                tool_name = tool_call["function"]["name"]
                if tool_name in ["create_event", "update_event", "delete_event"]:
                    actions = result.get("actions", [])
                    for action in actions:
                        if action.get("created") or action.get("updated") or action.get("deleted"):
                            has_mutation = True
                        # 사용자 확인이 필요한 상황인지 체크
                        if (action.get("need_confirm") or
                                action.get("need_notify_choice") or
                                action.get("need_index")):
                            need_user_confirmation = True

                self.conversation_history.append({
                    "tool_call_id": tool_call["id"],
                    "role": "tool",
                    "name": tool_call["function"]["name"],
                    "content": json.dumps(result, ensure_ascii=False)
                })

            # 핵심 수정: 사용자 확인이 필요하거나 변경이 완료된 경우 즉시 종료
            if need_user_confirmation:
                return self._generate_final_response(), {"actions": self._collect_all_actions(all_results)}

            continue

            ##### Dead Code #####
            # 확인이 필요한 경우 즉시 종료
            # for result in all_results:
            #     actions = result.get("actions", [])
            #     for action in actions:
            #         if (action.get("need_confirm") or
            #                 action.get("need_notify_choice") or
            #                 action.get("need_index")):
            #             return self._generate_final_response(), {"actions": self._collect_all_actions(all_results)}
            #
            # # 모든 도구가 성공적으로 완료되었는지 확인
            # all_completed = True
            # for result in all_results:
            #     actions = result.get("actions", [])
            #     has_success = any(
            #         action.get("created") or action.get("updated") or
            #         action.get("deleted") or action.get("list") or
            #         action.get("detail") for action in actions
            #     )
            #     if not has_success and not any(action.get("ok") is False for action in actions):
            #         all_completed = False
            #         break
            #
            # if all_completed:
            #     return self._generate_final_response(), {"actions": self._collect_all_actions(all_results)}

        # 최대 반복 횟수 초과
        logging.warning(f"Max iterations ({self.max_iterations}) exceeded for session {self.session_id}")
        return "작업이 복잡해서 완료하지 못했습니다. 단계별로 나누어 요청해 주세요.", None

    def _call_openai(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """OpenAI API 호출"""
        if not OPENAI_API_KEY:
            raise HTTPException(500, "OPENAI_API_KEY not set")

        # 디버깅용 로깅
        try:
            last_user = next((m for m in messages[::-1] if m["role"] == "user"), {})

            logging.debug(
                f"[LLM] req: iteration={len([m for m in messages if m.get('role') == 'assistant'])}, user='{last_user.get('content', '')[:]}...'")
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
            timeout=45,
        )

        if not r.ok:
            logging.error(f"OpenAI API error: {r.status_code} {r.text}")
            raise HTTPException(500, "LLM call failed")

        data = r.json()

        # 응답 로깅
        try:
            msg = data.get("choices", [{}])[0].get("message", {})
            tools = msg.get("tool_calls", [])
            logging.debug(f"[LLM] res: tool_calls={len(tools)}, content='{msg.get('content', '')[:80]}...'")
            for i, tc in enumerate(tools, 1):
                fn_name = tc.get("function", {}).get("name")
                logging.debug(f"  tool[{i}]={fn_name}")
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

            logging.debug(f"Tool {function_name} executed successfully")
            return result

        except Exception as e:
            logging.error(f"Tool execution error: {e}")
            return {
                "actions": [{
                    "ok": False,
                    "error": str(e)
                }]
            }

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
            logging.error(f"Failed to generate final response: {e}")
            return "작업이 완료되었습니다."

    def _sanitize_reply(self, text: str) -> str:
        """응답 텍스트 정제"""
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


def _openai_chat(messages):
    """
    기존 단일 호출 방식 (호환성용)
    """
    if not OPENAI_API_KEY:
        raise HTTPException(500, "OPENAI_API_KEY not set")

    # 입력 메시지 요약 로깅(앞부분만)
    try:
        import json as _json
        _first_user = next((m for m in messages[::-1] if m["role"] == "user"), {})
        logging.debug("[LLM] req: model=%s tool_choice=auto user='%s...'", OPENAI_MODEL,
                      (_first_user.get("content", "")[:80].replace("\n", " ")))
    except Exception:
        pass

    # tool_choice='auto' -> 모델이 툴 호출 여부/순서를 스스로 결정
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
    data = r.json()

    try:
        msg = (data.get("choices", [{}])[0].get("message") or {})
        tools = msg.get("tool_calls") or []
        logging.debug("[LLM] res: tool_calls=%d content='%s...'", len(tools),
                      (msg.get("content", "") or "")[:].replace("\n", " "))
        if tools:
            for i, tc in enumerate(tools, 1):
                fn = tc.get("function", {}).get("name")
                args = tc.get("function", {}).get("arguments")
                logging.debug("  tool[%d]=%s args=%s", i, fn, str(args)[:140])
    except Exception:
        pass
    return data