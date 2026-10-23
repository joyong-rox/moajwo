#!/usr/bin/env bash
# 결과 파일 생성 메모리 (격리 러너 전용). image_check.sh가 만든 moajwo-backend:check 이미지를 쓴다.
# 합성 xlsx는 측정 대상이 아닌 일회용 컨테이너에서 만들고, 측정은 512MiB 컨테이너(Render 권장 읽기 한도)에서 한다.
#  A: 결과 칸 예산을 끈 컨테이너 — 메모리 부족 종료 재현, 다시 띄워 정리 확인
#  B: Render 권장 예산(MOAJWO_MAX_OUTPUT_CELLS=300000) — 한도 아래·경계·위, 통계 요약, 동시 요청, 통합
# 사용법: bash output_memory.sh <출력 폴더>
set -uo pipefail
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
FILES=$(mktemp -d)
chmod 777 "$FILES"
docker run -i --rm -v "$FILES:/files" --entrypoint python moajwo-backend:check - <<'PY' > "$OUT/files.txt" 2>&1
import zipfile
import openpyxl
GU = ["종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구", "강북구", "도봉구", "노원구", "은평구",
      "서대문구", "마포구", "양천구", "강서구", "구로구", "금천구", "영등포구", "동작구", "관악구", "서초구", "강남구", "송파구", "강동구"]
def make(path, rows, seed):
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet(f"p{seed}")
    ws.append(["주민등록 인구 현황 2026년 9월 (단위: 명)"])
    ws.append(["시도", "시군구", "인구수"])
    for i in range(rows):
        ws.append(["서울특별시", GU[i % len(GU)], 1000 + (i * 7 + seed) % 50000])
    wb.save(path)
for per in (20000, 5000, 6000, 6200):
    for i in range(10):
        make(f"/files/b{per}_{i}.xlsx", per, per + i)
for i in range(4):
    make(f"/files/i20000_{i}.xlsx", 20000, 7 + i)
wb = openpyxl.Workbook(write_only=True)   # 큰 xlsx (풀었을 때 16MiB 한도 아래를 노림) — 결과를 만드는 동안 올린다
ws = wb.create_sheet("시트1")
ws.append(["기준일자", "시도", "시군구", "기관코드", "인구", "세대수", "예산", "비고"])
for i in range(39000):
    ws.append([f"2026-09-{i % 28 + 1:02d}", "서울특별시", f"시군구{i % 25}", 1100000 + i, 1000 + i, 400 + i % 97, i * 1.5, "합성"])
wb.save("/files/x_big.xlsx")
with zipfile.ZipFile("/files/x_big.xlsx") as z:
    print("x_big unpacked MiB", round(sum(x.file_size for x in z.infolist()) / 2**20, 2))
PY
ls -l "$FILES" >> "$OUT/files.txt"
LIMITS="-e MOAJWO_MAX_UPLOAD_MIB=10 -e MOAJWO_MAX_XLSX_UNCOMPRESSED_MIB=16 -e MOAJWO_MAX_FILE_CELLS=400000 -e MOAJWO_VIEW_CACHE_MIB=32 -e MOAJWO_PARSE_SLOTS=1"
run_part() {  # run_part <A|B> <결과 칸 예산>
  local name="out$1"
  docker run -d --name "$name" --memory=512m --memory-swap=512m -e OLLAMA_BASE_URL=http://127.0.0.1:9 $LIMITS \
    -e MOAJWO_MAX_OUTPUT_CELLS="$2" -p 127.0.0.1:18100:8000 moajwo-backend:check > /dev/null
  for _ in $(seq 1 60); do
    [ "$(docker inspect --format '{{.State.Health.Status}}' "$name" 2>/dev/null)" = healthy ] && break
    sleep 2
  done
  python3 "$HERE/output_memory.py" "$1" http://127.0.0.1:18100 "$name" "$FILES" "$OUT" > "$OUT/output-memory-$1.log" 2>&1
  echo "exit=$?" >> "$OUT/output-memory-$1.log"
  docker logs "$name" > "$OUT/out$1-backend.log" 2>&1
  docker inspect "$name" --format 'OOMKilled={{.State.OOMKilled}} running={{.State.Running}} restarts={{.RestartCount}}' >> "$OUT/output-memory-$1.log"
  docker rm -f "$name" > /dev/null
}
run_part A 5000000
run_part B 300000
rm -rf "$FILES"
