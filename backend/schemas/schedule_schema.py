from pydantic import BaseModel
from datetime import datetime

class ScheduleParseRequest(BaseModel):
    text: str

class ScheduleParseResponse(BaseModel):
    title: str
    datetime: datetime