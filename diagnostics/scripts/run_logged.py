"""명령 하나를 실행하고 표준 출력·오류 출력·종료 코드·실행 명령·대상 SHA·시각을 그대로 보관한다 (시험·판정 실행 기록용).

실패가 다시 나오면 이 기록으로 원인을 본다 — 통과할 때까지 반복해 실패를 덮지 않기 위한 것이다.
대상 SHA는 실행한 폴더가 속한 Git 저장소의 HEAD와 작업 트리 변경 여부다 (--sha-of로 다른 저장소·작업 트리를 지정할 수 있다).
출력은 받은 바이트 그대로 저장하고, 출력 인코딩에 영향을 주는 환경(PYTHONIOENCODING·PYTHONUTF8·기본 인코딩)도 남긴다.
사용법: python run_logged.py <보관 폴더> <이름> [--cwd 폴더] [--sha-of 폴더] -- <명령 ...>
종료 코드는 실행한 명령의 종료 코드를 그대로 돌려준다.
"""

import json
import locale
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def git(args: list[str], where: Path) -> str:
    p = subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, encoding="utf-8")
    return p.stdout.strip() if p.returncode == 0 else f"(git 실패: {p.stderr.strip()[:120]})"


def main() -> int:
    if "--" not in sys.argv or len(sys.argv) < 5:
        print(__doc__, file=sys.stderr)
        return 5
    i = sys.argv.index("--")
    head, cmd = sys.argv[1:i], sys.argv[i + 1 :]
    out, name = Path(head[0]), head[1]
    opts = dict(zip(head[2::2], head[3::2], strict=False))
    cwd = Path(opts.get("--cwd", "."))
    sha_of = Path(opts.get("--sha-of", str(cwd)))
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    p = subprocess.run(cmd, cwd=cwd, capture_output=True)
    (out / f"{name}.stdout.txt").write_bytes(p.stdout)
    (out / f"{name}.stderr.txt").write_bytes(p.stderr)
    env = {k: os.environ.get(k) for k in ("PYTHONIOENCODING", "PYTHONUTF8")} | {"preferred_encoding": locale.getpreferredencoding(False)}
    rec = {"name": name, "command": cmd, "cwd": str(cwd.resolve()), "started": started, "ended": datetime.now().astimezone().isoformat(timespec="seconds"),
           "exit_code": p.returncode, "target_sha": git(["rev-parse", "HEAD"], sha_of), "target_branch": git(["branch", "--show-current"], sha_of),
           "worktree_dirty": bool(git(["status", "--porcelain"], sha_of)), "stdout": f"{name}.stdout.txt", "stderr": f"{name}.stderr.txt",
           "output": "받은 바이트 그대로 (디코딩하지 않음)", "encoding_env": env}  # fmt: skip
    (out / f"{name}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"[{name}] 종료 코드 {p.returncode} · 기록 {out / (name + '.json')}")
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
