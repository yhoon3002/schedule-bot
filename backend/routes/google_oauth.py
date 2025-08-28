# routes/google_oauth.py
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

TOKENS = {} # 세션ID -> 토큰/프로필 메모리 저장소(프로덕션에선 외부 스토리지 권장)

class CodeIn(BaseModel):
    code: str
    redirect_uri: str
    session_id: str

def _exchange_code(code: str, redirect_uri: str):
    """
    OAuth 인가 코드를 토큰으로 교환한다.

    :param code: Google OAuth 인가 코드
    :type code: str
    :param redirect_uri: OAuth 플로우의 리다이렉트 URI
    :type redirect_uri: str
    :return: 토큰 페이로드(access_token, refresh_token, scope, expires_in)
    :rtype: Dict[str, Any]
    :raises HTTPException: 400 - 교환 실패 / 500 - 환경변수 누락
    """

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
    """
    OpenID Connect userinfo 엔드포인트로 사용자 프로필을 조회한다.

    :param access_token: 유효한 액세스 토큰
    :type access_token: str
    :return: 사용자 프로필 딕셔너리(실패 시 빈 dict)
    :rtype: Dict[str, Any]
    """

    r = requests.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    return r.json() if r.ok else {}

def _refresh(session_id: str):
    """
    세션의 액세스 토큰을 확인하고 만료 시 리프레시한다.

    :param session_id: 세션 식별자
    :type session_id: str
    :return: (access_token, expires_at, scope 등)을 포함한 토큰 딕셔너리
    :rtype: Dict[str, Any]
    :raises HTTPException: 401 - 미연결/리프레시 실패
    """

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

    # 아직 유효하면 그대로 반환
    if tok.get("access_token") and exp - 60 > now:
        return tok

    # refresh_token이 없으면 갱신 불가
    if not has_rt:
        logger.info("[_refresh] sid=%s | token expired and no refresh_token", session_id)
        raise HTTPException(401, "not connected")

    # 토큰 갱긴
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
    """
    구글 캘린더 연결 여부와 프로필 메타데이터를 반환한다.

    :param session_id: 세션 식별자
    :type session_id: str
    :return: {'connected', 'email', 'profile', 'scope'}
    :rtype: Dict[str, Any]
    """

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
    """
    최초 로그인: 코드 교환 -> userinfo 조회 -> TOKENS 저장

    :param body: code, redirect_uri, session_id를 포함
    :type body: CodeIn
    :return: {name, email, avatarUrl}
    :rtype: Dict[str, Optional[str]]
    """

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
    """
    기존 세션에 스코프를 확장/연결한다. (토큰 저장/갱신)

    :param body: code, redirect_uri, session_id를 포함
    :type body: CodeIn
    :return: {'connected': True, 'email': ...}
    :rtype: Dict[str, Any]
    """

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
    """
    현재 액세스 토큰을 revoke하고 세션 토큰 저장소를 비운다.

    :param session_id: 세션 식별자
    :type session_id: str
    :return: {'ok': True}
    :rtype: Dict[str, bool]
    """

    tok = TOKENS.get(session_id)
    if tok and tok.get("access_token"):
        try:
            requests.post("https://oauth2.googleapis.com/revoke",
                          params={"token": tok["access_token"]}, timeout=10)
        except Exception:
            pass
    TOKENS.pop(session_id, None)
    return {"ok": True}
