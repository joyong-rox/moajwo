import os
import tempfile

# app 모듈을 임포트하기 전에 테스트 전용 데이터 디렉터리를 지정한다 (실제 DB 보호).
os.environ["MOAJWO_DATA_DIR"] = tempfile.mkdtemp(prefix="moajwo-test-")
os.environ.pop("DATABASE_URL", None)
