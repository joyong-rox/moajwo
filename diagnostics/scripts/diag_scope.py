"""진단 도구가 요청을 보낼 수 있는 범위 — 한 곳에서 정하고 Python HTTP 요청과 브라우저가 함께 쓴다.

- 진단 대상: http://127.0.0.1:8086 (진단 격리 스택 moajwo-diag의 frontend). 시드·업로드·화면 검사 등 진단 요청은 여기에만.
- 표준 검사기: http://127.0.0.1:8888 (로컬 Nu Html Checker). 화면 HTML은 여기에만 보낸다.
- 운영(8080)은 어느 쪽에도 허용하지 않는다. 운영 health 감시는 monitor.py·health_timing.py의 별도 경로다.

주소는 문자열 접두어가 아니라 파싱해서 scheme·hostname·port를 정확히 비교하고, 사용자 정보·경로·쿼리·조각이 붙은
기본 주소는 거절한다. 실행 보호는 assert(최적화 실행 때 사라짐)가 아니라 ScopeError로 막는다.
표준 라이브러리만 쓴다 (단위 시험이 네트워크 없이 돈다).
"""

import urllib.request
from urllib.parse import urljoin, urlsplit

DIAG_TARGET = ("http", "127.0.0.1", 8086)
NU_TARGET = ("http", "127.0.0.1", 8888)
DEAD_PROXY = "http://127.0.0.1:9"  # 아무것도 듣지 않는 주소 — 허용 범위 밖 브라우저 요청은 여기로 보내져 실패한다


class ScopeError(RuntimeError):
    """허용 범위 밖 주소·요청."""


def _split(url: str):
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError as e:  # 숫자가 아닌 포트 등
        raise ScopeError(f"주소를 해석할 수 없음: {url!r} ({e})") from e
    return p, port


def base_url(value: str | None, target: tuple[str, str, int], what: str) -> str:
    """기본 주소를 검사해 'scheme://host:port' 형태로 돌려준다. 비어 있으면 허용 주소를 쓴다."""
    scheme, host, port = target
    canonical = f"{scheme}://{host}:{port}"
    if value is None or value == "":
        return canonical
    p, got_port = _split(value)
    if p.username is not None or p.password is not None or "@" in p.netloc:
        raise ScopeError(f"{what} 주소에 사용자 정보가 있음: 거절")
    if (p.scheme, p.hostname, got_port) != target or p.netloc != f"{host}:{port}":
        raise ScopeError(f"{what} 주소는 {canonical}만 허용: {value!r} 거절")
    if p.path not in ("", "/") or p.query or p.fragment or ";" in value:
        raise ScopeError(f"{what} 기본 주소에 경로·쿼리·조각이 있음: {value!r} 거절")
    return canonical


def diag_base(value: str | None) -> str:
    return base_url(value, DIAG_TARGET, "진단 대상(DIAG_URL)")


def nu_base(value: str | None) -> str:
    return base_url(value, NU_TARGET, "표준 검사기(NU_URL)")


def same_origin(url: str, base: str) -> bool:
    """요청 주소가 기본 주소와 같은 scheme·hostname·port이고 사용자 정보가 없는가 (경로·쿼리는 자유)."""
    try:
        p, port = _split(url)
        b, bport = _split(base)
    except ScopeError:
        return False
    if p.username is not None or p.password is not None or "@" in p.netloc:
        return False
    return (p.scheme, p.hostname, port) == (b.scheme, b.hostname, bport)


def check_url(url: str, base: str) -> str:
    if not same_origin(url, base):
        raise ScopeError(f"허용 범위({base}) 밖 요청: {url[:120]!r}")
    return url


class ScopedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """리다이렉트도 허용 범위 안에서만 따라간다 (밖이면 요청을 보내기 전에 ScopeError)."""

    def __init__(self, base: str) -> None:
        super().__init__()
        self.base = base

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201 — urllib 시그니처
        target = urljoin(req.full_url, newurl)
        if not same_origin(target, self.base):
            raise ScopeError(f"허용 범위({self.base}) 밖으로 리다이렉트: {target[:120]!r}")
        return super().redirect_request(req, fp, code, msg, headers, target)


def opener(base: str, *handlers: urllib.request.BaseHandler) -> urllib.request.OpenerDirector:
    """환경 프록시를 쓰지 않고(ProxyHandler({})), 리다이렉트를 허용 범위로 제한한 urllib 열기 도구."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), ScopedRedirectHandler(base), *handlers)


def browser_proxy(base: str) -> dict:
    """Playwright launch·launch_persistent_context의 proxy 인자.

    허용 주소만 프록시를 거치지 않고(bypass), 나머지는 아무것도 듣지 않는 프록시로 보내 실패시킨다.
    '<-loopback>'(루프백 자동 예외 끄기)을 **맨 앞에** 둔다: 다른 루프백 주소(운영 8080 포함)도 프록시로 보내되 허용 주소만
    직접 연결한다. Playwright가 뒤에 덧붙이는 '<-loopback>'에 맡기면 앞의 허용 규칙까지 무력화된다
    (Edge 155에서 루프백 임시 서버로 확인 — scope_selftest.py). 그래서 page.route가 잡지 못하는 리다이렉트 이후 요청도
    허용 주소 밖이면 나가지 않는다.
    """
    b, port = _split(base)
    return {"server": DEAD_PROXY, "bypass": f"<-loopback>,{b.hostname}:{port}"}


def route_guard(base: str, blocked: list[str]):
    """page/context.route('**/*', ...) 처리기: 첫 요청이 허용 범위 밖이면 막고 기록한다 (리다이렉트 이후는 browser_proxy가 막음)."""

    def handler(route) -> None:  # noqa: ANN001 — Playwright Route
        url = route.request.url
        if not same_origin(url, base):
            blocked.append(url[:120])
            route.abort()
            return
        route.continue_()

    return handler
