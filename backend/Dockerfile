# 기반 이미지는 판·배포판·digest로 고정한다 (2026-10-28, docs/DEPLOY.md 11장). 떠도는 태그(python:3.11-slim)는 빌드하는 PC에
# 남아 있던 옛 이미지를 레지스트리에 묻지 않고 그대로 쓸 수 있어, 2026-10-28 로컬 반영에서 CI가 검증한 기반과 다른 판이 쓰였다.
# python:3.11.18-slim-trixie = Python 3.11.18 · Debian 13 trixie. 아래 digest는 이 태그의 멀티아키 색인이고, 그 linux/amd64
# 매니페스트는 sha256:0451b9eeed3e5a9523d8958cc33262eb5843c7af3114c50f45d99f56e2b883ac (2026-10-26T22:50:44Z 생성 — 2026-10-27 CI
# 이미지 검사의 기반과 같은 매니페스트, 2026-10-28 Docker Hub 조회). 빌드 중 apt·pip로 받는 것은 기반 고정과 별개라, 반영 전에
# 완성 이미지 목록을 검증한 목록(deploy/expected-images/)과 대조한다. 기반을 바꿀 때는 digest·이 설명·검증 목록을 같은 PR에서
# 함께 바꾸고 CI 검증을 다시 한다. 자동 갱신은 두지 않는다.
FROM python:3.11.18-slim-trixie@sha256:82209229d3fda30d71e603ec95bb4bea0476635ba6df3df2e32284e036654f51

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MOAJWO_DATA_DIR=/app/data

WORKDIR /app

COPY requirements.txt requirements-linux-hashes.txt ./
# 2026-10 eMudhra 인증서 사고 대응 (docs/SECURITY-CA-20261026.md):
# - 기반 이미지의 pip(24.0)는 손상된 중간 인증서를 믿는 옛 certifi를 품고 있어, 의존성을 받기 전에 수정판 pip 26.2.2를 먼저
#   설치한다. 그 휠은 PyPI 파일 서버의 고정 주소에서 받되 pip가 URL의 sha256과 받은 내용을 대조하므로, 전송 구간이 가로채여도
#   다른 내용은 설치되지 않는다 (해시는 2026-10-26 PyPI JSON에서 확인).
#   의존성도 같은 고정 목록에 linux x86_64/CPython 3.11 휠의 sha256을 붙인 requirements-linux-hashes.txt로 --require-hashes 설치한다
#   (해시 목록은 진단 워크플로 hashes.yml이 만듦; Windows 개발 설치는 requirements.txt 그대로).
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
    && pip install --require-hashes -r requirements-linux-hashes.txt \
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
