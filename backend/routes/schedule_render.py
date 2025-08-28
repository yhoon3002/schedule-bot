# routes/schedule_render.py
# 렌더/ 서식

def _pack_g(e: dict) -> dict:
    """
    이벤트 객체에서 사용자에게 필요한 최소 필드를 추려서 패킹한다.

    :param e: Google 이벤트 객체
    :type e: dict
    :return: {id, calendarId, title, start, end, description, location, attendees, status}
    :rtype: dict
    """

    if not e:
        return {}

    start = e.get("start", {})
    end = e.get("end", {})
    return {
        "id": e.get("id"),
        "calendarId": e.get("_calendarId"),
        "title": e.get("summary") or "(제목 없음)",
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "description": e.get("description"),
        "location": e.get("location"),
        "attendees": [a.get("email") for a in (e.get("attendees") or []) if a.get("email")],
        "status": e.get("status"),
    }