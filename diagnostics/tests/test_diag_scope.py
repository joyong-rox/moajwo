"""diag_scope 단위 시험 — 네트워크를 쓰지 않는다 (소켓 연결을 막은 상태에서 돈다)."""

import ast
import email.message
import socket
import urllib.request
from pathlib import Path

import pytest

import diag_scope as ds

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("단위 시험이 네트워크 연결을 시도함")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.mark.parametrize("value", [None, "", "http://127.0.0.1:8086", "http://127.0.0.1:8086/"])
def test_diag_base_accepts_only_the_isolated_stack(value):
    assert ds.diag_base(value) == "http://127.0.0.1:8086"


@pytest.mark.parametrize(
    "value",
    [
        "http://127.0.0.1:8080",  # 운영
        "http://localhost:8080",
        "http://localhost:8086",  # 이름이 달라도 거절 (정확히 같은 hostname만)
        "https://127.0.0.1:8086",
        "http://127.0.0.1:8086.evil.example",
        "http://127.0.0.1:8086@evil.example",
        "http://user:pw@127.0.0.1:8086",
        "http://user@127.0.0.1:8086",
        "http://127.0.0.1",
        "http://127.0.0.1:08086",  # 정규형이 아닌 포트
        "http://[::1]:8086",
        "http://127.0.0.1:8086/api",
        "http://127.0.0.1:8086/?x=1",
        "http://127.0.0.1:8086/#frag",
        "http://127.0.0.1:8086/;p",
        "http://127.0.0.1:abc",
        "ftp://127.0.0.1:8086",
        "127.0.0.1:8086",
    ],
)
def test_diag_base_rejects_other_or_malformed_addresses(value):
    with pytest.raises(ds.ScopeError):
        ds.diag_base(value)


def test_nu_base_is_limited_to_the_local_checker():
    assert ds.nu_base(None) == "http://127.0.0.1:8888"
    assert ds.nu_base("http://127.0.0.1:8888/") == "http://127.0.0.1:8888"
    for bad in ("https://validator.w3.org/nu/", "http://127.0.0.1:8086", "http://localhost:8888", "http://127.0.0.1:8888/?out=json"):
        with pytest.raises(ds.ScopeError):
            ds.nu_base(bad)


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("http://127.0.0.1:8086/api/files?x=1", True),
        ("http://127.0.0.1:8086/", True),
        ("http://127.0.0.1:8080/api/health", False),
        ("http://localhost:8086/", False),
        ("https://127.0.0.1:8086/", False),
        ("http://u@127.0.0.1:8086/", False),
        ("http://127.0.0.1:8086@evil.example/", False),
        ("http://evil.example/", False),
        ("data:text/html,x", False),
        ("not a url", False),
    ],
)
def test_same_origin_compares_scheme_host_and_port_exactly(url, ok):
    assert ds.same_origin(url, "http://127.0.0.1:8086") is ok


def test_check_url_raises_explicitly_outside_scope():
    assert ds.check_url("http://127.0.0.1:8086/api/x", "http://127.0.0.1:8086")
    with pytest.raises(ds.ScopeError):
        ds.check_url("http://127.0.0.1:8080/api/health", "http://127.0.0.1:8086")


def _redirect(target: str):
    h = ds.ScopedRedirectHandler("http://127.0.0.1:8086")
    req = urllib.request.Request("http://127.0.0.1:8086/api/files")
    return h.redirect_request(req, None, 302, "Found", email.message.Message(), target)


def test_redirect_inside_scope_is_followed():
    new = _redirect("/api/other")
    assert new is not None and new.full_url == "http://127.0.0.1:8086/api/other"


@pytest.mark.parametrize("target", ["http://127.0.0.1:8080/api/health", "http://evil.example/", "//evil.example/x", "https://127.0.0.1:8086/"])
def test_redirect_outside_scope_is_refused_before_any_request(target):
    with pytest.raises(ds.ScopeError):
        _redirect(target)


def test_opener_ignores_environment_proxies(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    # 대조: 기본 열기 도구는 환경 프록시를 쓴다
    default = urllib.request.build_opener()
    assert any(isinstance(h, urllib.request.ProxyHandler) and h.proxies for h in default.handlers)
    # 진단 열기 도구: 빈 ProxyHandler가 기본(환경) 프록시 처리기를 대신하므로 프록시를 쓰는 처리기가 없다
    op = ds.opener("http://127.0.0.1:8086")
    assert not any(isinstance(h, urllib.request.ProxyHandler) and h.proxies for h in op.handlers)
    assert any(isinstance(h, ds.ScopedRedirectHandler) for h in op.handlers)
    assert not any(type(h) is urllib.request.HTTPRedirectHandler for h in op.handlers)


def test_browser_proxy_bypasses_only_the_allowed_origin():
    p = ds.browser_proxy("http://127.0.0.1:8086")
    assert p == {"server": ds.DEAD_PROXY, "bypass": "<-loopback>,127.0.0.1:8086"}  # 순서 중요: <-loopback>이 앞


class _Route:
    def __init__(self, url):
        self.request = type("R", (), {"url": url})()
        self.done = None

    def abort(self):
        self.done = "abort"

    def continue_(self):
        self.done = "continue"


def test_route_guard_uses_the_same_scope_as_python_requests():
    blocked: list[str] = []
    guard = ds.route_guard("http://127.0.0.1:8086", blocked)
    ok, prod, ext = _Route("http://127.0.0.1:8086/assets/a.js"), _Route("http://127.0.0.1:8080/api/health"), _Route("https://cdn.example/x.js")
    for r in (ok, prod, ext):
        guard(r)
    assert (ok.done, prod.done, ext.done) == ("continue", "abort", "abort")
    assert blocked == ["http://127.0.0.1:8080/api/health", "https://cdn.example/x.js"]


def test_scripts_do_not_open_urls_directly_or_use_assert_for_guards():
    """진단 스크립트는 urllib.request.urlopen을 직접 쓰지 않고(허용 범위 우회 방지), 실행 보호에 assert를 쓰지 않는다.
    운영 health 전용(monitor.py·health_timing.py)은 별도 열기 도구를 쓰고 diag_scope·diag_common을 import하지 않는다."""
    for f in SCRIPTS.glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "urlopen":
                raise AssertionError(f"{f.name}: urlopen 직접 사용")
            if isinstance(node, ast.Assert):
                raise AssertionError(f"{f.name}:{node.lineno}: assert 사용")
        if f.name in ("monitor.py", "health_timing.py"):
            imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            imports |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
            assert not imports & {"diag_scope", "diag_common"}, f.name
