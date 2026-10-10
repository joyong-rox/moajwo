"""행정안전부 행정동 코드표(말소코드 포함) 조회.

저장소에 함께 배포하는 공식 원본 xlsx(reference/)를 처음 쓸 때 한 번 읽어 메모리에 둔다. 네트워크는 쓰지 않는다.
코드 체계 (10자리): 시도 2 + 시군구 3 + 읍면동 3 + 00
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import openpyxl

REFERENCE_DIR = Path(__file__).with_name("reference")
SOURCE_META = REFERENCE_DIR / "admin_codes.source.json"


@dataclass(frozen=True)
class AdminArea:
    code: str  # 10자리 행정동코드
    sido: str
    sigungu: str | None
    eupmyeondong: str | None
    created: str | None  # YYYYMMDD
    abolished: str | None  # YYYYMMDD, 현존이면 None

    @property
    def level(self) -> str:
        if self.eupmyeondong:
            return "eupmyeondong"
        if self.sigungu:
            return "sigungu"
        return "sido"

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.sido, self.sigungu, self.eupmyeondong) if p)


@dataclass(frozen=True)
class AdminCodeTable:
    by_code: dict[str, AdminArea]
    by_prefix: dict[int, dict[str, AdminArea]]  # 길이(2/5/8/10) → 앞자리 → 해당 단위 지역
    effective_date: str

    def lookup(self, code: str) -> AdminArea | None:
        """2자리 시도, 5자리 시군구, 8자리 읍면동, 10자리 행정동 코드를 조회한다. 말소 코드도 찾는다."""
        return self.by_prefix.get(len(code), {}).get(code)


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@lru_cache(maxsize=1)
def load_table() -> AdminCodeTable:
    meta = json.loads(SOURCE_META.read_text(encoding="utf-8"))
    wb = openpyxl.load_workbook(REFERENCE_DIR / meta["file"], read_only=True)
    try:
        rows = wb.worksheets[0].iter_rows(values_only=True)
        header = next(rows)
        if list(header[:6]) != meta["columns"]:
            raise ValueError(f"행정동 코드표 형식이 예상과 다릅니다: {header}")
        by_code: dict[str, AdminArea] = {}
        for row in rows:
            code = _text(row[0])
            if code is None or len(code) != 10 or not code.isdigit():
                continue
            area = AdminArea(code, _text(row[1]) or "", _text(row[2]), _text(row[3]), _text(row[4]), _text(row[5]))
            # 같은 코드가 말소 후 재사용된 경우 현존 행을 우선한다.
            if code not in by_code or by_code[code].abolished:
                by_code[code] = area
    finally:
        wb.close()

    by_prefix: dict[int, dict[str, AdminArea]] = {2: {}, 5: {}, 8: {}, 10: dict(by_code)}
    for area in by_code.values():
        length = {"sido": 2, "sigungu": 5, "eupmyeondong": 8}[area.level]
        key = area.code[:length]
        current = by_prefix[length].get(key)
        if current is None or (current.abolished and not area.abolished):
            by_prefix[length][key] = area
    return AdminCodeTable(by_code, by_prefix, meta["effective_date"])


def _compact(text: str) -> str:
    return "".join(text.split())


@lru_cache(maxsize=1)
def _name_index() -> dict[str, tuple[str, str | None]]:
    """정식명칭(공백 무시) → (시도 2자리, 시군구 3자리 또는 None). 현존 코드를 말소 코드보다 우선한다."""
    table = load_table()
    index: dict[str, tuple[str, str | None]] = {}
    current: dict[str, bool] = {}
    for length in (2, 5):
        for prefix, area in table.by_prefix[length].items():
            name = _compact(area.sido if length == 2 else f"{area.sido}{area.sigungu or ''}")
            alive = area.abolished is None
            if name in index and current[name] and not alive:
                continue
            index[name] = (prefix[:2], prefix[2:5] if length == 5 else None)
            current[name] = alive
    return index


def infer_region_codes(name: str) -> tuple[str, str | None] | None:
    """자치단체 정식명칭으로 시도·시군구 코드를 찾는다. '서울특별시 강남구' → ('11', '680'), '경기도' → ('41', None).

    용어사전 업로드에서 비어 있는 코드를 채우는 데 쓴다 (못 찾으면 None).
    """
    return _name_index().get(_compact(name))
