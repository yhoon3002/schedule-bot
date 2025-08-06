from sqlalchemy import Column, Integer, String, DateTime
from database import Base

class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    start_time = Column(DateTime)
    end_time = Column(DateTime)
