#!/usr/bin/env bash
# 결과 파일 생성 메모리 (격리 러너 전용). image_check.sh가 만든 moajwo-backend:check 이미지를 쓴다.
# 합성 xlsx는 측정 대상이 아닌 일회용 컨테이너에서 만들고, 측정은 512MiB 컨테이너(Render 권장 읽기 한도)에서 한다.
#  A: 결과 칸 예산을 끈 컨테이너 — 메모리 부족 종료 재현, 다시 띄워 정리 확인
#  B: Render 권장 예산(MOAJWO_MAX_OUTPUT_CELLS=300000) — 한도 아래·경계·위, 통계 요약, 동시 요청, 통합
#  C: 통계 요약 묶기·한도 가까운 통합  ·  D1~D7: 동시 생성·긴 글자·넓은 표·큰 통계 요약·분석 Excel·큰 병합 (사례마다 새 컨테이너)
#  D3L: 로컬 기본 한도(1GiB 컨테이너)에서 공유 긴 글자  ·  D8: 결과 만들기 차례 2개(MOAJWO_OUTPUT_SLOTS=2) + 동시 큰 xlsx 올리기
# 사용법: [IMAGE=moajwo-backend:before] [PARTS="D1 D2"] bash output_memory.sh <출력 폴더> — 이미지 기본은 moajwo-backend:check
set -uo pipefail
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
IMAGE=${IMAGE:-moajwo-backend:check}
FILES=$(mktemp -d)
chmod 777 "$FILES"
docker run -i --rm -v "$FILES:/files" -v "$HERE:/tools:ro" --entrypoint python "$IMAGE" - <<'PY' > "$OUT/files.txt" 2>&1
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
for i in range(10, 15):  # A3: 15개 × 20,000행 = 묶기 행 한도 300,000행
    make(f"/files/b20000_{i}.xlsx", 20000, 20000 + i)
for i in range(4):
    make(f"/files/i20000_{i}.xlsx", 20000, 7 + i)
make("/files/i37000_0.xlsx", 37000, 3)
def make_month(path, rows, month):  # 파일마다 기준 달이 다름 — 통계 요약에서 파일끼리 같은 대상이 겹치지 않게
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet(f"m{month}")
    ws.append([f"주민등록 인구 현황 2026년 {month}월 (단위: 명)"])
    ws.append(["시도", "시군구", "인구수"])
    for i in range(rows):
        ws.append(["서울특별시", GU[i % len(GU)], 1000 + (i * 7 + month) % 50000])
    wb.save(path)
for per in (5000, 6000):
    for i in range(10):
        make_month(f"/files/m{per}_{i}.xlsx", per, i + 1)
wb = openpyxl.Workbook(write_only=True)   # 큰 xlsx (풀었을 때 16MiB 한도 아래를 노림) — 결과를 만드는 동안 올린다
ws = wb.create_sheet("시트1")
ws.append(["기준일자", "시도", "시군구", "기관코드", "인구", "세대수", "예산", "비고"])
for i in range(39000):
    ws.append([f"2026-09-{i % 28 + 1:02d}", "서울특별시", f"시군구{i % 25}", 1100000 + i, 1000 + i, 400 + i % 97, i * 1.5, "합성"])
wb.save("/files/x_big.xlsx")
with zipfile.ZipFile("/files/x_big.xlsx") as z:
    print("x_big unpacked MiB", round(sum(x.file_size for x in z.infolist()) / 2**20, 2))
# 부분 D — 긴 글자·넓은 표·큰 통계 요약·분석 입력
HAN = "가나다라마바사아자차카타파하"
def long_csv(path, rows, chars, seed):  # 칸마다 3,000자 (UTF-8 약 9MB — 올리기 한도 10MiB 아래)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("시도,시군구,비고\n")
        for i in range(rows):
            body = f"{seed}-{i:06d}" + "".join(HAN[(i + j + seed) % len(HAN)] for j in range(chars - 10))
            f.write(f"서울특별시,{GU[i % len(GU)]},{body}\n")
for i in range(5):
    long_csv(f"/files/lt_{i}.csv", 1000, 3000, i)
LONGSTR = " " + "가" * 32000 + " "  # 앞뒤 공백 — 읽을 때 칸마다 정리한 사본이 생긴다 (공유 문자열은 파일에 한 번)
import sys
sys.path.insert(0, "/tools")
from shared_xlsx import shared_xlsx  # Excel처럼 공유 문자열로 저장 (openpyxl은 칸마다 글자를 넣어 저장한다)
for n in (499, 500, 1999, 2000, 4000, 8000):  # 499칸 = 15,968,998자 (Render 1,600만 바로 아래), 1999칸 = 로컬 6,400만 바로 아래
    shared_xlsx(f"/files/sst_{n}.xlsx", [["시도", "시군구", "비고"]] + [["서울특별시", GU[i % len(GU)], LONGSTR] for i in range(n)])
for k in range(8):  # D9~D11: 한도(1,600만 자) 바로 아래 파일 — 글자가 조금씩 달라 같은 파일로 보이지 않게
    v = " " + chr(0xAC00 + k) + "가" * 31999 + " "
    shared_xlsx(f"/files/sst499_{k}.xlsx", [["시도", "시군구", "비고"]] + [["서울특별시", GU[i % len(GU)], v] for i in range(499)])
shared_xlsx("/files/merge_300k.xlsx", [["시도", "인구수"], ["서울특별시", "10"]], merges=("A3:Z11540",))  # 26 × 11,538 ≈ 30만 칸
shared_xlsx("/files/merge_1m.xlsx", [["시도", "인구수"], ["서울특별시", "10"]], merges=("A3:Z40000",))  # 26 × 39,998 ≈ 104만 칸
wb = openpyxl.Workbook(write_only=True)  # 1,000열 × 290행
ws = wb.create_sheet("w")
ws.append(["시도", "시군구"] + [f"항목{j}" for j in range(998)])
for i in range(290):
    ws.append(["서울특별시", GU[i % len(GU)]] + [i * 1000 + j for j in range(998)])
wb.save("/files/w_wide.xlsx")
def stats(path, rows, seed):  # 숫자 항목 7개
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet(f"s{seed}")
    ws.append([f"인구·예산 현황 2026년 {seed + 1}월"])
    ws.append(["시도", "시군구", "인구수(명)", "남자(명)", "여자(명)", "세대수(세대)", "예산(천원)", "지출(천원)", "집행률(%)"])
    for i in range(rows):
        ws.append(["서울특별시", GU[i % len(GU)], 1000 + i % 5000, 500 + i % 2500, 500 + i % 2400, 400 + i % 97, i * 10, i * 7,
                   (i % 100) / 1.0])
    wb.save(path)
for i in range(4):
    stats(f"/files/s7_{i}.xlsx", 27000, i)
wb = openpyxl.Workbook(write_only=True)  # 분석: 시군구 5,000개, 숫자 아닌 값 5,000행
ws = wb.create_sheet("a")
ws.append(["주민등록 인구 현황 2026년 9월 (단위: 명)"])
ws.append(["시도", "시군구", "인구수"])
for i in range(30000):
    ws.append(["서울특별시", f"시군구{i % 5000:04d}", (1000 + i) if i % 6 else "자료없음"])
wb.save("/files/an_30k.xlsx")
PY
ls -l "$FILES" >> "$OUT/files.txt"
# Render 권장 읽기 한도 (docs/MEMORY.md). 글자 수 한도는 수정 전 이미지에 없는 변수라 무시된다 — 같은 명령으로 둘을 잰다
LIMITS="-e MOAJWO_MAX_UPLOAD_MIB=10 -e MOAJWO_MAX_XLSX_UNCOMPRESSED_MIB=16 -e MOAJWO_MAX_FILE_CELLS=400000 -e MOAJWO_VIEW_CACHE_MIB=32 -e MOAJWO_PARSE_SLOTS=1 -e MOAJWO_MAX_FILE_TEXT_CHARS=16000000"
run_part() {  # run_part <부분> <결과 칸 예산> [메모리] [LIMITS 대신 쓸 환경 변수]
  local name="out$1" memory="${3:-512m}" limits="${4-$LIMITS}"
  docker run -d --name "$name" --memory="$memory" --memory-swap="$memory" -e OLLAMA_BASE_URL=http://127.0.0.1:9 $limits \
    -e MOAJWO_MAX_OUTPUT_CELLS="$2" -p 127.0.0.1:18100:8000 "$IMAGE" > /dev/null
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
for part in ${PARTS:-A B}; do
  case "$part" in
    A|A3) run_part "$part" 5000000 ;;
    D3L) run_part D3L 1000000 1g "" ;;  # 로컬·폐쇄망 기본 한도(환경 변수 없음) — 로컬은 컨테이너 제한이 없어 1GiB로 잰다
    D8) run_part D8 300000 512m "$LIMITS -e MOAJWO_OUTPUT_SLOTS=2" ;;
    *) run_part "$part" 300000 ;;
  esac
done
rm -rf "$FILES"
