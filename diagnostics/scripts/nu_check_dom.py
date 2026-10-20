"""a11y_scan.py를 NU_URL=off로 돌려 저장한 DOM·서버 문서를 로컬 Nu Html Checker로 검사한다 (브라우저와 Nu를 동시에 띄우지 않기).

HTML은 diag_scope.nu_base가 허용하는 로컬 검사기 주소(http://127.0.0.1:8888)에만 보낸다.
결과를 같은 폴더의 a11y-results.json(각 상태의 "nu")과 nu-server-documents.json에 채운다.
사용법: python nu_check_dom.py <a11y_scan 출력 폴더>   환경: NU_URL(기본 http://127.0.0.1:8888)
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

from diag_scope import check_url, nu_base, opener

OUT = Path(sys.argv[1])
NU = nu_base(os.environ.get("NU_URL"))
OP = opener(NU)


def nu(body: bytes, ctype: str) -> list[dict]:
    req = urllib.request.Request(check_url(f"{NU}/?out=json", NU), data=body, headers={"Content-Type": ctype}, method="POST")
    with OP.open(req, timeout=120) as r:
        return json.loads(r.read())["messages"]


def main() -> None:
    path = OUT / "a11y-results.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    done = 0
    for rec in data["results"]:
        if rec["status"] != "검사함" or rec.get("nu") is not None:
            continue
        html = (OUT / "dom" / f"{rec['viewport']}-{rec['id']}.html").read_bytes()
        rec["nu"] = [{"type": m["type"], "sub": m.get("subType"), "msg": m["message"][:220], "extract": (m.get("extract") or "")[:120]}
                     for m in nu(html, "text/html; charset=utf-8")]  # fmt: skip
        done += 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    docs = {}
    server = OUT / "server"
    for f in sorted(server.iterdir()) if server.exists() else []:
        name = "index.html" if f.name == "index.html" else "/" + f.name.replace("__", "/")
        ctype = "text/html; charset=utf-8" if f.suffix == ".html" else "text/css; charset=utf-8" if f.suffix == ".css" else "image/svg+xml"
        docs[name] = nu(f.read_bytes(), ctype)
    (OUT / "nu-server-documents.json").write_text(json.dumps(docs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"DOM {done}개·서버 문서 {len(docs)}개 검사")


if __name__ == "__main__":
    main()
