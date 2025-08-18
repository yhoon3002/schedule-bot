# syntax=docker/dockerfile:1

# 1) Build stage
FROM node:20-alpine AS build
WORKDIR /app

# 의존성 설치 (lockfile 사용)
COPY package.json package-lock.json ./
RUN npm ci

# 소스 복사
COPY . .

# Vite 빌드타임 변수 주입 (Render/로컬에서 --build-arg로 넣음)
ARG VITE_API_BASE_URL
ARG VITE_GOOGLE_CLIENT_ID
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_GOOGLE_CLIENT_ID=$VITE_GOOGLE_CLIENT_ID

# 빌드 (dist 생성)
RUN npm run build

# 2) Serve stage (정적 파일 서빙)
FROM nginx:1.27-alpine
# SPA 라우팅을 위한 nginx 설정
COPY ops/nginx.conf /etc/nginx/conf.d/default.conf
# 빌드 산출물 복사
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80