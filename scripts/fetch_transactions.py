"""
국토교통부 아파트 매매/전월세 실거래가 API 수집 스크립트.

사용 예:
  # 강남구, 최근 12개월만 (파일럿 테스트용, 빠름)
  python scripts/fetch_transactions.py --region 강남구 --months 12

  # 서울+경기 전체, 최근 5년치 (본 수집, 시간 오래 걸림)
  python scripts/fetch_transactions.py --months 60

원본 XML 응답은 data/raw/transactions/ 아래에 캐싱되어, 같은 (지역,연월,매매/전월세)
조합은 재실행해도 다시 호출하지 않는다. 강제로 다시 받으려면 --force 옵션을 쓰거나
해당 캐시 파일을 지우면 된다.
"""
from __future__ import annotations

import argparse
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

from common import (
    DATA_PROCESSED_DIR,
    DATA_RAW_DIR,
    default_end_ym,
    default_start_ym,
    ensure_dirs,
    get_api_key,
    get_with_retry,
    load_lawd_codes,
    month_range,
)

SALE_URL = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
RENT_URL = "http://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"

RAW_TX_DIR = DATA_RAW_DIR / "transactions"
REQUEST_DELAY_SEC = 0.15
NUM_OF_ROWS = 1000
# data.go.kr 응답의 resultCode는 API마다 자릿수가 다르게 온다 (실측: "000"/"OK" = 정상, "03"류 = 자료없음).
SUCCESS_CODES = {"00", "000"}
NODATA_CODES = {"03", "030", "04", "040"}


def _item_to_dict(item: ET.Element) -> dict:
    return {child.tag: (child.text or "").strip() for child in item}


def _call_api(url: str, service_key: str, lawd_cd: str, deal_ymd: str, page_no: int) -> ET.Element:
    params = {
        "serviceKey": service_key,
        "LAWD_CD": lawd_cd,
        "DEAL_YMD": deal_ymd,
        "numOfRows": NUM_OF_ROWS,
        "pageNo": page_no,
    }
    resp = get_with_retry(url, params, timeout=30)
    return ET.fromstring(resp.content)


def _fetch_one(url: str, service_key: str, lawd_cd: str, deal_ymd: str, cache_subdir: Path, force: bool) -> list[dict]:
    cache_file = cache_subdir / f"{lawd_cd}_{deal_ymd}.xml"
    if cache_file.exists() and not force:
        root = ET.fromstring(cache_file.read_bytes())
        result_code = root.findtext("./header/resultCode")
        if result_code not in SUCCESS_CODES:
            return []
        items = root.findall("./body/items/item")
        return [_item_to_dict(it) for it in items]

    all_items: list[dict] = []
    page_no = 1
    first_root_bytes: bytes | None = None
    while True:
        params_root = _call_api(url, service_key, lawd_cd, deal_ymd, page_no)
        if page_no == 1:
            first_root_bytes = ET.tostring(params_root, encoding="utf-8")
        result_code = params_root.findtext("./header/resultCode")
        result_msg = params_root.findtext("./header/resultMsg")
        if result_code not in SUCCESS_CODES:
            if result_code not in NODATA_CODES:
                print(f"  [경고] {lawd_cd} {deal_ymd} page{page_no}: {result_code} {result_msg}", file=sys.stderr)
            break

        items = params_root.findall("./body/items/item")
        all_items.extend(_item_to_dict(it) for it in items)

        total_count = int(params_root.findtext("./body/totalCount") or 0)
        if page_no * NUM_OF_ROWS >= total_count:
            break
        page_no += 1
        time.sleep(REQUEST_DELAY_SEC)

    if first_root_bytes is not None:
        cache_file.write_bytes(first_root_bytes)
    return all_items


def _to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", "").str.strip(), errors="coerce")


def fetch_sale(lawd_codes: pd.DataFrame, ymds: list[str], service_key: str, force: bool) -> pd.DataFrame:
    cache_dir = RAW_TX_DIR / "sale"
    ensure_dirs(cache_dir)
    rows: list[dict] = []
    total = len(lawd_codes) * len(ymds)
    done = 0
    for _, region in lawd_codes.iterrows():
        for ymd in ymds:
            done += 1
            items = _fetch_one(SALE_URL, service_key, region["lawd_cd"], ymd, cache_dir, force)
            for it in items:
                it["_sido"] = region["sido"]
                it["_sigungu"] = region["sigungu"]
                it["_lawd_cd"] = region["lawd_cd"]
                it["_deal_ymd"] = ymd
            rows.extend(items)
            if done % 20 == 0 or done == total:
                print(f"[매매] {done}/{total} ({region['sigungu']} {ymd}) 누적 {len(rows)}건")
            time.sleep(REQUEST_DELAY_SEC)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    out = pd.DataFrame()
    out["sido"] = df["_sido"]
    out["sigungu"] = df["_sigungu"]
    out["lawd_cd"] = df["_lawd_cd"]
    out["apt_name"] = df.get("aptNm")
    out["dong"] = df.get("umdNm")
    out["jibun"] = df.get("jibun")
    out["road_name"] = df.get("roadNm")
    out["area_exclusive_m2"] = _to_num(df.get("excluUseAr", pd.Series(dtype=str)))
    out["floor"] = _to_num(df.get("floor", pd.Series(dtype=str)))
    out["build_year"] = _to_num(df.get("buildYear", pd.Series(dtype=str)))
    out["deal_year"] = _to_num(df.get("dealYear", pd.Series(dtype=str)))
    out["deal_month"] = _to_num(df.get("dealMonth", pd.Series(dtype=str)))
    out["deal_day"] = _to_num(df.get("dealDay", pd.Series(dtype=str)))
    out["deal_amount_10k"] = _to_num(df.get("dealAmount", pd.Series(dtype=str)))
    out["cancelled"] = df.get("cdealType").fillna("") == "해제" if "cdealType" in df.columns else False
    out["dealing_type"] = df.get("dealingGbn") if "dealingGbn" in df.columns else None
    out["deal_type"] = "매매"
    return out


def fetch_rent(lawd_codes: pd.DataFrame, ymds: list[str], service_key: str, force: bool) -> pd.DataFrame:
    cache_dir = RAW_TX_DIR / "rent"
    ensure_dirs(cache_dir)
    rows: list[dict] = []
    total = len(lawd_codes) * len(ymds)
    done = 0
    for _, region in lawd_codes.iterrows():
        for ymd in ymds:
            done += 1
            items = _fetch_one(RENT_URL, service_key, region["lawd_cd"], ymd, cache_dir, force)
            for it in items:
                it["_sido"] = region["sido"]
                it["_sigungu"] = region["sigungu"]
                it["_lawd_cd"] = region["lawd_cd"]
                it["_deal_ymd"] = ymd
            rows.extend(items)
            if done % 20 == 0 or done == total:
                print(f"[전월세] {done}/{total} ({region['sigungu']} {ymd}) 누적 {len(rows)}건")
            time.sleep(REQUEST_DELAY_SEC)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    out = pd.DataFrame()
    out["sido"] = df["_sido"]
    out["sigungu"] = df["_sigungu"]
    out["lawd_cd"] = df["_lawd_cd"]
    out["apt_name"] = df.get("aptNm")
    out["dong"] = df.get("umdNm")
    out["jibun"] = df.get("jibun")
    out["road_name"] = df.get("roadNm") if "roadNm" in df.columns else None
    out["area_exclusive_m2"] = _to_num(df.get("excluUseAr", pd.Series(dtype=str)))
    out["floor"] = _to_num(df.get("floor", pd.Series(dtype=str)))
    out["build_year"] = _to_num(df.get("buildYear", pd.Series(dtype=str)))
    out["deal_year"] = _to_num(df.get("dealYear", pd.Series(dtype=str)))
    out["deal_month"] = _to_num(df.get("dealMonth", pd.Series(dtype=str)))
    out["deal_day"] = _to_num(df.get("dealDay", pd.Series(dtype=str)))
    deposit = _to_num(df.get("deposit", pd.Series(dtype=str)))
    monthly_rent = _to_num(df.get("monthlyRent", pd.Series(dtype=str)))
    out["deal_amount_10k"] = deposit
    out["monthly_rent_10k"] = monthly_rent
    out["cancelled"] = False
    out["deal_type"] = monthly_rent.fillna(0).gt(0).map({True: "월세", False: "전세"})
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", default=None, help="시도/시군구 이름 부분일치 필터 (예: 강남구). 생략 시 전체 56개 시군구")
    parser.add_argument("--start-ym", default=None, help="시작 연월 YYYYMM (예: 202101)")
    parser.add_argument("--end-ym", default=None, help="종료 연월 YYYYMM. 생략 시 이번달")
    parser.add_argument("--months", type=int, default=None, help="종료월 기준 최근 N개월 (start/end-ym 대신 간단히 사용)")
    parser.add_argument("--force", action="store_true", help="캐시 무시하고 다시 API 호출")
    parser.add_argument("--skip-rent", action="store_true", help="전월세는 수집하지 않고 매매만")
    args = parser.parse_args()

    end_ym = args.end_ym or default_end_ym()
    if args.months:
        end_period = pd.Period(end_ym, freq="M")
        start_period = end_period - (args.months - 1)
        start_ym = str(start_period).replace("-", "")
    else:
        start_ym = args.start_ym or default_start_ym()

    ymds = month_range(start_ym, end_ym)
    lawd_codes = load_lawd_codes(args.region)
    if lawd_codes.empty:
        print(f"'{args.region}'에 해당하는 지역을 찾지 못했습니다. config/lawd_codes.csv를 확인하세요.", file=sys.stderr)
        sys.exit(1)

    print(f"대상 지역 {len(lawd_codes)}개, 기간 {start_ym}~{end_ym} ({len(ymds)}개월)")
    service_key = get_api_key("DATA_GO_KR_KEY")

    ensure_dirs(DATA_PROCESSED_DIR)

    sale_df = fetch_sale(lawd_codes, ymds, service_key, args.force)
    frames = [sale_df] if not sale_df.empty else []

    if not args.skip_rent:
        rent_df = fetch_rent(lawd_codes, ymds, service_key, args.force)
        if not rent_df.empty:
            frames.append(rent_df)

    if not frames:
        print("수집된 거래가 없습니다.", file=sys.stderr)
        sys.exit(1)

    combined = pd.concat(frames, ignore_index=True)
    combined["deal_date"] = pd.to_datetime(
        dict(year=combined["deal_year"], month=combined["deal_month"], day=combined["deal_day"]),
        errors="coerce",
    )

    out_path = DATA_PROCESSED_DIR / "transactions.parquet"
    if out_path.exists() and not args.force:
        existing = pd.read_parquet(out_path)
        combined = pd.concat([existing, combined], ignore_index=True)
        dedup_cols = [
            c
            for c in (
                "lawd_cd", "apt_name", "dong", "jibun", "area_exclusive_m2",
                "floor", "deal_date", "deal_amount_10k", "deal_type", "monthly_rent_10k",
            )
            if c in combined.columns
        ]
        combined = combined.drop_duplicates(subset=dedup_cols, keep="last")

    combined.to_parquet(out_path, index=False)
    print(f"완료: {len(combined):,}건 -> {out_path}")


if __name__ == "__main__":
    main()
