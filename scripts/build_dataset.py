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


def main() -> None:
    print("실거래가 + 세대수 + 배정학교 + 좌표 + 역세권 결합 중... (시간이 좀 걸립니다)")
    df = _compute_transactions_enriched()
    if df.empty:
        print("결합할 데이터가 없습니다. 먼저 fetch_transactions.py 등으로 원본 데이터를 받아주세요.", file=sys.stderr)
        sys.exit(1)

    df.to_parquet(ANALYSIS_DATASET_PATH, index=False)
    print(f"완료: {len(df):,}행 -> {ANALYSIS_DATASET_PATH}")
    print("이제 대시보드를 (다시) 실행하면 이 파일을 바로 읽어서 훨씬 빨리 뜹니다.")


if __name__ == "__main__":
    main()
