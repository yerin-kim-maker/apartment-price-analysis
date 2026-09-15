"""
학구도안내서비스(schoolzone.emac.kr)에서 내려받은 학교 위치/통학구역 파일을 파싱.

이 사이트는 자바스크립트 기반이라 자동 다운로드가 안 돼서, 아래 파일들을 직접
https://schoolzone.emac.kr/publicData/publicDataList.do 에서 받아 압축을 풀고
data/raw/school_zone/ 폴더에 넣어줘야 한다 (파일명이 달라도 되고, 안의 .shp/.dbf/.shx/.prj
세트만 아래 이름으로 맞추면 됨):

  data/raw/school_zone/
    school_locations.csv     <- "초중등학교위치" 안의 csv
    elementary_zones.shp/.dbf/.shx/.prj  <- "초등학교통학구역" 안의 shp세트
    middle_zones.shp/.dbf/.shx/.prj      <- "중학교학교군" 안의 shp세트

실행:
  python scripts/parse_school_zones.py

실제 파일 구조 확인 결과:
  - school_locations.csv: UTF-8, 컬럼에 위도/경도가 이미 포함되어 있어 지오코딩이 필요 없음.
  - 통학구역 shp: EUC-KR 인코딩, 학교명은 HAKGUDO_NM 필드에 "OO초통학구역"/"OO중학구" 처럼
    들어있음. 초등학교는 학구당 학교 1곳이라 이름이 깔끔하지만, 중학교는 공동학군이 흔해서
    "고북중해미중공동중학구"처럼 여러 학교명이 붙어있는 경우가 많다 — 이건 정확한 개별
    학교명 목록이 아니라 "학구/학군 이름표"로 취급한다 (화면에도 그렇게 안내함).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
import shapefile  # pyshp
from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform as shapely_transform

from common import DATA_PROCESSED_DIR, DATA_RAW_DIR, ensure_dirs

ZONE_DIR = DATA_RAW_DIR / "school_zone"
SHP_ENCODING = "euc-kr"
CSV_ENCODING = "utf-8"


def parse_school_locations() -> pd.DataFrame:
    csv_path = ZONE_DIR / "school_locations.csv"
    if not csv_path.exists():
        print("  school_locations.csv 없음 — 건너뜀")
        return pd.DataFrame()

    df = pd.read_csv(csv_path, encoding=CSV_ENCODING)
    out = pd.DataFrame()
    out["school_id"] = df.get("학교ID")
    out["school_name"] = df.get("학교명")
    out["school_level"] = df.get("학교급구분")
    out["addr"] = df.get("소재지지번주소")
    out["road_addr"] = df.get("소재지도로명주소")
    out["lat"] = pd.to_numeric(df.get("위도"), errors="coerce")
    out["lon"] = pd.to_numeric(df.get("경도"), errors="coerce")
    out = out.dropna(subset=["lat", "lon"])
    return out


def _read_zone_shp(shp_path: Path) -> tuple[list[str], list]:
    """(HAKGUDO_NM 목록, WGS84로 변환된 shapely geometry 목록) 반환."""
    if not shp_path.exists():
        return [], []

    reader = shapefile.Reader(str(shp_path), encoding=SHP_ENCODING)
    field_names = [f[0] for f in reader.fields[1:]]
    if "HAKGUDO_NM" not in field_names:
        print(f"  [경고] {shp_path.name}에 HAKGUDO_NM 필드가 없습니다. 실제 필드: {field_names}", file=sys.stderr)
        return [], []

    prj_path = shp_path.with_suffix(".prj")
    transformer = None
    if prj_path.exists():
        try:
            src_crs = CRS.from_wkt(prj_path.read_text(encoding="utf-8", errors="ignore"))
            if src_crs.to_epsg() != 4326:
                transformer = Transformer.from_crs(src_crs, CRS.from_epsg(4326), always_xy=True).transform
        except Exception as exc:  # noqa: BLE001
            print(f"  [경고] {prj_path.name} 좌표계 해석 실패: {exc}", file=sys.stderr)

    names: list[str] = []
    geoms: list = []
    for sr in reader.iterShapeRecords():
        name = sr.record["HAKGUDO_NM"]
        geo = shape(sr.shape.__geo_interface__)
        if transformer is not None:
            geo = shapely_transform(transformer, geo)
        names.append(name)
        geoms.append(geo)
    return names, geoms


def parse_elementary_zones() -> pd.DataFrame:
    names, geoms = _read_zone_shp(ZONE_DIR / "elementary_zones.shp")
    if not names:
        print("  elementary_zones.shp 없음 — 건너뜀")
        return pd.DataFrame()
    clean_names = [re.sub(r"통학구역$", "", n).strip() for n in names]
    return pd.DataFrame({"school_name_raw": clean_names, "geometry_wkt": [g.wkt for g in geoms]})


def parse_middle_zones() -> pd.DataFrame:
    names, geoms = _read_zone_shp(ZONE_DIR / "middle_zones.shp")
    if not names:
        print("  middle_zones.shp 없음 — 건너뜀")
        return pd.DataFrame()
    # 중학교는 공동학군 표기가 많아 이름을 깔끔히 분리하기 어려움 -> 학구 이름표 그대로 사용
    return pd.DataFrame({"school_name_raw": names, "geometry_wkt": [g.wkt for g in geoms]})


def main() -> None:
    ensure_dirs(ZONE_DIR, DATA_PROCESSED_DIR)

    if not any(ZONE_DIR.glob("*")):
        print(
            f"{ZONE_DIR} 폴더가 비어 있습니다.\n"
            "https://schoolzone.emac.kr/publicData/publicDataList.do 에서 받아 넣어주세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("학교 위치 파싱 중...")
    schools = parse_school_locations()
    if not schools.empty:
        schools.to_parquet(DATA_PROCESSED_DIR / "schools.parquet", index=False)
        print(f"  -> {DATA_PROCESSED_DIR / 'schools.parquet'} ({len(schools):,}건)")

    print("초등학교 통학구역 파싱 중...")
    elem = parse_elementary_zones()
    if not elem.empty:
        elem.to_parquet(DATA_PROCESSED_DIR / "elementary_zones.parquet", index=False)
        print(f"  -> {DATA_PROCESSED_DIR / 'elementary_zones.parquet'} ({len(elem):,}건)")

    print("중학교 학구 파싱 중...")
    mid = parse_middle_zones()
    if not mid.empty:
        mid.to_parquet(DATA_PROCESSED_DIR / "middle_zones.parquet", index=False)
        print(f"  -> {DATA_PROCESSED_DIR / 'middle_zones.parquet'} ({len(mid):,}건)")


if __name__ == "__main__":
    main()
