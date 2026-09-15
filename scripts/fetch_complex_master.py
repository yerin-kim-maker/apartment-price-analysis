"""
국토교통부 공동주택 단지 목록/기본정보 API 수집 스크립트 (세대수, 준공년도 등).

사용 예:
  # 강남구만 (파일럿 테스트용, 빠름)
  python scripts/fetch_complex_master.py --region 강남구

  # 서울+경기 전체
  python scripts/fetch_complex_master.py

원본 JSON 응답은 data/raw/complex/ 아래에 캐싱되어, 재실행해도 이미 받은 단지는
다시 호출하지 않는다. 강제로 다시 받으려면 --force 옵션을 쓴다.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

from common import DATA_PROCESSED_DIR, DATA_RAW_DIR, ensure_dirs, get_api_key, get_with_retry, load_lawd_codes

LIST_URL = "http://apis.data.go.kr/1613000/AptListService4/getSigunguAptList4"
BASIS_URL = "http://apis.data.go.kr/1613000/AptBasisInfoServiceV5/getAphusBassInfoV5"

RAW_COMPLEX_DIR = DATA_RAW_DIR / "complex"
LIST_CACHE_DIR = RAW_COMPLEX_DIR / "list"
BASIS_CACHE_DIR = RAW_COMPLEX_DIR / "basis"
REQUEST_DELAY_SEC = 0.3
NUM_OF_ROWS = 500
SUCCESS_CODES = {"00", "000"}


def _get_json(url: str, params: dict) -> dict:
    resp = get_with_retry(url, params, timeout=30)
    return resp.json()


def fetch_complex_list(lawd_cd: str, service_key: str, force: bool) -> list[dict]:
    cache_file = LIST_CACHE_DIR / f"{lawd_cd}.json"
    if cache_file.exists() and not force:
        import json

        return json.loads(cache_file.read_text(encoding="utf-8"))

    all_items: list[dict] = []
    page_no = 1
    while True:
        data = _get_json(
            LIST_URL,
            {"serviceKey": service_key, "sigunguCode": lawd_cd, "pageNo": page_no, "numOfRows": NUM_OF_ROWS},
        )
        header = data.get("response", {}).get("header", {})
        if header.get("resultCode") not in SUCCESS_CODES:
            print(f"  [경고] 단지목록 {lawd_cd} page{page_no}: {header}", file=sys.stderr)
            break

        body = data.get("response", {}).get("body", {})
        items = body.get("items") or []
        if isinstance(items, dict):  # 결과가 1건이면 items가 dict로 올 때가 있음
            items = [items]
        all_items.extend(items)

        total_count = int(body.get("totalCount", 0))
        if page_no * NUM_OF_ROWS >= total_count or not items:
            break
        page_no += 1
        time.sleep(REQUEST_DELAY_SEC)

    import json

    cache_file.write_text(json.dumps(all_items, ensure_ascii=False), encoding="utf-8")
    return all_items


def fetch_complex_basis(kapt_code: str, service_key: str, force: bool) -> dict | None:
    cache_file = BASIS_CACHE_DIR / f"{kapt_code}.json"
    if cache_file.exists() and not force:
        import json

        return json.loads(cache_file.read_text(encoding="utf-8"))

    data = _get_json(BASIS_URL, {"serviceKey": service_key, "kaptCode": kapt_code})
    time.sleep(REQUEST_DELAY_SEC)  # 캐시 히트일 땐(위에서 이미 return) 이 대기가 필요 없다
    header = data.get("response", {}).get("header", {})
    if header.get("resultCode") not in SUCCESS_CODES:
        print(f"  [경고] 단지기본정보 {kapt_code}: {header}", file=sys.stderr)
        return None

    item = data.get("response", {}).get("body", {}).get("item")
    if not item:
        return None

    import json

    cache_file.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
    return item


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", default=None, help="시도/시군구 이름 부분일치 필터 (예: 강남구). 생략 시 전체")
    parser.add_argument("--force", action="store_true", help="캐시 무시하고 다시 API 호출")
    args = parser.parse_args()

    ensure_dirs(LIST_CACHE_DIR, BASIS_CACHE_DIR, DATA_PROCESSED_DIR)
    service_key = get_api_key("DATA_GO_KR_KEY")

    lawd_codes = load_lawd_codes(args.region)
    if lawd_codes.empty:
        print(f"'{args.region}'에 해당하는 지역을 찾지 못했습니다.", file=sys.stderr)
        sys.exit(1)

    print(f"대상 지역 {len(lawd_codes)}개: 단지 목록 수집 중...")
    all_list_rows: list[dict] = []
    for _, region in lawd_codes.iterrows():
        items = fetch_complex_list(region["lawd_cd"], service_key, args.force)
        for it in items:
            it["_sido"] = region["sido"]
            it["_sigungu"] = region["sigungu"]
        all_list_rows.extend(items)
        print(f"  {region['sigungu']}: {len(items)}개 단지")

    if not all_list_rows:
        print("수집된 단지가 없습니다.", file=sys.stderr)
        sys.exit(1)

    list_df = pd.DataFrame(all_list_rows).drop_duplicates(subset=["kaptCode"])
    print(f"\n전체 단지 {len(list_df):,}개. 단지별 세대수/기본정보 수집 중... (시간이 걸립니다)")

    basis_rows: list[dict] = []
    total = len(list_df)
    try:
        for i, kapt_code in enumerate(list_df["kaptCode"], start=1):
            item = fetch_complex_basis(kapt_code, service_key, args.force)
            if item:
                basis_rows.append(item)
            if i % 200 == 0 or i == total:
                print(f"  {i}/{total} 완료")
                _save(list_df, basis_rows, args.force)  # 중간 저장 — API 한도 등으로 죽어도 여기까지는 안 날아감
    finally:
        # 루프 도중 예외(429 한도초과 등)가 나도 지금까지 모은 건 반드시 저장한다.
        _save(list_df, basis_rows, args.force)


def _save(list_df: pd.DataFrame, basis_rows: list[dict], force: bool) -> None:
    # 주의: basis_df를 왼쪽에 두고 merge한다 — list_df를 왼쪽에 두면 "이번 실행에서 아직 못 받은
    # 나머지 전체 단지"까지 NaN 행으로 딸려나와서, 기존에 저장돼 있던 다른 실행분의 데이터를
    # drop_duplicates(keep="last")가 NaN으로 덮어써버리는 사고가 난다(실제로 한 번 겪었음).
    basis_df = pd.DataFrame(basis_rows)
    if basis_df.empty:
        print("  저장할 신규 데이터가 없어 건너뜁니다.")
        return
    merged = basis_df.merge(list_df, on="kaptCode", how="left", suffixes=("", "_list"))

    out = pd.DataFrame()
    out["kapt_code"] = merged["kaptCode"]
    out["apt_name"] = merged["kaptName"]
    out["sido"] = merged["_sido"]
    out["sigungu"] = merged["_sigungu"]
    out["dong"] = merged.get("as3")
    out["addr"] = merged.get("kaptAddr")
    out["road_addr"] = merged.get("doroJuso")
    out["household_count"] = pd.to_numeric(merged.get("kaptdaCnt"), errors="coerce")
    out["building_count"] = pd.to_numeric(merged.get("kaptDongCnt"), errors="coerce")
    out["top_floor"] = pd.to_numeric(merged.get("kaptTopFloor"), errors="coerce")
    out["use_approval_ymd"] = merged.get("kaptUsedate")
    out["sale_type"] = merged.get("codeSaleNm")
    out["heat_type"] = merged.get("codeHeatNm")
    out["mgmt_type"] = merged.get("codeMgrNm")
    out["apt_type"] = merged.get("codeAptNm")  # 국토부 정식 분류: 아파트/주상복합/연립주택 등
    out["hall_type"] = merged.get("codeHallNm")  # 복도식/계단식

    out_path = DATA_PROCESSED_DIR / "complex_master.parquet"
    if out_path.exists() and not force:
        existing = pd.read_parquet(out_path)
        out = pd.concat([existing, out], ignore_index=True).drop_duplicates(subset=["kapt_code"], keep="last")

    out.to_parquet(out_path, index=False)
    print(f"  (저장됨: {len(out):,}개 단지, 세대수 있음 {out['household_count'].notna().sum():,}개) -> {out_path}")


if __name__ == "__main__":
    main()
