from pydantic import BaseModel
from datetime import datetime

class ScheduleInput(BaseModel):
    text: str

class ScheduleResponse(BaseModel):
    title: str
    start_time: datetime
    end_time: datetime
