"""진단 스택 볼륨의 보존 확인용 스냅숏 — 표마다 행 해시(rowid별), 업로드 원본·결과 파일의 sha256. 내용은 남기지 않는다.

진단 backend 컨테이너 안에서 DB를 읽기 전용(mode=ro)으로 열고 /app/data 아래 파일을 읽기만 한다 (운영 컨테이너는 받지 않음).
사용법: python diag_snapshot.py snap <결과 JSON>
        python diag_snapshot.py diff <앞 JSON> <뒤 JSON> [<결과 JSON>]   — 앞에 있던 행·파일이 바뀌거나 사라졌는지, 새로 생긴 것은 몇 개인지
"""

import json
import subprocess
import sys
from pathlib import Path

from diag_common import BACKEND

SNAP_PY = r"""
import hashlib, json, os, sqlite3
c = sqlite3.connect('file:/app/data/moajwo.db?mode=ro', uri=True)
tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
out = {'tables': {}, 'files': {}}
for t in tables:
    try:
        rows = c.execute(f'select rowid, * from "{t}" order by rowid').fetchall()
    except sqlite3.OperationalError:  # rowid 없는 표
        rows = [(i, *r) for i, r in enumerate(c.execute(f'select * from "{t}"').fetchall())]
    out['tables'][t] = {str(r[0]): hashlib.sha256(json.dumps(r[1:], default=str, ensure_ascii=False).encode()).hexdigest()[:16] for r in rows}
for base in ('uploads', 'outputs'):
    root = os.path.join('/app/data', base)
    for d, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(d, f)
            h = hashlib.sha256()
            with open(p, 'rb') as fh:
                for b in iter(lambda: fh.read(1 << 20), b''):
                    h.update(b)
            out['files'][os.path.relpath(p, '/app/data')] = h.hexdigest()
print(json.dumps(out))
"""


def snap(path: Path) -> None:
    p = subprocess.run(["docker", "exec", BACKEND, "python", "-c", SNAP_PY], capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise SystemExit(f"스냅숏 실패: {p.stderr[-400:]}")
    data = json.loads(p.stdout)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"표 {len(data['tables'])}개 · 행 {sum(len(v) for v in data['tables'].values())} · 파일 {len(data['files'])}")


def diff(a_path: Path, b_path: Path, out: Path | None) -> None:
    a, b = (json.loads(p.read_text(encoding="utf-8")) for p in (a_path, b_path))
    res = {"tables": {}, "files": {}}
    for t in sorted(set(a["tables"]) | set(b["tables"])):
        ra, rb = a["tables"].get(t, {}), b["tables"].get(t, {})
        changed = sorted((k for k in ra if k in rb and ra[k] != rb[k]), key=int)
        gone = sorted((k for k in ra if k not in rb), key=int)
        added = len([k for k in rb if k not in ra])
        if changed or gone or added:
            res["tables"][t] = {"before": len(ra), "after": len(rb), "changed_rowids": changed, "deleted_rowids": gone, "added": added}
    fa, fb = a["files"], b["files"]
    res["files"] = {"before": len(fa), "after": len(fb), "changed": sorted(k for k in fa if k in fb and fa[k] != fb[k]),
                    "deleted": sorted(k for k in fa if k not in fb), "added": len([k for k in fb if k not in fa])}  # fmt: skip
    for t, v in res["tables"].items():
        print(f"{t}: {v['before']} → {v['after']} · 바뀐 기존 행 {len(v['changed_rowids'])} · 사라진 행 {len(v['deleted_rowids'])} · 새 행 {v['added']}")
    f = res["files"]
    print(f"파일: {f['before']} → {f['after']} · 바뀜 {len(f['changed'])} · 사라짐 {len(f['deleted'])} · 새 파일 {f['added']}")
    if out:
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1] == "snap":
        snap(Path(sys.argv[2]))
    else:
        diff(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]) if len(sys.argv) > 4 else None)
