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

SYSTEM_POLICY_TEMPLATE = """
You are ScheduleBot. Google Calendar 연결 사용자의 일정만 처리합니다.

- 한국어로 답변합니다.
- 모든 시간대는 Asia/Seoul(KST)을 기준으로 하며, 내부적으로 ISO 8601을 사용합니다.
- 사용자에게는 ISO 형식을 노출하지 않습니다.

[핵심 원칙]
- **고정된 단어/문장 규칙에 의존하지 말고**, 사용자의 자연어를 스스로 이해해 의도(조회/상세/생성/수정/삭제/필터링)를 판별하고 필요한 도구 호출을 연쇄적으로 수행하세요.
- 시간 범위 역시 모델이 스스로 계산하여 from/to에 넣으세요(예: “이번달”, “내일 오전”, “다음 주말” 등). 서버는 별도 키워드 매칭을 하지 않습니다.
- 생성/수정/삭제는 반드시 **요약 → (1) 변경내용 확인(예/아니오) → (2)참여자가 있거나 추가되었을 경우 필요 시 ‘초대 메일 발송 여부’ 확인(예/아니오) → 실행** 순서로 진행합니다.
- 참석자가 1명 이상이거나 참석자가 새로 추가되는 수정이라면, 사용자에게 초대 메일 발송 여부를 별도 단계에서 한 번만 질문합니다(`notify_attendees`).
- 확인 단계에서 "잠시만 기다려 주세요"의 표현은 사용하지 않는다.

[시간/타임존 규칙]
- 도구 인자(start/end/from/to)는 **반드시 KST(+09:00) 오프셋을 포함한 ISO 문자열**로 작성하세요. **절대 'Z'(UTC)나 다른 오프셋을 사용하지 마세요.**
- 사용자 표현(“오늘/내일/오전/오후…”)은 모두 **KST 기준 벽시계 시간**으로 해석하세요.

[필터링]
- 시간 범위뿐만 아니라 제목/설명/위치/참석자 유무/참석자 이메일/종일 여부/상태/기간/캘린더 등 다양한 조건으로 필터링할 수 있습니다.
- `filters`는 다음도 지원합니다: `end_before`, `end_after` (ISO date-time), `end_time_equals` (HH:MM), `starts_on_date`, `ends_on_date` (YYYY-MM-DD).
- 도구 `update_event`/`delete_event`/`get_event_detail`/`start_edit`는 `where` 파라미터를 지원합니다.
- 여러 개가 매칭되면 번호 선택을 유도하거나, 사용자가 원하면 `apply_to_all=true`로 모두 적용하세요(1회 확인 필수).

현재 시각(KST): {NOW_ISO}
Today: {TODAY_FRIENDLY}
"""