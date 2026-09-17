"""
실거래가 + 세대수 + 배정학교 + 단지 좌표 + 역세권을 미리 한 번에 결합해서
data/processed/analysis_dataset.parquet 로 저장해둔다.

대시보드(Home.py)는 이 파일이 있으면 매번 다시 계산하지 않고 그냥 읽기만 해서 훨씬 빨리 뜬다.
단지명 매칭처럼 시간이 걸리는 계산을 여기서 미리 한 번만 하는 것.

**아래 스크립트들로 원본 데이터를 새로 받거나 갱신할 때마다 이 스크립트도 다시 실행해야
대시보드에 최신 데이터가 반영된다**:
  fetch_transactions.py, fetch_complex_master.py, geocode_complexes.py,
  match_assigned_schools.py, parse_subway_stations.py, compute_walk_time.py 등

실행:
  python scripts/build_dataset.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from utils.data_loader import ANALYSIS_DATASET_PATH, _compute_transactions_enriched  # noqa: E402

# app/Home.py가 실제로 안 쓰는 컬럼들 — 원본 파이프라인 중간 단계에선 필요하지만
# 최종 결합 결과에는 그냥 죽은 용량이라 배포용 파일에서는 뺀다.
UNUSED_COLS = [
    # price_per_pyeong_10k는 빠진 것처럼 보이지만 flag_price_outliers()가 로딩 후에도 쓴다 — 빼면 안 됨.
    "apt_type", "building_count", "cancelled", "deal_amount_10k", "deal_day", "deal_month",
    "deal_year", "elementary_distance_m", "jibun", "lawd_cd", "middle_match_type",
    "monthly_rent_10k", "price_10k", "road_name", "use_approval_ymd",
]


def _optimize_for_deploy(df):
    """Streamlit Community Cloud 무료 플랜 메모리 한도(약 1GB)에 맞추기 위한 최적화.
    원본 그대로면 pandas에 올렸을 때 2GB 가까이 먹어서 배포 환경에서 메모리 부족으로 죽는다
    (문자열 컬럼이 90% 이상 차지 — 행마다 파이썬 str 객체를 따로 들고 있어서 그렇다).
    실제 쓰지 않는 컬럼을 빼고, 반복되는 문자열은 category로, 실수/정수는 더 작은 타입으로
    바꿔서 약 260MB 수준까지 줄인다 (그래도 정보 손실은 없음 — 표현 방식만 바뀜)."""
    df = df.drop(columns=[c for c in UNUSED_COLS if c in df.columns])

    for c in df.select_dtypes(include=["object"]).columns:
        df[c] = df[c].astype("category")

    import pandas as pd

    for c in df.select_dtypes(include=["float64"]).columns:
        df[c] = df[c].astype("float32")
    for c in df.select_dtypes(include=["int64"]).columns:
        df[c] = pd.to_numeric(df[c], downcast="integer")

    return df


def main() -> None:
    print("실거래가 + 세대수 + 배정학교 + 좌표 + 역세권 결합 중... (시간이 좀 걸립니다)")
    df = _compute_transactions_enriched()
    if df.empty:
        print("결합할 데이터가 없습니다. 먼저 fetch_transactions.py 등으로 원본 데이터를 받아주세요.", file=sys.stderr)
        sys.exit(1)

    df = _optimize_for_deploy(df)
    df.to_parquet(ANALYSIS_DATASET_PATH, index=False, compression="zstd")
    print(f"완료: {len(df):,}행 -> {ANALYSIS_DATASET_PATH}")
    print("이제 대시보드를 (다시) 실행하면 이 파일을 바로 읽어서 훨씬 빨리 뜹니다.")


if __name__ == "__main__":
    main()
