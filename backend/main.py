# main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import Base, engine

# 데이터베이스 테이블 생성
Base.metadata.create_all(bind=engine)

# FastAPI 앱 생성
app = FastAPI()

# CORS 설정 (Vue 프론트엔드에서 접근 허용)
origins = ["http://localhost:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 기본 엔드포인트
@app.get("/")
def root():
    return {"message": "Hello from FastAPI"}
