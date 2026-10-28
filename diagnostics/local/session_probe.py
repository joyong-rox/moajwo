"""S-03 실제 경로: 로그인 쿠키 속성(값은 기록하지 않음), 교차 출처 응답 헤더, 만료 세션, 승인 대기 로그인.
격리 DB의 세션 만료 시각만 과거로 바꾼다 (진단용 격리 자료). 사용법: session_probe.py <출력 json> <ISO DB 경로>"""
import http.client
import json
import sqlite3
import sys

from diag_common import USERS, creds

out, iso_db = sys.argv[1], sys.argv[2]
rec = {}
def req(method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", 8086, timeout=30)
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"} if body is not None else {}
    h.update(headers or {})
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    return r.status, r.getheaders(), r.read()
s, h, _ = req("POST", "/api/auth/login", {"username": USERS["user1"], "password": creds()["user1"]})
cookie = [v for k, v in h if k.lower() == "set-cookie"][0]
name, _, rest = cookie.partition("=")
token, _, attrs = rest.partition(";")
rec["login"] = {"status": s, "cookie_name": name, "attributes": [a.strip() for a in attrs.split(";") if a.strip()]}
auth = {"Cookie": f"{name}={token}"}
s, h, _ = req("GET", "/api/auth/me", headers={**auth, "Origin": "http://evil.example"})
rec["cross_origin_get"] = {"status": s, "acao": [v for k, v in h if k.lower().startswith("access-control-")]}
s, h, _ = req("OPTIONS", "/api/files", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
rec["preflight"] = {"status": s, "acao": [v for k, v in h if k.lower().startswith("access-control-")]}
s, h, b = req("POST", "/api/auth/logout", headers=auth)
rec["logout"] = {"status": s, "clears_cookie": any(k.lower() == "set-cookie" and "Max-Age=0" in v or "expires=Thu, 01 Jan 1970" in v for k, v in h)}
s, _, _ = req("GET", "/api/auth/me", headers=auth)
rec["after_logout"] = s
s, h, _ = req("POST", "/api/auth/login", {"username": USERS["user2"], "password": creds()["user2"]})
token2 = [v for k, v in h if k.lower() == "set-cookie"][0].split(";")[0]
s_before, _, _ = req("GET", "/api/auth/me", headers={"Cookie": token2})
con = sqlite3.connect(iso_db)
uid = con.execute("select id from users where username=?", (USERS["user2"],)).fetchone()[0]
n = con.execute("update auth_sessions set expires_at = datetime('now', '-1 minute') where user_id=?", (uid,)).rowcount
con.commit()
con.close()
s_after, _, _ = req("GET", "/api/auth/me", headers={"Cookie": token2})
rec["expiry"] = {"before": s_before, "sessions_expired": n, "after": s_after}
s, _, b = req("POST", "/api/auth/login", {"username": "diag-pending", "password": creds()["pending"]})
rec["pending_login"] = {"status": s, "detail": json.loads(b).get("detail")}
print(json.dumps(rec, ensure_ascii=False, indent=1))
open(out, "w", encoding="utf-8").write(json.dumps(rec, ensure_ascii=False, indent=1))
