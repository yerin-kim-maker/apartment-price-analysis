"""
단지 좌표(complex_coords.parquet)와 지하철역 좌표(subway_stations.parquet)로
각 단지의 최근접 역과 도보시간을 계산.

도보시간은 분속 80m로 가정한 직선거리 기준 추정치다 (실제 도로를 따라가는 경로가 아니라
참고용 근사치).

사전 준비: scripts/geocode_complexes.py, scripts/parse_subway_stations.py를 먼저 실행.

실행:
  python scripts/compute_walk_time.py
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from common import DATA_PROCESSED_DIR

WALK_SPEED_M_PER_MIN = 80


def nearest_station_batch(complex_lat: np.ndarray, complex_lon: np.ndarray, station_lat: np.ndarray, station_lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """각 단지에 대해 가장 가까운 역의 인덱스와 거리(m)를 벡터화된 haversine으로 계산."""
    r = 6371000
    lat1 = np.radians(complex_lat)[:, None]
    lon1 = np.radians(complex_lon)[:, None]
    lat2 = np.radians(station_lat)[None, :]
    lon2 = np.radians(station_lon)[None, :]

    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    dist = 2 * r * np.arcsin(np.sqrt(a))  # shape: (n_complex, n_station)

    nearest_idx = np.argmin(dist, axis=1)
    nearest_dist = dist[np.arange(len(complex_lat)), nearest_idx]
    return nearest_idx, nearest_dist


def main() -> None:
    coords_path = DATA_PROCESSED_DIR / "complex_coords.parquet"
    stations_path = DATA_PROCESSED_DIR / "subway_stations.parquet"

    if not coords_path.exists():
        print("complex_coords.parquet가 없습니다. 먼저 scripts/geocode_complexes.py를 실행하세요.", file=sys.stderr)
        sys.exit(1)
    if not stations_path.exists():
        print("subway_stations.parquet가 없습니다. 먼저 scripts/parse_subway_stations.py를 실행하세요.", file=sys.stderr)
        sys.exit(1)

    coords = pd.read_parquet(coords_path).dropna(subset=["lat", "lon"])
    stations = pd.read_parquet(stations_path).dropna(subset=["lat", "lon"])

    print(f"단지 {len(coords):,}개 x 역사 {len(stations):,}개 최근접 거리 계산 중...")

    # 메모리 절약을 위해 청크 단위로 처리 (단지 수가 많아도 안전하게)
    CHUNK = 2000
    idxs = []
    dists = []
    for start in range(0, len(coords), CHUNK):
        chunk = coords.iloc[start : start + CHUNK]
        idx, dist = nearest_station_batch(
            chunk["lat"].to_numpy(), chunk["lon"].to_numpy(),
            stations["lat"].to_numpy(), stations["lon"].to_numpy(),
        )
        idxs.append(idx)
        dists.append(dist)
        print(f"  {min(start + CHUNK, len(coords)):,}/{len(coords):,}")

    nearest_idx = np.concatenate(idxs)
    nearest_dist = np.concatenate(dists)

    out = coords[["kapt_code", "apt_name", "sigungu"]].copy()
    out["nearest_station"] = stations["station_name"].to_numpy()[nearest_idx]
    out["nearest_station_line"] = stations["line_name"].to_numpy()[nearest_idx]
    out["distance_m"] = nearest_dist.round(0)
    out["walk_minutes"] = (nearest_dist / WALK_SPEED_M_PER_MIN).round(1)

    out_path = DATA_PROCESSED_DIR / "complex_subway.parquet"
    out.to_parquet(out_path, index=False)
    print(f"\n완료: {len(out):,}개 단지 -> {out_path}")
    print(f"평균 도보시간: {out['walk_minutes'].mean():.1f}분, 중앙값: {out['walk_minutes'].median():.1f}분")


if __name__ == "__main__":
    main()
