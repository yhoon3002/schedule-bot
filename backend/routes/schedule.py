from fastapi import APIRouter
from schemas.schedule_schema import ScheduleParseResponse, ScheduleParseRequest
from services.schedule_service import parse_schedule

router = APIRouter()

@router.post("/parse", response_model=ScheduleParseResponse)
def parse_schedule_route(request: ScheduleParseRequest):
    return parse_schedule(request)