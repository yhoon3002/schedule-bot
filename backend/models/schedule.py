# models/schedule.py
from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()

class Schedule(Base):
    __tablename__ = "schedules"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    start = Column(DateTime, nullable=False, index=True)
    end = Column(DateTime, nullable=True)
    description = Column(Text, nullable=True)
    location = Column(String(255), nullable=True)
    attendees = Column(Text, nullable=True)  # comma-separated
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
