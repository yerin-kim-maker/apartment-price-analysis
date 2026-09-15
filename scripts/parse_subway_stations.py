"""
data.go.kr에서 받은 "전국도시철도역사정보표준데이터" 파일을 파싱.

이 데이터셋은 API보다 파일 다운로드가 더 확실해서, 아래에서 직접 받아
data/raw/subway/ 폴더에 넣어야 한다 (CSV든 XLSX든 상관없음, 이 폴더에 하나만 있으면 됨):
  https://www.data.go.kr 에서 "전국도시철도역사정보표준데이터" 검색 -> 다운로드

실행:
  python scripts/parse_subway_stations.py

실제 필드명은 받아봐야 확정 가능해서, 흔한 필드명 후보로 자동 인식을 시도하고
확신이 없으면 실제 컬럼 목록을 출력해서 확인할 수 있게 한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from common import DATA_PROCESSED_DIR, DATA_RAW_DIR, ensure_dirs

SUBWAY_DIR = DATA_RAW_DIR / "subway"

NAME_CANDIDATES = ["역사명", "station_name", "STATN_NM", "역사명칭"]
LINE_CANDIDATES = ["노선명", "line_name", "ROUTE_NM", "LN_NM"]
LAT_CANDIDATES = ["역위도", "위도", "LAT", "lat", "Y좌표", "YCRD"]
LON_CANDIDATES = ["역경도", "경도", "LOT", "LON", "lon", "X좌표", "XCRD"]
TRANSFER_CANDIDATES = ["환승역구분", "TRNSIT_YN", "환승역여부"]


def _find_col(columns: list[str], candidates: list[str]) -> str | None:
    for c in candidates:
        if c in columns:
            return c
    lower_map = {c.lower(): c for c in columns}
    for c in candidates:
        if c.lower() in lower_map:
            return lower_map[c.lower()]
    return None


def _load_any(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(path)
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"{path.name} 인코딩을 인식하지 못했습니다.")


def main() -> None:
    ensure_dirs(SUBWAY_DIR, DATA_PROCESSED_DIR)

    candidates = [p for p in SUBWAY_DIR.glob("*") if p.suffix.lower() in (".csv", ".xlsx", ".xls")]
    if not candidates:
        print(
            f"{SUBWAY_DIR} 폴더에 파일이 없습니다.\n"
            "data.go.kr에서 '전국도시철도역사정보표준데이터'를 검색해 다운로드한 뒤 이 폴더에 넣어주세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    src = candidates[0]
    print(f"파싱 중: {src.name}")
    df = _load_any(src)
    columns = list(df.columns)
    print(f"필드 목록: {columns}")

    name_col = _find_col(columns, NAME_CANDIDATES)
    line_col = _find_col(columns, LINE_CANDIDATES)
    lat_col = _find_col(columns, LAT_CANDIDATES)
    lon_col = _find_col(columns, LON_CANDIDATES)
    transfer_col = _find_col(columns, TRANSFER_CANDIDATES)

    missing = [n for n, c in [("역사명", name_col), ("위도", lat_col), ("경도", lon_col)] if c is None]
    if missing:
        print(
            f"[오류] 다음 필드를 자동으로 못 찾았습니다: {missing}. "
            f"실제 필드 목록({columns})을 보고 스크립트의 *_CANDIDATES에 정확한 이름을 추가해주세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    out = pd.DataFrame()
    out["station_name"] = df[name_col]
    out["line_name"] = df[line_col] if line_col else None
    out["lat"] = pd.to_numeric(df[lat_col], errors="coerce")
    out["lon"] = pd.to_numeric(df[lon_col], errors="coerce")
    out["is_transfer"] = df[transfer_col] if transfer_col else None
    out = out.dropna(subset=["lat", "lon"])

    out_path = DATA_PROCESSED_DIR / "subway_stations.parquet"
    out.to_parquet(out_path, index=False)
    print(f"완료: {len(out):,}개 역사 -> {out_path}")


if __name__ == "__main__":
    main()
