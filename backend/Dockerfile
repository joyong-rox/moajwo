FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MOAJWO_DATA_DIR=/app/data

WORKDIR /app

COPY requirements.txt ./
# 2026-10 eMudhra 인증서 사고 대응 (docs/SECURITY-CA-20261026.md):
# - 기반 이미지의 pip(24.0)는 손상된 중간 인증서를 믿는 옛 certifi를 품고 있어, 의존성을 받기 전에 수정판 pip 26.2.2를 먼저
#   설치한다. 그 휠은 PyPI 파일 서버의 고정 주소에서 받되 pip가 URL의 sha256과 받은 내용을 대조하므로, 전송 구간이 가로채여도
#   다른 내용은 설치되지 않는다 (해시는 2026-10-26 PyPI JSON에서 확인).
# - OS 신뢰 저장소(Debian ca-certificates 보안 갱신)를 최신으로 올린다. 다른 패키지는 올리지 않는다.
# - 설치한 pip·pip 내장 certifi·python 판을 빌드 기록에 남긴다.
# 설치가 끝나면 설치 도구(pip·setuptools·wheel)는 실행 이미지에서 뺀다 — 앱이 쓰지 않고, 기반 이미지에 든 판의
# 알려진 취약점(진단 보고 M-03)이 실행 이미지에 남지 않게. 의존성을 바꾸려면 이미지를 다시 만든다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && dpkg-query -W -f='ca-certificates ${Version}\n' ca-certificates \
    && python -m pip install --no-cache-dir "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70" \
    && python --version && python -m pip --version \
    && python -c "from pip._vendor import certifi; print('pip-vendored certifi', certifi.__version__)" \
    && pip install -r requirements.txt \
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
