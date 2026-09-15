"""
VWorld Geocoder API로 단지 주소를 위경도 좌표로 변환.

사용 예:
  python scripts/geocode_complexes.py                # 전체
  python scripts/geocode_complexes.py --region 강남구  # 특정 구만

도로명주소(road_addr)로 먼저 시도하고, 실패하면 지번주소(addr)로 재시도한다.
결과는 data/raw/geocode_cache.json에 주소 문자열 기준으로 캐싱되어, 재실행해도
이미 좌표를 찾은(혹은 실패가 확인된) 주소는 다시 호출하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import pandas as pd

from common import DATA_PROCESSED_DIR, DATA_RAW_DIR, ensure_dirs, get_api_key, get_with_retry

GEOCODE_URL = "http://api.vworld.kr/req/address"
CACHE_PATH = DATA_RAW_DIR / "geocode_cache.json"
REQUEST_DELAY_SEC = 0.1


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=None), encoding="utf-8")


def _geocode_one(address: str, addr_type: str, api_key: str) -> tuple[float, float] | None:
    params = {
        "service": "address",
        "request": "getcoord",
        "version": "2.0",
        "crs": "epsg:4326",
        "address": address,
        "type": addr_type,  # "road" 또는 "parcel"
        "format": "json",
        "key": api_key,
    }
    resp = get_with_retry(GEOCODE_URL, params, timeout=15)
    data = resp.json()
    status = data.get("response", {}).get("status")
    if status != "OK":
        return None
    point = data.get("response", {}).get("result", {}).get("point", {})
    x, y = point.get("x"), point.get("y")
    if x is None or y is None:
        # 응답 구조가 예상과 다르면 원인 파악할 수 있게 원본을 남긴다.
        print(f"  [주의] 예상과 다른 응답 구조: {json.dumps(data, ensure_ascii=False)[:300]}", file=sys.stderr)
        return None
    return float(y), float(x)  # (lat, lon)


def geocode_address(addr: str | None, road_addr: str | None, api_key: str, cache: dict) -> tuple[float, float] | None:
    for address, addr_type in ((road_addr, "road"), (addr, "parcel")):
        if not address or not isinstance(address, str) or not address.strip():
            continue
        if address in cache:
            cached = cache[address]
            return (cached["lat"], cached["lon"]) if cached else None

        try:
            result = _geocode_one(address, addr_type, api_key)
        except Exception as exc:  # noqa: BLE001
            print(f"  [오류] '{address}' 지오코딩 실패: {exc}", file=sys.stderr)
            result = None

        cache[address] = {"lat": result[0], "lon": result[1]} if result else None
        time.sleep(REQUEST_DELAY_SEC)
        if result:
            return result
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", default=None, help="시군구 이름 부분일치 필터 (예: 강남구). 생략 시 전체")
    parser.add_argument("--force", action="store_true", help="캐시 무시하고 다시 지오코딩")
    args = parser.parse_args()

    complex_master_path = DATA_PROCESSED_DIR / "complex_master.parquet"
    if not complex_master_path.exists():
        print("complex_master.parquet가 없습니다. 먼저 scripts/fetch_complex_master.py를 실행하세요.", file=sys.stderr)
        sys.exit(1)

    cm = pd.read_parquet(complex_master_path)
    if args.region:
        cm = cm[cm["sigungu"].str.contains(args.region) | cm["sido"].str.contains(args.region)]
    if cm.empty:
        print(f"'{args.region}'에 해당하는 단지가 없습니다.", file=sys.stderr)
        sys.exit(1)

    ensure_dirs(DATA_RAW_DIR, DATA_PROCESSED_DIR)
    api_key = get_api_key("VWORLD_KEY")
    cache = {} if args.force else _load_cache()

    rows: list[dict] = []
    total = len(cm)
    try:
        for i, row in enumerate(cm.itertuples(index=False), start=1):
            latlon = geocode_address(getattr(row, "addr", None), getattr(row, "road_addr", None), api_key, cache)
            rows.append(
                {
                    "kapt_code": row.kapt_code,
                    "apt_name": row.apt_name,
                    "sigungu": row.sigungu,
                    "lat": latlon[0] if latlon else None,
                    "lon": latlon[1] if latlon else None,
                }
            )
            if i % 100 == 0 or i == total:
                print(f"{i}/{total} 처리 ({sum(1 for r in rows if r['lat'] is not None)}건 성공)")
                _save_cache(cache)  # 중간중간 저장해서 중단돼도 이어할 수 있게
                _save(rows, args.force)  # 중단되면 parquet 저장이 아예 안 되는 문제 방지
    finally:
        _save_cache(cache)
        _save(rows, args.force)


def _save(rows: list[dict], force: bool) -> None:
    if not rows:
        return
    out = pd.DataFrame(rows)
    out_path = DATA_PROCESSED_DIR / "complex_coords.parquet"
    if out_path.exists() and not force:
        existing = pd.read_parquet(out_path)
        out = pd.concat([existing, out], ignore_index=True).drop_duplicates(subset=["kapt_code"], keep="last")
    out.to_parquet(out_path, index=False)

    success = out["lat"].notna().sum()
    print(f"  (저장됨: {success:,}/{len(out):,}개 단지 좌표 확보) -> {out_path}")


if __name__ == "__main__":
    main()
