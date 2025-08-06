from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes.schedule import router as schedule_router
from database import Base, engine

Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite 개발용 포트
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(schedule_router, prefix="/schedule", tags=["Schedule"])
