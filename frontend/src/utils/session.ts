// src/utils/session.ts
export function getSessionId() {
    const key = "ai_schedule_session_id";
    let id = localStorage.getItem(key);
    if (!id) {
        id = crypto.randomUUID();
        localStorage.setItem(key, id);
    }
    return id;
}
