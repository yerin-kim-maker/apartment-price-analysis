from __future__ import annotations

import datetime
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

from utils.name_match import best_match, normalize_name

APP_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = APP_DIR.parent
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
TRANSACTIONS_PATH = PROCESSED_DIR / "transactions.parquet"
COMPLEX_MASTER_PATH = PROCESSED_DIR / "complex_master.parquet"
SCHOOL_ASSIGNMENT_PATH = PROCESSED_DIR / "school_assignment.parquet"
COMPLEX_COORDS_PATH = PROCESSED_DIR / "complex_coords.parquet"
COMPLEX_SUBWAY_PATH = PROCESSED_DIR / "complex_subway.parquet"
ANALYSIS_DATASET_PATH = PROCESSED_DIR / "analysis_dataset.parquet"

# 배포 환경(Streamlit Community Cloud 등)은 Git LFS를 지원하지 않아 100MB 넘는 이 파일을
# 저장소에 그냥 커밋해두면 포인터 텍스트만 받아진다. 대신 GitHub Release 첨부파일로 올려두고
# 앱 구동 시 없으면(또는 포인터처럼 너무 작으면) 직접 내려받는다.
ANALYSIS_DATASET_URL = (
    "https://github.com/yerin-kim-maker/apartment-price-analysis/releases/download/"
    "data-v1/analysis_dataset.parquet"
)
_MIN_VALID_DATASET_BYTES = 10 * 1024 * 1024  # 실제 파일(백여MB)보다 훨씬 작지만 LFS 포인터(수백바이트)는 확실히 걸러내는 기준


def _ensure_analysis_dataset() -> None:
    if ANALYSIS_DATASET_PATH.exists() and ANALYSIS_DATASET_PATH.stat().st_size >= _MIN_VALID_DATASET_BYTES:
        return
    ANALYSIS_DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(ANALYSIS_DATASET_URL, stream=True, timeout=180)
    resp.raise_for_status()
    tmp_path = ANALYSIS_DATASET_PATH.with_suffix(".downloading")
    with open(tmp_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
    tmp_path.replace(ANALYSIS_DATASET_PATH)

PYEONG = 3.3058

# 전용면적 구간 (국토부 통계에서 흔히 쓰는 구간 기준)
AREA_BUCKETS = [
    (0, 60, "60㎡ 이하 (~18평)"),
    (60, 85, "60~85㎡ (18~26평)"),
    (85, 102, "85~102㎡ (26~31평)"),
    (102, 135, "102~135㎡ (31~41평)"),
    (135, float("inf"), "135㎡ 초과 (41평~)"),
]


def area_bucket(m2: float) -> str:
    if pd.isna(m2):
        return "미상"
    for low, high, label in AREA_BUCKETS:
        if low < m2 <= high:
            return label
    return "미상"


# 세대수 구간
HOUSEHOLD_BUCKETS = [
    (0, 300, "300세대 미만"),
    (300, 500, "300~499세대"),
    (500, 1000, "500~999세대"),
    (1000, 1500, "1000~1499세대"),
    (1500, float("inf"), "1500세대 이상"),
]


def household_bucket(n: float) -> str:
    if pd.isna(n):
        return "정보없음"
    for low, high, label in HOUSEHOLD_BUCKETS:
        if low <= n < high:
            return label
    return "정보없음"


def building_type_label(apt_type: str | None, building_count: float) -> str:
    """국토부 공동주택 등록정보의 공식 건물유형(codeAptNm)을 우선 쓰고, 그 값이 없는 단지만
    동수(building_count) 기준 추정으로 보완한다(동수 1개=주상복합·오피스텔형 추정)."""
    if isinstance(apt_type, str) and apt_type.strip():
        t = apt_type.strip()
        if t == "아파트":
            return "아파트"
        if "주상복합" in t:
            return "주상복합"
        if "연립" in t or "다세대" in t:
            return "연립·다세대"
        return t
    if pd.isna(building_count):
        return "정보없음"
    return "주상복합/오피스텔형 추정" if building_count <= 1 else "아파트"


# 준공 연식 구간 (현재 연도 - 건축년도 기준)
AGE_BUCKETS = [
    (0, 5, "5년 이하(신축)"),
    (5, 10, "5~10년"),
    (10, 20, "10~20년"),
    (20, 30, "20~30년"),
    (30, float("inf"), "30년 초과"),
]


def age_bucket(build_year: float, as_of_year: int | None = None) -> str:
    if pd.isna(build_year):
        return "정보없음"
    as_of_year = as_of_year or datetime.date.today().year
    age = as_of_year - int(build_year)
    if age < 0:
        return "정보없음"
    for low, high, label in AGE_BUCKETS:
        if low <= age < high:
            return label
    return "정보없음"


# 역세권 도보시간 구간 (역세권 분석 파이프라인이 완성되면 채워짐 — 지금은 전부 "정보없음")
WALK_TIME_BUCKETS = [
    (0, 5, "5분 이내"),
    (5, 10, "5~10분"),
    (10, 15, "10~15분"),
    (15, float("inf"), "15분 초과"),
]


def walk_time_bucket(minutes: float) -> str:
    if pd.isna(minutes):
        return "정보없음"
    for low, high, label in WALK_TIME_BUCKETS:
        if low <= minutes < high:
            return label
    return "정보없음"


# 거래유동성 구간 — 최근 12개월 거래건수 기준 (자주 거래될수록 환금성이 좋다고 봄)
LIQUIDITY_BUCKETS = [
    (0, 3, "드묾(연 1~2건)"),
    (3, 6, "보통(연 3~5건)"),
    (6, 12, "활발(연 6~11건)"),
    (12, float("inf"), "매우활발(연 12건 이상)"),
]


def liquidity_bucket(n: float) -> str:
    if pd.isna(n):
        return "정보없음"
    for low, high, label in LIQUIDITY_BUCKETS:
        if low <= n < high:
            return label
    return "정보없음"


# 가격모멘텀 구간 — 그 단지 "자기 자신"의 최근 6개월 평단가 대비 직전 6개월(6~12개월 전) 평단가 변동률.
# 다른 단지와 비교하는 저평가율과는 별개로, 단지 자체의 최근 가격 추세만 본다.
MOMENTUM_BUCKETS = [
    (float("-inf"), -5, "하락(-5%이하)"),
    (-5, 5, "보합(-5~5%)"),
    (5, 15, "상승(5~15%)"),
    (15, float("inf"), "급상승(15%이상)"),
]


def momentum_bucket(pct: float) -> str:
    if pd.isna(pct):
        return "정보없음"
    for low, high, label in MOMENTUM_BUCKETS:
        if low <= pct < high:
            return label
    return "정보없음"


# 점수 체계 기본값 — 전부 객관적으로 측정 가능한 지표만 사용 (브랜드/선호도 등 주관적 항목은 없음).
# "정보없음"은 해당 단지의 그 항목 데이터가 아직 없다는 뜻이라 점수표에 넣지 않는다 (합성 시 그 항목만 빼고 재계산됨).
SCORE_DIMENSIONS = {
    "세대수": [label for _, _, label in HOUSEHOLD_BUCKETS],
    "연식": [label for _, _, label in AGE_BUCKETS],
    "역세권": [label for _, _, label in WALK_TIME_BUCKETS],
    "거래유동성": [label for _, _, label in LIQUIDITY_BUCKETS],
    "가격모멘텀": [label for _, _, label in MOMENTUM_BUCKETS],
}

DEFAULT_SCORES = {
    "세대수": {
        "300세대 미만": 20,
        "300~499세대": 40,
        "500~999세대": 60,
        "1000~1499세대": 80,
        "1500세대 이상": 100,
    },
    "연식": {
        "5년 이하(신축)": 100,
        "5~10년": 80,
        "10~20년": 60,
        "20~30년": 40,
        "30년 초과": 20,
    },
    "역세권": {
        "5분 이내": 100,
        "5~10분": 80,
        "10~15분": 60,
        "15분 초과": 30,
    },
    "거래유동성": {
        "드묾(연 1~2건)": 30,
        "보통(연 3~5건)": 55,
        "활발(연 6~11건)": 80,
        "매우활발(연 12건 이상)": 100,
    },
    "가격모멘텀": {
        "하락(-5%이하)": 30,
        "보합(-5~5%)": 60,
        "상승(5~15%)": 85,
        "급상승(15%이상)": 100,
    },
}

DEFAULT_WEIGHTS = {"세대수": 25, "연식": 20, "역세권": 20, "거래유동성": 20, "가격모멘텀": 15}

BUCKET_COL_BY_DIM = {
    "세대수": "household_bucket",
    "연식": "age_bucket",
    "역세권": "walk_time_bucket",
    "거래유동성": "liquidity_bucket",
    "가격모멘텀": "momentum_bucket",
}


def default_score_table() -> pd.DataFrame:
    rows = []
    for dim, buckets in SCORE_DIMENSIONS.items():
        for b in buckets:
            rows.append({"카테고리": dim, "구간": b, "점수": DEFAULT_SCORES[dim].get(b, 50)})
    return pd.DataFrame(rows)


def default_weight_table() -> pd.DataFrame:
    return pd.DataFrame({"카테고리": list(DEFAULT_WEIGHTS.keys()), "가중치(%)": list(DEFAULT_WEIGHTS.values())})


def compute_composite_score(row: pd.Series, score_map: dict[tuple[str, str], float], weight_map: dict[str, float]) -> float | None:
    """항목별 가중치와 구간별 점수표로 단지 종합점수를 계산.
    데이터 없는 항목("정보없음")은 계산에서 빼고, 남은 항목들 가중치로 다시 100%를 맞춰 계산한다
    (예: 역세권 데이터가 아직 없으면 세대수·연식만으로 계산됨)."""
    weighted_sum = 0.0
    total_weight = 0.0
    for dim, col in BUCKET_COL_BY_DIM.items():
        bucket_val = row.get(col)
        if bucket_val is None or bucket_val == "정보없음":
            continue
        weight = weight_map.get(dim, 0)
        score = score_map.get((dim, bucket_val))
        if weight <= 0 or score is None:
            continue
        weighted_sum += score * weight
        total_weight += weight
    if total_weight <= 0:
        return None
    return round(weighted_sum / total_weight, 1)


def score_breakdown(row: pd.Series, score_map: dict[tuple[str, str], float], weight_map: dict[str, float]) -> pd.DataFrame:
    """compute_composite_score()가 내부적으로 계산하는 걸 항목별로 풀어서 보여주는 표.
    단지점수가 "왜" 이 점수가 나왔는지 화면에서 바로 확인할 수 있게 한다."""
    rows = []
    for dim, col in BUCKET_COL_BY_DIM.items():
        bucket_val = row.get(col)
        weight = weight_map.get(dim, 0)
        if bucket_val is None or bucket_val == "정보없음":
            rows.append({"항목": dim, "이 단지 구간": "정보없음", "배점": None, "가중치(%)": weight, "반영": "제외(데이터없음)"})
            continue
        score = score_map.get((dim, bucket_val))
        rows.append(
            {
                "항목": dim,
                "이 단지 구간": bucket_val,
                "배점": score,
                "가중치(%)": weight,
                "반영": "반영" if (score is not None and weight > 0) else "제외(가중치 0 또는 배점 없음)",
            }
        )
    return pd.DataFrame(rows)


# 저평가율 비교 그룹 기준 — 단지점수(세대수·연식·역세권·유동성·모멘텀을 종합한 0~100점) 구간.
# 세대수/연식 등 개별 스펙이 아니라, 그걸 다 합친 "품질 점수"가 비슷한 단지끼리 묶어서
# 그 안에서 가격이 싼지(저평가)를 본다 — 같은 동네·비슷한 종합점수인데 더 싸게 거래되면 저평가.
SCORE_BUCKETS = [
    (0, 40, "40점 미만"),
    (40, 55, "40~54점"),
    (55, 70, "55~69점"),
    (70, 85, "70~84점"),
    (85, 101, "85점 이상"),
]


def score_bucket(score: float) -> str:
    if pd.isna(score):
        return "정보없음"
    for low, high, label in SCORE_BUCKETS:
        if low <= score < high:
            return label
    return "정보없음"


def _score_bucket_vectorized(score: pd.Series) -> pd.Series:
    bins = [0] + [b[1] for b in SCORE_BUCKETS]
    labels = [b[2] for b in SCORE_BUCKETS]
    result = pd.cut(score, bins=bins, labels=labels, right=False)
    return result.astype(object).where(result.notna(), "정보없음")


def flag_price_outliers(d: pd.DataFrame, group_cols: tuple[str, ...] = ("apt_name", "area_bucket"), threshold: float = 0.35) -> pd.Series:
    """같은 (단지,평형대) 안에서 평당가가 중앙값 대비 threshold 이상 벗어나거나, 직거래(중개 없는 거래)인
    경우를 '특수거래 의심'으로 표시한다. 가족간 거래 여부는 공개 데이터로 알 수 없어 확정할 수는 없고,
    가격이 시세와 크게 동떨어진 경우를 보수적으로 걸러내는 근사치다."""
    if d.empty:
        return pd.Series([], dtype=bool)
    med = d.groupby(list(group_cols))["price_per_pyeong_10k"].transform("median")
    dev = (d["price_per_pyeong_10k"] - med).abs() / med
    is_price_outlier = dev > threshold
    is_direct_deal = d["dealing_type"].fillna("") == "직거래" if "dealing_type" in d.columns else pd.Series(False, index=d.index)
    return (is_price_outlier | is_direct_deal).fillna(False)


def _household_bucket_vectorized(n: pd.Series) -> pd.Series:
    bins = [0] + [b[1] for b in HOUSEHOLD_BUCKETS]
    labels = [b[2] for b in HOUSEHOLD_BUCKETS]
    result = pd.cut(n, bins=bins, labels=labels, right=False)
    return result.astype(object).where(result.notna(), "정보없음")


def _walk_time_bucket_vectorized(minutes: pd.Series) -> pd.Series:
    bins = [0] + [b[1] for b in WALK_TIME_BUCKETS]
    labels = [b[2] for b in WALK_TIME_BUCKETS]
    result = pd.cut(minutes, bins=bins, labels=labels, right=False)
    return result.astype(object).where(result.notna(), "정보없음")


def _liquidity_bucket_vectorized(n: pd.Series) -> pd.Series:
    bins = [0] + [b[1] for b in LIQUIDITY_BUCKETS]
    labels = [b[2] for b in LIQUIDITY_BUCKETS]
    result = pd.cut(n, bins=bins, labels=labels, right=False)
    return result.astype(object).where(result.notna(), "정보없음")


def _momentum_bucket_vectorized(pct: pd.Series) -> pd.Series:
    bins = [float("-inf")] + [b[1] for b in MOMENTUM_BUCKETS]
    labels = [b[2] for b in MOMENTUM_BUCKETS]
    result = pd.cut(pct, bins=bins, labels=labels, right=False)
    return result.astype(object).where(result.notna(), "정보없음")


def _building_type_vectorized(apt_type: pd.Series, building_count: pd.Series) -> pd.Series:
    """building_type_label()과 동일한 로직(공식 건물유형 우선, 없으면 동수로 추정)을 벡터화."""
    import numpy as np

    t = apt_type.fillna("").astype(str).str.strip()
    is_apt = t == "아파트"
    is_mixed = t.str.contains("주상복합", na=False)
    is_row = t.str.contains("연립", na=False) | t.str.contains("다세대", na=False)
    has_type = t != ""

    fallback = pd.Series(
        np.select(
            [building_count.isna(), building_count <= 1],
            ["정보없음", "주상복합/오피스텔형 추정"],
            default="아파트",
        ),
        index=apt_type.index,
    )

    return pd.Series(
        np.select(
            [is_apt, is_mixed, is_row, has_type],
            ["아파트", "주상복합", "연립·다세대", t],
            default=fallback,
        ),
        index=apt_type.index,
    )


def _area_bucket_vectorized(m2: pd.Series) -> pd.Series:
    """area_bucket()과 동일한 결과를 pd.cut으로 한 번에 계산 (행마다 파이썬 함수 호출하는 것보다 훨씬 빠름)."""
    bins = [0] + [b[1] for b in AREA_BUCKETS]
    labels = [b[2] for b in AREA_BUCKETS]
    result = pd.cut(m2, bins=bins, labels=labels, right=True)
    return result.astype(object).where(result.notna(), "미상")


def _age_bucket_vectorized(build_year: pd.Series, as_of_year: int | None = None) -> pd.Series:
    """age_bucket()과 동일한 결과(연식 = as_of_year - build_year, [low,high) 구간)를 벡터화 계산."""
    as_of_year = as_of_year or datetime.date.today().year
    age = as_of_year - build_year
    bins = [0] + [b[1] for b in AGE_BUCKETS]
    labels = [b[2] for b in AGE_BUCKETS]
    result = pd.cut(age, bins=bins, labels=labels, right=False)
    return result.astype(object).where(result.notna(), "정보없음")


@st.cache_data(show_spinner="실거래가 데이터를 불러오는 중...")
def load_transactions() -> pd.DataFrame:
    if not TRANSACTIONS_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(TRANSACTIONS_PATH)
    df = df[~df["cancelled"].fillna(False)].copy()
    df["area_pyeong"] = (df["area_exclusive_m2"] / PYEONG).round(1)
    df["area_bucket"] = _area_bucket_vectorized(df["area_exclusive_m2"])
    df["age_bucket"] = _age_bucket_vectorized(df["build_year"])
    df["price_10k"] = df["deal_amount_10k"]
    df["price_per_pyeong_10k"] = (df["price_10k"] / df["area_pyeong"]).round(1)
    # 백만원 단위 (1억 = 100백만원) — 화면 표시용. 만원 단위보다 자릿수가 적어 읽기 쉽다.
    df["price_mn"] = (df["price_10k"] / 100).round(1)
    df["price_per_pyeong_mn"] = (df["price_per_pyeong_10k"] / 100).round(1)
    df["yyyymm"] = df["deal_date"].dt.to_period("M").astype(str)
    return df


@st.cache_data(show_spinner="단지 정보를 불러오는 중...")
def load_complex_master() -> pd.DataFrame:
    if not COMPLEX_MASTER_PATH.exists():
        return pd.DataFrame()
    return pd.read_parquet(COMPLEX_MASTER_PATH)


@st.cache_data(show_spinner="단지명을 매칭하는 중... (처음 한 번만 시간이 걸립니다)")
def build_apt_name_match_table() -> pd.DataFrame:
    """실거래가의 단지명(sigungu, dong, apt_name)을 공동주택 단지목록의 등록명과 매칭.
    표기가 정확히 같지 않은 경우가 많아, 같은 (시군구,법정동) 안에서만 정규화/유사매칭을
    시도하고 후보가 애매하면 매칭하지 않는다. 결과: sigungu, dong, apt_name, household_count,
    building_count, use_approval_ymd, match_type 컬럼을 가진 매칭 테이블."""
    tx = load_transactions()
    cm = load_complex_master()
    if tx.empty or cm.empty:
        return pd.DataFrame(
            columns=[
                "sigungu", "dong", "apt_name", "kapt_code", "household_count",
                "building_count", "use_approval_ymd", "match_type",
            ]
        )

    cm_valid = cm.dropna(subset=["household_count"]).copy()
    cm_valid["norm"] = cm_valid["apt_name"].apply(normalize_name)

    tx_keys = tx[["sigungu", "dong", "apt_name"]].dropna().drop_duplicates()
    tx_keys["norm"] = tx_keys["apt_name"].apply(normalize_name)

    cm_cols = ["sigungu", "dong", "norm", "kapt_code", "household_count", "building_count", "use_approval_ymd", "apt_type"]
    # 빠른 경로: 정규화 후 정확히 같은 이름은 문자열 유사도 계산 없이 merge로 바로 매칭 (대부분이 여기서 끝남).
    # 같은 (시군구,법정동,정규화명)에 후보가 여럿이면 원래 로직처럼 첫 번째를 쓴다.
    cm_first = cm_valid.sort_values("household_count", ascending=False).drop_duplicates(
        subset=["sigungu", "dong", "norm"], keep="first"
    )
    exact = tx_keys.merge(cm_first[cm_cols], on=["sigungu", "dong", "norm"], how="inner")
    exact["match_type"] = "exact"
    exact["match_score"] = 1.0

    check = tx_keys.merge(
        exact[["sigungu", "dong", "apt_name"]], on=["sigungu", "dong", "apt_name"], how="left", indicator=True
    )
    residual = tx_keys[check["_merge"].to_numpy() == "left_only"]

    # 느린 경로(유사도 비교)는 빠른 경로로 못 붙은 나머지에만 적용 — 후보군도 (시군구,법정동)로 미리 좁혀둔다.
    rows: list[dict] = []
    if not residual.empty:
        for (sigungu, dong), group in residual.groupby(["sigungu", "dong"]):
            cands = cm_valid[(cm_valid["sigungu"] == sigungu) & (cm_valid["dong"] == dong)]
            if cands.empty:
                continue
            cand_norm_to_rows = cands.groupby("norm")
            cand_norms = list(cand_norm_to_rows.groups.keys())
            for _, row in group.iterrows():
                matched_norm, score, match_type = best_match(row["norm"], cand_norms)
                if matched_norm is None:
                    continue
                cm_row = cand_norm_to_rows.get_group(matched_norm).iloc[0]
                rows.append(
                    {
                        "sigungu": sigungu,
                        "dong": dong,
                        "apt_name": row["apt_name"],
                        "kapt_code": cm_row.get("kapt_code"),
                        "household_count": cm_row["household_count"],
                        "building_count": cm_row.get("building_count"),
                        "use_approval_ymd": cm_row.get("use_approval_ymd"),
                        "apt_type": cm_row.get("apt_type"),
                        "match_type": match_type,
                        "match_score": round(score, 2),
                    }
                )

    fuzzy_df = pd.DataFrame(rows)
    exact_df = exact.drop(columns=["norm"]).rename(columns={"match_score": "match_score"})
    return pd.concat([exact_df, fuzzy_df], ignore_index=True)


@st.cache_data(show_spinner="실거래가와 단지정보를 결합하는 중...")
def load_transactions_with_household() -> pd.DataFrame:
    """실거래가에 단지 세대수를 결합 (단지명 매칭 기반이라 일부는 붙지 않을 수 있음)."""
    tx = load_transactions()
    if tx.empty:
        return tx

    match_table = build_apt_name_match_table()
    if match_table.empty:
        tx = tx.copy()
        tx["household_count"] = pd.NA
        tx["household_bucket"] = "정보없음"
        tx["building_type"] = "정보없음"
        return tx

    merged = tx.merge(
        match_table[["sigungu", "dong", "apt_name", "household_count", "building_count", "use_approval_ymd", "apt_type"]],
        on=["sigungu", "dong", "apt_name"],
        how="left",
    )
    merged["household_bucket"] = _household_bucket_vectorized(merged["household_count"])
    merged["building_type"] = _building_type_vectorized(merged["apt_type"], merged["building_count"])
    return merged


@st.cache_data(show_spinner="배정 학교 정보를 불러오는 중...")
def load_school_assignment() -> pd.DataFrame:
    if not SCHOOL_ASSIGNMENT_PATH.exists():
        return pd.DataFrame()
    return pd.read_parquet(SCHOOL_ASSIGNMENT_PATH)


@st.cache_data(show_spinner="단지 좌표를 불러오는 중...")
def load_complex_coords() -> pd.DataFrame:
    if not COMPLEX_COORDS_PATH.exists():
        return pd.DataFrame()
    return pd.read_parquet(COMPLEX_COORDS_PATH)


@st.cache_data(show_spinner="역세권 정보를 불러오는 중...")
def load_complex_subway() -> pd.DataFrame:
    if not COMPLEX_SUBWAY_PATH.exists():
        return pd.DataFrame()
    return pd.read_parquet(COMPLEX_SUBWAY_PATH)


def _compute_transactions_enriched() -> pd.DataFrame:
    """실거래가 + 세대수 + 배정학교(초/중) + 단지 좌표(위경도) + 역세권(최근접역 도보시간)을
    한 번에 결합한 데이터프레임. 전부 (시군구,법정동,단지명) 이름 매칭(kapt_code 경유) 기반이라
    일부는 못 붙을 수 있다. 이 계산은 시간이 좀 걸려서(단지명 매칭 등), scripts/build_dataset.py로
    미리 한 번 계산해 analysis_dataset.parquet에 저장해두면 대시보드가 훨씬 빨리 뜬다."""
    tx = load_transactions_with_household()
    if tx.empty:
        return tx

    extra_cols = [
        "elementary_schools", "elementary_match_type", "elementary_distance_m", "elementary_walk_minutes",
        "middle_schools", "middle_match_type",
        "lat", "lon", "nearest_station", "nearest_station_line", "distance_m", "walk_minutes",
    ]
    tx = tx.copy()
    for c in extra_cols:
        tx[c] = None
    tx["walk_time_bucket"] = "정보없음"

    match_table = build_apt_name_match_table()
    if match_table.empty:
        return tx

    name_to_kapt = match_table.dropna(subset=["kapt_code"])[["sigungu", "dong", "apt_name", "kapt_code"]]

    school = load_school_assignment()
    coords = load_complex_coords()
    subway = load_complex_subway()

    lookup = name_to_kapt.copy()
    school_cols_needed = [
        "elementary_schools", "elementary_match_type", "elementary_distance_m", "elementary_walk_minutes",
        "middle_schools", "middle_match_type",
    ]
    if not school.empty:
        lookup = lookup.merge(school[["kapt_code"] + school_cols_needed], on="kapt_code", how="left")
    else:
        for c in school_cols_needed:
            lookup[c] = None

    if not coords.empty:
        lookup = lookup.merge(coords[["kapt_code", "lat", "lon"]], on="kapt_code", how="left")
    else:
        lookup["lat"] = None
        lookup["lon"] = None

    subway_cols_needed = ["nearest_station", "nearest_station_line", "distance_m", "walk_minutes"]
    if not subway.empty:
        lookup = lookup.merge(subway[["kapt_code"] + subway_cols_needed], on="kapt_code", how="left")
    else:
        for c in subway_cols_needed:
            lookup[c] = None

    lookup_cols = [c for c in lookup.columns if c not in ("kapt_code",)]
    tx = tx.drop(columns=extra_cols)
    merged = tx.merge(lookup[lookup_cols], on=["sigungu", "dong", "apt_name"], how="left")
    merged["walk_time_bucket"] = _walk_time_bucket_vectorized(merged["walk_minutes"])
    return merged


@st.cache_data(show_spinner="실거래가에 세대수·배정학교·좌표·역세권을 결합하는 중...")
def load_transactions_enriched() -> pd.DataFrame:
    """analysis_dataset.parquet(미리 계산해둔 결합 결과)가 있으면 그냥 읽어서 바로 반환하고,
    없으면(아직 한 번도 build_dataset.py를 안 돌렸으면) 그 자리에서 직접 계산한다."""
    _ensure_analysis_dataset()
    if ANALYSIS_DATASET_PATH.exists():
        return pd.read_parquet(ANALYSIS_DATASET_PATH)
    return _compute_transactions_enriched()


def data_last_updated() -> str | None:
    if not TRANSACTIONS_PATH.exists():
        return None
    ts = TRANSACTIONS_PATH.stat().st_mtime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
