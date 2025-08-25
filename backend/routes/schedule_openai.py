# OpenAI 호출

import os, requests
from fastapi import HTTPException
from routes.schedule_spec import TOOLS_SPEC

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE = os.getenv("OPENAI_BASE", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

def _openai_chat(messages):
    if not OPENAI_API_KEY:
        raise HTTPException(500, "OPENAI_API_KEY not set")
    r = requests.post(
        f"{OPENAI_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENAI_MODEL,
            "temperature": 0.2,
            "messages": messages,
            "tools": TOOLS_SPEC,
            "tool_choice": "auto",
        },
        timeout=30,
    )
    if not r.ok:
        raise HTTPException(500, "LLM call failed")
    return r.json()