from fastapi import FastAPI
from routes import schedule
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Cors
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# 라우터 등록
app.include_router(schedule.router)