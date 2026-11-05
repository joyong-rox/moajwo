"""APT 보안 수정판(.deb)을 APT를 거치지 않고 받아 검증한다 — Dockerfile 빌드 단계 전용.
설명: docs/SECURITY-APT-20261105.md

왜: 기반 이미지의 apt 3.0.3에는 DSA-6607-1(CVE-2026-109518) 결함이 있다 —
HTTP 리디렉션·다운로드 재개 중에 검증한 패키지가 바뀔 수 있다. 수정판 3.0.4를 그 apt로 받으면
고치려는 결함을 그대로 거친다. 그래서 이 스크립트가 받고, 검증의 기준은 전송 경로가 아니라
(1) Debian 서명과 (2) 고정한 SHA-256이다.

검증 순서 (하나라도 어긋나면 종료 1, 아무것도 쓰지 않는다):
  1. dists/<SUITE>/InRelease를 받아 기반 이미지에 든 Debian 저장소 키링(debian-archive-keyring)으로
     sqv 서명 검증(Cleartext Signature). 서명이 맞으면 sqv가 내놓는 '검증된 본문'만 읽는다.
     Codename 확인, Valid-Until이 지금보다 뒤인지 확인(옛 메타데이터 재사용 방지).
  2. 그 본문의 SHA256 목록에 적힌 main/binary-amd64/Packages.xz를 by-hash 주소로 받아 크기·SHA-256 대조.
  3. Packages에서 고정한 패키지·판의 항목을 찾아 SHA-256·크기가 아래 PINS와 같은지 대조
     (서명된 목록 ↔ 저장소에 고정한 값).
  4. 각 .deb를 받아 크기·SHA-256이 항목·PINS와 같은지 대조한다. 모두 맞을 때만 출력 폴더에 쓴다.
설치(dpkg -i)와 판 비교(dpkg --compare-versions)는 Dockerfile이 한다.
표준 라이브러리만 쓴다 (기반 이미지의 python).

사용: python apt_verified_fetch.py <출력 폴더>   — 위 검증 후 .deb 저장
      python apt_verified_fetch.py --discover     — 1~2와 Packages 항목 출력만
                                                    (PINS를 정할 때. 고정값과 대조하지 않음)
"""

from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import lzma
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ARCHIVE = "https://deb.debian.org/debian-security"
SUITE = "trixie-security"
INDEX = "main/binary-amd64/Packages.xz"
KEYRING = "/usr/share/keyrings/debian-archive-keyring.pgp"
# 패키지 → (판, .deb SHA-256, 크기).
# 2026-11-05 서명 검증한 trixie-security Packages에서 옮김 (docs/SECURITY-APT-20261105.md).
PINS: dict[str, tuple[str, str, int]] = {
    "libapt-pkg7.0": ("3.0.4", "577eb8f489de419290102364892dbd13ca256297b61f96d61b114fd4ac18be41", 1113872),
    "apt": ("3.0.4", "15c2030e0fb46580af4f1b4cc303f4bd26da781b004e99763bc9eff7cf8e19b8", 1453228),
}


def fail(msg: str) -> None:
    print(f"[apt-fix 실패] {msg}", file=sys.stderr)
    sys.exit(1)


def get(url: str, limit: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "moajwo-build-apt-fix/1"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 — 고정 https 주소
        body = r.read(limit + 1)
    if len(body) > limit:
        fail(f"응답이 예상 크기를 넘음: {url}")
    return body


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fields(text: str) -> list[dict[str, str]]:
    """Debian 제어 파일(Release·Packages) → 문단 목록. 이어지는 줄(공백으로 시작)은 앞 필드에 붙인다."""
    out, cur, last = [], {}, None
    for line in text.splitlines():
        if not line.strip():
            if cur:
                out.append(cur)
            cur, last = {}, None
        elif line[0] in " \t" and last:
            cur[last] += "\n" + line.strip()
        elif ":" in line:
            k, v = line.split(":", 1)
            cur[k], last = v.strip(), k
    if cur:
        out.append(cur)
    return out


def verified_release() -> dict[str, str]:
    """InRelease를 받아 sqv로 서명을 검증하고, 검증된 본문의 첫 문단을 돌려준다."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "InRelease").write_bytes(get(f"{ARCHIVE}/dists/{SUITE}/InRelease", 2_000_000))
        cmd = ["sqv", "--keyring", KEYRING, "--cleartext", str(t / "InRelease"), "--output", str(t / "Release")]
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if r.returncode != 0 or not (t / "Release").exists():
            fail(f"InRelease 서명 검증 실패 (sqv 종료 {r.returncode}): {r.stderr.strip()[:300]}")
        return fields((t / "Release").read_text(encoding="utf-8"))[0]


def main() -> int:
    discover = sys.argv[1:] == ["--discover"]
    if not discover and len(sys.argv) != 2:
        fail("사용: apt_verified_fetch.py <출력 폴더> | --discover")
    if not discover and any(not h or not n for _, h, n in PINS.values()):
        fail("PINS가 비어 있음 — --discover로 확인한 값을 먼저 고정")
    rel = verified_release()
    if rel.get("Codename") != SUITE:
        fail(f"Codename이 {SUITE}가 아님: {rel.get('Codename')}")
    valid_until = email.utils.parsedate_to_datetime(rel["Valid-Until"])
    if valid_until <= dt.datetime.now(dt.UTC):
        fail(f"메타데이터 유효 기간이 지남: Valid-Until {rel['Valid-Until']}")
    sums = {}
    for line in rel.get("SHA256", "").splitlines():
        parts = line.split()
        if len(parts) == 3:
            sums[parts[2]] = (parts[0], int(parts[1]))
    if INDEX not in sums:
        fail(f"서명된 목록에 {INDEX}가 없음")
    want_hash, want_size = sums[INDEX]
    raw = get(f"{ARCHIVE}/dists/{SUITE}/{INDEX.rsplit('/', 1)[0]}/by-hash/SHA256/{want_hash}", want_size)
    if len(raw) != want_size or sha256(raw) != want_hash:
        fail(f"{INDEX} 크기·SHA-256이 서명된 목록과 다름")
    pkgs = fields(lzma.decompress(raw).decode("utf-8"))
    print(f"[apt-fix] InRelease 서명 확인 · Codename {rel['Codename']} · Date {rel.get('Date')}")
    print(f"[apt-fix] Valid-Until {rel['Valid-Until']}")
    print(f"[apt-fix] {INDEX} {want_size} bytes sha256 {want_hash} 확인")
    verified: list[tuple[str, bytes]] = []
    for name, (ver, pin_hash, pin_size) in PINS.items():
        cands = [p for p in pkgs if p.get("Package") == name and p.get("Architecture") in ("amd64", "all")]
        if discover:
            for p in cands:
                desc = " ".join(p.get(k, "-") for k in ("Version", "Architecture", "Size", "SHA256", "Filename"))
                print(f"[apt-fix] 목록 {name} {desc}")
            continue
        st = next((p for p in cands if p.get("Version") == ver), None)
        if st is None:
            have = [p.get("Version") for p in cands]
            fail(f"{name} {ver}이(가) 서명된 목록에 없음 (있는 판: {have}) — 고정값을 새로 검토")
        if st.get("SHA256") != pin_hash or int(st.get("Size", "-1")) != pin_size:
            fail(f"{name} {ver}: 서명된 목록의 SHA-256·크기가 고정값과 다름")
        deb = get(f"{ARCHIVE}/{st['Filename']}", pin_size)
        if len(deb) != pin_size or sha256(deb) != pin_hash:
            fail(f"{name} {ver}: 받은 .deb의 SHA-256·크기가 다름")
        verified.append((st["Filename"].rsplit("/", 1)[1], deb))
        print(f"[apt-fix] {name} {ver} {pin_size} bytes sha256 {pin_hash} 확인")
    if not discover:  # 모든 패키지가 검증된 뒤에만 쓴다 (하나라도 어긋나면 위에서 종료 — 일부만 쓰지 않음)
        out = Path(sys.argv[1])
        out.mkdir(parents=True, exist_ok=True)
        for fname, deb in verified:
            (out / fname).write_bytes(deb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
