"""Trivy 원시 결과(JSON) 재집계 — 새로 스캔하지 않는다. 사용법: python summarize_trivy.py <trivy 결과 폴더> <출력 md>

이미지별 검출 행 수(심각도별), 중복을 뺀 취약점 ID 수, 두 이미지 공통 ID, HIGH 이상 전체 행(패키지·설치 버전·수정 버전·
배포판 수정 상태·심각도 출처·자료 출처·레이어)을 표로 만든다. 두 이미지의 건수를 합쳐 '서로 다른 취약점 수'로 쓰지 않는다.
"""

import collections
import json
import sys
from pathlib import Path

SRC, OUT = Path(sys.argv[1]), Path(sys.argv[2])
IMAGES = {"backend": "image-backend.json", "frontend": "image-frontend.json"}
SEV = {1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "CRITICAL"}


def load(name: str) -> tuple[dict, list[dict]]:
    d = json.loads((SRC / name).read_text(encoding="utf-8"))
    rows = []
    for r in d.get("Results") or []:
        for v in r.get("Vulnerabilities") or []:
            rows.append({**v, "_target": r["Target"], "_type": r.get("Type")})
    return d, rows


def layer_of(d: dict, v: dict) -> str:
    diff = (v.get("Layer") or {}).get("DiffID")
    ids = d["Metadata"].get("DiffIDs") or []
    return f"{ids.index(diff) + 1}/{len(ids)}" if diff in ids else "?"


def sev_source(v: dict) -> str:
    vs = v.get("VendorSeverity") or {}
    pick = v.get("SeveritySource")
    vendor = ", ".join(f"{k}:{SEV.get(x, x)}" for k, x in sorted(vs.items()))
    return f"{pick or '(배포판 기본)'} | {vendor}"


lines = ["# Trivy 재집계 (원시 결과 기준, 새 스캔 없음)", ""]
version = (SRC / "version.txt").read_text(encoding="utf-8") if (SRC / "version.txt").exists() else ""
lines += ["```", version.strip(), "```", ""]
data = {k: load(v) for k, v in IMAGES.items()}
ids_by_image: dict[str, set] = {}
high_by_image: dict[str, set] = {}
lines += ["## 이미지별 검출", "", "| 이미지 | 검사 대상(Trivy ImageID = 설정 다이제스트) | OS | 검출 행 (심각도별) | 중복 뺀 ID | HIGH 행 | HIGH 중복 뺀 ID | HIGH 수정 상태 |",
          "|---|---|---|---|---|---|---|---|"]  # fmt: skip
for k, (d, rows) in data.items():
    m = d["Metadata"]
    c = collections.Counter(v["Severity"] for v in rows)
    ids_by_image[k] = {v["VulnerabilityID"] for v in rows}
    high = [v for v in rows if v["Severity"] in ("HIGH", "CRITICAL")]
    high_by_image[k] = {v["VulnerabilityID"] for v in high}
    st = collections.Counter(v.get("Status") for v in high)
    lines.append(f"| {k} | `{m.get('ImageID')}` | {m['OS']['Family']} {m['OS']['Name']} | {dict(c)} 합 {len(rows)} | {len(ids_by_image[k])} | {len(high)} | "
                 f"{len(high_by_image[k])} | {dict(st)} |")  # fmt: skip
common = ids_by_image["backend"] & ids_by_image["frontend"]
common_high = high_by_image["backend"] & high_by_image["frontend"]
lines += ["", f"- 두 이미지에 함께 있는 ID(전 심각도): {len(common)}개 — {', '.join(sorted(common)) or '없음'}",
          f"- 두 이미지 모두에서 HIGH인 ID: {len(common_high)}개 — {', '.join(sorted(common_high)) or '없음'}",
          f"- 두 이미지의 HIGH 중복 뺀 ID 합집합: {len(high_by_image['backend'] | high_by_image['frontend'])}개 (행 수 합과 다름)", ""]  # fmt: skip
for k, (d, rows) in data.items():
    high = sorted((v for v in rows if v["Severity"] in ("HIGH", "CRITICAL")), key=lambda v: (v["PkgName"], v["VulnerabilityID"]))
    lines += [f"## {k} HIGH 이상 전체 행 ({len(high)}행)", "",
              "| 패키지 | 설치 버전 | 취약점 ID | 수정 버전 | 수정 상태 | 심각도 출처 | 자료 출처 | 레이어 | 대상 |", "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for v in high:
        ds = (v.get("DataSource") or {}).get("Name", "")
        lines.append(f"| {v['PkgName']} | {v['InstalledVersion']} | {v['VulnerabilityID']} | {v.get('FixedVersion') or '없음'} | {v.get('Status')} | "
                     f"{sev_source(v)} | {ds} | {layer_of(d, v)} | {v['_type']} |")  # fmt: skip
    lines.append("")
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
print("\n".join(lines[:16]))
