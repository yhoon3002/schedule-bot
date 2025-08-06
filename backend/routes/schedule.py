from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from schemas.schedule_schema import ScheduleInput, ScheduleResponse
from services.schedule_service import parse_and_save_schedule
from database import get_db

router = APIRouter()

@router.post("/parse", response_model=ScheduleResponse)
def parse_schedule(payload: ScheduleInput, db: Session = Depends(get_db)):
    return parse_and_save_schedule(payload, db)
