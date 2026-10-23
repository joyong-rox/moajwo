#!/usr/bin/env bash
# 결과 파일 생성 메모리 (격리 러너 전용). image_check.sh가 만든 moajwo-backend:check 이미지를 쓴다.
# 합성 xlsx는 측정 대상이 아닌 일회용 컨테이너에서 만들고, 측정은 512MiB 컨테이너(Render 권장 읽기 한도)에서 한다.
# 사용법: bash output_memory.sh <출력 폴더>
set -uo pipefail
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
FILES=$(mktemp -d)
chmod 777 "$FILES"
docker run -i --rm -v "$FILES:/files" --entrypoint python moajwo-backend:check - <<'PY' > "$OUT/files.txt" 2>&1
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
    print(path, rows)
for per in (2000, 5000, 10000, 15000, 20000, 30000):
    for i in range(10):
        make(f"/files/b{per}_{i}.xlsx", per, per + i)
make("/files/small.xlsx", 3, 1)
PY
ls -l "$FILES" >> "$OUT/files.txt"
docker run -d --name outmem --memory=512m --memory-swap=512m \
  -e OLLAMA_BASE_URL=http://127.0.0.1:9 -e MOAJWO_MAX_UPLOAD_MIB=10 -e MOAJWO_MAX_XLSX_UNCOMPRESSED_MIB=16 \
  -e MOAJWO_MAX_FILE_CELLS=400000 -e MOAJWO_VIEW_CACHE_MIB=32 -e MOAJWO_PARSE_SLOTS=1 \
  -p 127.0.0.1:18100:8000 moajwo-backend:check > /dev/null
for _ in $(seq 1 60); do
  [ "$(docker inspect --format '{{.State.Health.Status}}' outmem 2>/dev/null)" = healthy ] && break
  sleep 2
done
python3 "$HERE/output_memory.py" http://127.0.0.1:18100 outmem "$FILES" "$OUT" > "$OUT/output-memory.log" 2>&1
echo "exit=$?" >> "$OUT/output-memory.log"
docker logs outmem > "$OUT/outmem-backend.log" 2>&1
docker inspect outmem --format 'OOMKilled={{.State.OOMKilled}} running={{.State.Running}} restarts={{.RestartCount}}' >> "$OUT/output-memory.log"
docker rm -f outmem > /dev/null
rm -rf "$FILES"
