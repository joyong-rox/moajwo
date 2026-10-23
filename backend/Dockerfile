FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MOAJWO_DATA_DIR=/app/data

WORKDIR /app

COPY requirements.txt ./
# 설치가 끝나면 설치 도구(pip·setuptools·wheel)는 실행 이미지에서 뺀다 — 앱이 쓰지 않고, 기반 이미지에 든 판의
# 알려진 취약점(진단 보고 M-03)이 실행 이미지에 남지 않게. 의존성을 바꾸려면 이미지를 다시 만든다.
RUN pip install -r requirements.txt \
    && pip uninstall -y pip setuptools wheel

COPY . .

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data \
    && chown app:app /app/data
USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

# 기동 시 마이그레이션을 최신으로 맞춘 뒤 서버를 띄운다.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
