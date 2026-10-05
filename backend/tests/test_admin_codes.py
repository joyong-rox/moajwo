import hashlib
import json

from app.admin_codes import REFERENCE_DIR, SOURCE_META, load_table


def test_reference_file_matches_recorded_hash() -> None:
    meta = json.loads(SOURCE_META.read_text(encoding="utf-8"))
    digest = hashlib.sha256((REFERENCE_DIR / meta["file"]).read_bytes()).hexdigest()
    assert digest == meta["file_sha256"]


def test_lookup_levels() -> None:
    table = load_table()
    assert table.lookup("11").full_name == "서울특별시"
    assert table.lookup("11500").full_name == "서울특별시 강서구"
    assert table.lookup("26440").full_name == "부산광역시 강서구"  # 동명 시군구는 시도로 구분
    assert table.lookup("11500520").full_name == "서울특별시 강서구 등촌제1동"
    assert table.lookup("1150052000").level == "eupmyeondong"
    assert table.lookup("11510") is None
    assert table.lookup("4111") is None


def test_abolished_and_new_sido() -> None:
    table = load_table()
    assert table.lookup("12").sido == "전남광주통합특별시" and table.lookup("12").abolished is None
    assert table.lookup("29").abolished == "20260701"  # 광주광역시 → 통합
    assert table.lookup("42").abolished == "20230611"  # 강원도 → 강원특별자치도(51)
    assert table.lookup("51").abolished is None


def test_active_sido_count() -> None:
    table = load_table()
    active = [a for a in table.by_prefix[2].values() if a.abolished is None]
    assert len(active) == 16
