import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.schedule import router as schedule_router
from routes.google_oauth import router as google_auth_router
from routes.google_calendar import router as google_calendar_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        os.getenv("WEB_ORIGIN", ""),
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(google_auth_router)
app.include_router(google_calendar_router)
app.include_router(schedule_router)


@app.get("/health")
def health():
    return {"ok": True}
