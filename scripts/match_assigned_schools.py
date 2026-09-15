"""
단지 좌표 + 통학구역 폴리곤을 point-in-polygon으로 매칭해서 배정 학교를 정한다.

- 초등학교: 통학구역이 보통 1개 학교로 확정되지만, "공동통학구역"이면 여러 학교가
  겹칠 수 있어 그 경우 후보 전체를 남긴다.
- 중학교: 서울/경기 대부분이 공동학군(추첨 배정)이라 애초에 여러 학교가 후보로
  나오는 게 정상이다 — "배정 확정"이 아니라 "후보 학교 + 근접도"로 취급한다.
- 폴리곤 매칭이 안 되는 단지(통학구역 데이터 밖 등)는 최근접 학교 거리로 대체하고
  match_type에 "최단거리추정"이라고 표시해 정확도가 다름을 구분한다.

사전 준비: scripts/geocode_complexes.py, scripts/parse_school_zones.py를 먼저 실행해야 함.

실행:
  python scripts/match_assigned_schools.py
"""
from __future__ import annotations

import sys

import pandas as pd
from shapely import STRtree
from shapely.geometry import Point
from shapely import wkt as shapely_wkt

from common import DATA_PROCESSED_DIR, haversine_m


def _load_zones(path) -> tuple[list, list[str]]:
    if not path.exists():
        return [], []
    df = pd.read_parquet(path)
    geoms = [shapely_wkt.loads(g) for g in df["geometry_wkt"]]
    names = df["school_name_raw"].tolist()
    return geoms, names


def match_polygon(point: Point, geoms: list, names: list[str]) -> tuple[list[str], str]:
    if not geoms:
        return [], "데이터없음"
    tree = STRtree(geoms)
    # predicate="within": point가 tree 안의 폴리곤 안에 있는지 (point.within(polygon))
    idxs = tree.query(point, predicate="within")
    matched = [names[i] for i in idxs if geoms[i].contains(point)]  # 이중 확인
    if not matched:
        return [], "매칭실패"
    if len(matched) == 1:
        return matched, "단일배정"
    return sorted(set(matched)), "공동학군/중복구역"


def nearest_school(point: Point, schools: pd.DataFrame, level: str) -> tuple[str | None, float | None]:
    cand = schools[schools.get("school_level", pd.Series(dtype=str)) == level] if "school_level" in schools.columns else schools
    if cand.empty or "lat" not in cand.columns:
        return None, None
    best_name, best_dist = None, None
    for _, row in cand.iterrows():
        if pd.isna(row.get("lat")) or pd.isna(row.get("lon")):
            continue
        d = haversine_m(point.y, point.x, row["lat"], row["lon"])
        if best_dist is None or d < best_dist:
            best_dist, best_name = d, row.get("school_name_raw") or row.get("school_name")
    return best_name, best_dist


def distance_to_named_school(point: Point, name: str, schools_by_name: dict[str, pd.DataFrame]) -> float | None:
    """배정된 학교 '이름'까지의 거리. 같은 이름의 학교가 여러 지역에 있을 수 있어(흔치 않지만),
    그 중 이 단지에서 가장 가까운 걸 고른다 — 통학구역으로 이미 배정된 이름이라 대개 그게 맞는 학교다."""
    cand = schools_by_name.get(name)
    if cand is None or cand.empty:
        return None
    dists = [haversine_m(point.y, point.x, r.lat, r.lon) for r in cand.itertuples() if pd.notna(r.lat) and pd.notna(r.lon)]
    return min(dists) if dists else None


def main() -> None:
    coords_path = DATA_PROCESSED_DIR / "complex_coords.parquet"
    if not coords_path.exists():
        print("complex_coords.parquet가 없습니다. 먼저 scripts/geocode_complexes.py를 실행하세요.", file=sys.stderr)
        sys.exit(1)

    coords = pd.read_parquet(coords_path).dropna(subset=["lat", "lon"])
    if coords.empty:
        print("좌표가 확보된 단지가 없습니다.", file=sys.stderr)
        sys.exit(1)

    elem_geoms, elem_names = _load_zones(DATA_PROCESSED_DIR / "elementary_zones.parquet")
    mid_geoms, mid_names = _load_zones(DATA_PROCESSED_DIR / "middle_zones.parquet")

    schools_path = DATA_PROCESSED_DIR / "schools.parquet"
    schools = pd.read_parquet(schools_path) if schools_path.exists() else pd.DataFrame()

    if not elem_geoms and not mid_geoms:
        print(
            "통학구역 데이터가 없습니다. 먼저 scripts/parse_school_zones.py를 실행하세요 "
            "(data/raw/school_zone/ 에 schoolzone.emac.kr에서 받은 파일을 넣어야 합니다).",
            file=sys.stderr,
        )
        sys.exit(1)

    elem_schools = schools[schools.get("school_level") == "초등학교"] if not schools.empty else pd.DataFrame()
    if not elem_schools.empty:
        # 통학구역 쪽 학교명은 "OO초통학구역" -> "OO초"로 줄여져 있는데(parse_school_zones.py),
        # schools.parquet 쪽은 "OO초등학교"처럼 정식 명칭이라 그냥 groupby하면 하나도 안 맞는다.
        # "초등학교"->"초"로 줄인 이름을 키로 써서 양쪽을 맞춘다.
        elem_schools = elem_schools.assign(
            _short_name=elem_schools["school_name"].str.replace("초등학교", "초", regex=False)
        )
        elem_schools_by_name: dict[str, pd.DataFrame] = {
            name: g for name, g in elem_schools.groupby("_short_name")
        }
        # 혹시 통학구역 쪽에 정식 명칭 그대로 나오는 경우도 있을 수 있어 원래 이름도 같이 남겨둔다.
        for name, g in elem_schools.groupby("school_name"):
            elem_schools_by_name.setdefault(name, g)
    else:
        elem_schools_by_name = {}

    rows = []
    total = len(coords)
    for i, row in enumerate(coords.itertuples(index=False), start=1):
        pt = Point(row.lon, row.lat)

        elem_matches, elem_type = match_polygon(pt, elem_geoms, elem_names)
        if not elem_matches:
            name, _ = nearest_school(pt, schools, "초등학교")
            elem_matches = [name] if name else []
            elem_type = "최단거리추정" if name else "매칭실패"

        # 배정된(또는 최단거리로 추정된) 초등학교 "이름"까지의 실제 거리 — 통학구역 안이라도 단지 안에서
        # 학교 위치가 가까운 쪽/먼 쪽일 수 있어 따로 계산한다.
        elem_distance_m = None
        if elem_matches:
            dists = [d for name in elem_matches if (d := distance_to_named_school(pt, name, elem_schools_by_name)) is not None]
            elem_distance_m = min(dists) if dists else None

        mid_matches, mid_type = match_polygon(pt, mid_geoms, mid_names)
        if not mid_matches:
            name, _ = nearest_school(pt, schools, "중학교")
            mid_matches = [name] if name else []
            mid_type = "최단거리추정" if name else "매칭실패"

        rows.append(
            {
                "kapt_code": row.kapt_code,
                "apt_name": row.apt_name,
                "sigungu": row.sigungu,
                "elementary_schools": "|".join(elem_matches),
                "elementary_match_type": elem_type,
                "elementary_distance_m": elem_distance_m,
                "elementary_walk_minutes": (elem_distance_m / 80) if elem_distance_m is not None else None,
                "middle_schools": "|".join(mid_matches),
                "middle_match_type": mid_type,
            }
        )
        if i % 500 == 0 or i == total:
            print(f"{i}/{total} 처리")

    out = pd.DataFrame(rows)
    out_path = DATA_PROCESSED_DIR / "school_assignment.parquet"
    out.to_parquet(out_path, index=False)
    print(f"\n완료: {len(out):,}개 단지 -> {out_path}")
    print(out["elementary_match_type"].value_counts())


if __name__ == "__main__":
    main()
