FROM node:24-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/index.html frontend/vite.config.ts frontend/tsconfig*.json ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN npm run build

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MOAJWO_DATA_DIR=/app/preview-data \
    MOAJWO_STATIC_DIR=/app/static \
    MOAJWO_COOKIE_SECURE=true
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/app ./app
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY deploy/render/preview_app.py deploy/render/bootstrap.py deploy/render/start.sh ./
COPY --from=frontend /build/dist ./static
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/preview-data \
    && chown app:app /app/preview-data
USER app
EXPOSE 10000
CMD ["sh", "/app/start.sh"]
