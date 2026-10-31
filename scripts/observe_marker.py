# 관찰 점검(scripts/observe.sh) 보조 — 실행 중인 backend 이미지의 앱 코드에 저장소의 사유 표식(backend/app/account_markers.py의
# GENERATED_TEMP_PASSWORD_NOTE)이 들어 있는지 확인한다(7-4차). 읽기 전용: 이미지의 app/account_markers.py(있으면) 또는 app/adminctl.py를 읽기만 한다.
# 운영에서는 observe.sh가 실행 중 이미지의 one-off 컨테이너(볼륨 없음, 저장소 표식 파일만 ro 마운트) python에 표준 입력으로 넘기고,
# 시험에서는 호스트 python으로 합성 이미지 폴더(OBS_IMAGE_APP_DIR)에 실행한다.
# 출력 한 줄: "match <파일>" · "mismatch <파일>" · "missing" · "error <이유>". 불일치면 observe.sh가 판정 7번을 '주의'로 낸다(발급 이력을 놓칠 수 있음).
import importlib.util
import os
import sys

app_dir = os.environ.get("IMAGE_APP_DIR", "/app/app")
markers = os.environ.get("OBS_MARKERS_PATH", "/markers/account_markers.py")
try:
    spec = importlib.util.spec_from_file_location("account_markers", markers)
    if spec is None or spec.loader is None or not os.path.isfile(markers):
        raise FileNotFoundError(markers)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    note = mod.GENERATED_TEMP_PASSWORD_NOTE
except Exception as exc:  # noqa: BLE001 — 어떤 이유든 한 줄로 보고하고 끝낸다
    print(f"error 저장소 표식 모듈을 읽을 수 없음: {type(exc).__name__}")
    sys.exit(0)
for name in ("account_markers.py", "adminctl.py"):  # 새 판(상수 모듈) 먼저, 없으면 옛 판(adminctl.py 안의 리터럴)
    path = os.path.join(app_dir, name)
    if os.path.isfile(path):
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError as exc:
            print(f"error {name} 읽기 실패: {type(exc).__name__}")
            sys.exit(0)
        print(("match " if note in text else "mismatch ") + name)
        sys.exit(0)
print("missing")
