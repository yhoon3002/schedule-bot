# services/schedule_service.py
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timedelta, timezone

from models.schedule import Schedule
from schemas.schedule_schema import ScheduleCreate, ScheduleUpdate

KST = timezone(timedelta(hours=9))


def _to_list(att: Optional[str]) -> Optional[List[str]]:
    return att.split(",") if att else None

def _to_str(att: Optional[List[str]]) -> Optional[str]:
    return ",".join(att) if att else None

def ensure_times(payload: ScheduleCreate) -> ScheduleCreate:
    data = payload.model_dump()
    start = data.get("start")
    end = data.get("end")
    if start is None:
        start = datetime.now(KST)
        data["start"] = start
    if end is None and start is not None:
        data["end"] = start + timedelta(hours=1)
    return ScheduleCreate(**data)

def create(db: Session, payload: ScheduleCreate) -> Schedule:
    payload = ensure_times(payload)
    ev = Schedule(
        title=payload.title,
        start=payload.start,
        end=payload.end,
        description=payload.description,
        location=payload.location,
        attendees=_to_str(payload.attendees),
    )
    db.add(ev); db.commit(); db.refresh(ev)
    return ev

def upsert(db: Session, payload: ScheduleCreate) -> Tuple[Schedule, bool]:
    payload = ensure_times(payload)
    title, start, end = payload.title, payload.start, payload.end

    ev = db.query(Schedule).filter(
        Schedule.title == title,
        Schedule.start == start,
        Schedule.end == end,
    ).first()
    if ev:
        if payload.description is not None: ev.description = payload.description
        if payload.location is not None: ev.location = payload.location
        if payload.attendees is not None: ev.attendees = _to_str(payload.attendees)
        db.commit(); db.refresh(ev)
        return ev, False

    # 같은 날 동일 제목이 이미 있으면 가장 최근 것을 갱신
    day_start_s = start.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_s = start.replace(hour=23, minute=59, second=59, microsecond=999999)
    day_start_e = end.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_e = end.replace(hour=23, minute=59, second=59, microsecond=999999)

    ev = db.query(Schedule).filter(
        Schedule.title == title,
        Schedule.start >= day_start_s, Schedule.start <= day_end_s,
        Schedule.end   >= day_start_e, Schedule.end   <= day_end_e,
    ).order_by(Schedule.start.desc()).first()

    if ev:
        ev.start = start
        ev.end = end
        if payload.description is not None: ev.description = payload.description
        if payload.location is not None: ev.location = payload.location
        if payload.attendees is not None: ev.attendees = _to_str(payload.attendees)
        db.commit(); db.refresh(ev)
        return ev, False

    return create(db, payload), True

def upsert_many(db: Session, items: List[Dict[str, Any]]) -> List[Schedule]:
    results: List[Schedule] = []
    for it in items:
        ev, _ = upsert(db, ScheduleCreate(**it))
        results.append(ev)
    return results

def get_list(
    db: Session,
    q: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> List[Schedule]:
    query = db.query(Schedule)
    if date_from: query = query.filter(Schedule.start >= date_from)
    if date_to:   query = query.filter(Schedule.start <= date_to)
    if q:
        like = f"%{q}%"
        query = query.filter((Schedule.title.like(like)) | (Schedule.description.like(like)))
    return query.order_by(Schedule.start.asc()).all()

def get_overlapping(db: Session, date_from: datetime, date_to: datetime) -> List[Schedule]:
    return (
        db.query(Schedule)
        .filter(
            Schedule.start <= date_to,
            or_(Schedule.end == None, Schedule.end >= date_from),
        )
        .order_by(Schedule.start.asc())
        .all()
    )

def get(db: Session, event_id: int) -> Optional[Schedule]:
    return db.query(Schedule).get(event_id)

def get_many(db: Session, ids: List[int]) -> List[Schedule]:
    if not ids: return []
    return db.query(Schedule).filter(Schedule.id.in_(ids)).order_by(Schedule.start.asc()).all()

def update(db: Session, event_id: int, patch: ScheduleUpdate) -> Schedule:
    ev = get(db, event_id)
    if not ev: raise ValueError("NOT_FOUND")
    data = patch.model_dump(exclude_unset=True)
    if "attendees" in data:
        data["attendees"] = _to_str(data["attendees"])
    for k, v in data.items():
        setattr(ev, k, v)
    db.commit(); db.refresh(ev)
    return ev

def update_many(db: Session, items: List[Dict[str, Any]]) -> List[Schedule]:
    results: List[Schedule] = []
    for it in items:
        ev_id = int(it["id"])
        patch = ScheduleUpdate(**it["patch"])
        ev = update(db, ev_id, patch)
        results.append(ev)
    return results

def delete(db: Session, event_id: int) -> None:
    ev = get(db, event_id)
    if not ev: raise ValueError("NOT_FOUND")
    db.delete(ev); db.commit()

def delete_many(db: Session, ids: List[int]) -> int:
    if not ids: return 0
    deleted = db.query(Schedule).filter(Schedule.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return deleted

def delete_all(db: Session) -> int:
    rows = db.query(Schedule).delete()
    db.commit()
    return rows

def human_line_with_times(e: Schedule, tz: timezone = KST) -> str:
    st = e.start.astimezone(tz) if e.start.tzinfo else e.start.replace(tzinfo=tz)
    ed = e.end.astimezone(tz) if e.end and e.end.tzinfo else (e.end.replace(tzinfo=tz) if e.end else None)
    s_date = st.strftime("%Y-%m-%d (%a) %H:%M")
    e_date = ed.strftime("%Y-%m-%d (%a) %H:%M") if ed else "없음"
    return f"{s_date} ~ {e_date} · {e.title}"
