from schemas.schedule_schema import ScheduleInput, ScheduleResponse
from models.schedule import Schedule
from sqlalchemy.orm import Session
from datetime import datetime

def parse_and_save_schedule(payload: ScheduleInput, db: Session) -> ScheduleResponse:
    # TODO: OpenAI API 연동 → GPT로 시간/제목 추출
    dummy_title = "회의"
    dummy_start = datetime.now()
    dummy_end = datetime.now()

    schedule = Schedule(
        title=dummy_title,
        start_time=dummy_start,
        end_time=dummy_end,
    )

    db.add(schedule)
    db.commit()
    db.refresh(schedule)

    return ScheduleResponse(
        title=schedule.title,
        start_time=schedule.start_time,
        end_time=schedule.end_time,
    )
