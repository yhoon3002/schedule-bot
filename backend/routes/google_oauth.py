import os, time, logging, requests
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/google", tags=["google-auth"])

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

logger.info("[GoogleOAuth] BACKEND CLIENT_ID=%s****** (loaded=%s)",
            GOOGLE_CLIENT_ID[:6], bool(GOOGLE_CLIENT_ID))

TOKENS = {}  # session_id -> token bundle

class CodeIn(BaseModel):
    code: str
    redirect_uri: str   # 'postmessage'
    session_id: str

def _exchange_code(code: str, redirect_uri: str):
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(500, "GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET not set")
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    if r.status_code != 200:
        logger.error("[GoogleOAuth] Token exchange failed %s | client_id=%s****** redirect_uri=%s | body=%s",
                     r.status_code, GOOGLE_CLIENT_ID[:6], redirect_uri, r.text)
        raise HTTPException(400, f"token exchange failed: {r.text}")
    return r.json()

def _userinfo(access_token: str):
    r = requests.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    return r.json() if r.ok else {}

def _refresh(session_id: str):
    tok = TOKENS.get(session_id)
    if not tok:
        logger.info("[_refresh] sid=%s | no token", session_id)
        raise HTTPException(401, "not connected")

    now = time.time()
    exp = tok.get("expires_at", 0)
    has_rt = bool(tok.get("refresh_token"))
    logger.info("[_refresh] sid=%s | exp_in=%s has_rt=%s",
                session_id, int(tok.get("expires_at", 0) - time.time()),
                bool(tok.get("refresh_token")))

    # 아직 유효하면 그대로 사용
    if tok.get("access_token") and exp - 60 > now:
        return tok

    if not has_rt:
        logger.info("[_refresh] sid=%s | token expired and no refresh_token", session_id)
        raise HTTPException(401, "not connected")

    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
        },
        timeout=20,
    )
    if r.status_code != 200:
        logger.error("[GoogleOAuth:_refresh] sid=%s | refresh failed: %s", session_id, r.text)
        raise HTTPException(401, f"refresh failed: {r.text}")

    data = r.json()
    tok["access_token"] = data["access_token"]
    tok["expires_at"] = time.time() + data.get("expires_in", 3600)
    TOKENS[session_id] = tok
    return tok

@router.get("/status")
def status(session_id: str = Query(...)):
    tok = TOKENS.get(session_id)
    scope = tok.get("scope") if tok else ""
    connected = bool(tok and "https://www.googleapis.com/auth/calendar" in (scope or ""))
    logger.info("[GoogleOAuth:status] sid=%s | has_tok=%s connected=%s scope=%s",
                session_id, bool(tok), connected, scope)
    return {
        "connected": connected,
        "email": tok.get("email") if tok else None,
        "profile": {
            "name": tok.get("name") if tok else None,
            "avatarUrl": tok.get("picture") if tok else None
        } if tok else None,
        "scope": scope,
    }

@router.post("/login")
def login(body: CodeIn):
    data = _exchange_code(body.code, body.redirect_uri)
    access = data["access_token"]
    profile = _userinfo(access)
    TOKENS[body.session_id] = {
        "access_token": access,
        "refresh_token": data.get("refresh_token"),
        "expires_at": time.time() + data.get("expires_in", 3600),
        "scope": data.get("scope", ""),
        "email": profile.get("email"),
        "name": profile.get("name"),
        "picture": profile.get("picture"),
    }
    logger.info("[GoogleOAuth:login] sid=%s | scope=%s", body.session_id, data.get("scope"))
    return {"name": profile.get("name"), "email": profile.get("email"), "avatarUrl": profile.get("picture")}

@router.post("/connect")
def connect(body: CodeIn):
    data = _exchange_code(body.code, body.redirect_uri)
    access = data["access_token"]
    tok = TOKENS.get(body.session_id, {})
    tok.update({
        "access_token": access,
        "refresh_token": data.get("refresh_token", tok.get("refresh_token")),
        "expires_at": time.time() + data.get("expires_in", 3600),
        "scope": data.get("scope", tok.get("scope", "")),
    })
    profile = _userinfo(access)
    tok["email"] = tok.get("email") or profile.get("email")
    tok["name"] = tok.get("name") or profile.get("name")
    tok["picture"] = tok.get("picture") or profile.get("picture")
    TOKENS[body.session_id] = tok
    logger.info("[GoogleOAuth:connect] sid=%s | scope=%s", body.session_id, tok.get("scope"))
    return {"connected": True, "email": tok.get("email")}

@router.post("/disconnect")
def disconnect(session_id: str = Query(...)):
    tok = TOKENS.get(session_id)
    if tok and tok.get("access_token"):
        try:
            requests.post("https://oauth2.googleapis.com/revoke",
                          params={"token": tok["access_token"]}, timeout=10)
        except Exception:
            pass
    TOKENS.pop(session_id, None)
    return {"ok": True}
