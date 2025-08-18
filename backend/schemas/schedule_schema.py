# schemas/schedule_schema.py
from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional, List
from datetime import datetime

class ScheduleCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    description: Optional[str] = None
    location: Optional[str] = None
    attendees: Optional[List[str]] = None

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v, info):
        start = info.data.get("start")
        if v and start and v < start:
            raise ValueError("end must be after start")
        return v

class ScheduleUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    description: Optional[str] = None
    location: Optional[str] = None
    attendees: Optional[List[str]] = None

class ScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    start: datetime
    end: Optional[datetime]
    description: Optional[str]
    location: Optional[str]
    attendees: Optional[List[str]]
