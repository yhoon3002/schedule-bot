from schemas.schedule_schema import ScheduleParseRequest, ScheduleParseResponse
from datetime import datetime

def parse_schedule(request: ScheduleParseRequest) -> ScheduleParseResponse:
    # GPT 대신 하드코딩 로직
    if "회의" in request.text:
        return ScheduleParseResponse(
            title="영훈이와 회의",
            datetime=datetime(2025, 8, 14, 15, 0)
        )

    return ScheduleParseResponse(
        title="일정 없음",
        datetime=datetime.now()
    )