import math

import pandas as pd
import plotly.express as px
import streamlit as st

from utils.data_loader import (
    AREA_BUCKETS,
    HOUSEHOLD_BUCKETS,
    _liquidity_bucket_vectorized,
    _momentum_bucket_vectorized,
    _score_bucket_vectorized,
    compute_composite_score,
    data_last_updated,
    default_score_table,
    default_weight_table,
    flag_price_outliers,
    load_transactions_enriched,
)

st.set_page_config(page_title="아파트 실거래가 분석", page_icon="🏢", layout="wide")

st.title("서울·경기 아파트 실거래가 분석")
st.caption("국토교통부 실거래가 공공데이터 기반 (네이버 부동산 크롤링 없이, 합법적인 공식 데이터만 사용)")

df = load_transactions_enriched()

if df.empty:
    st.warning(
        "아직 수집된 실거래가 데이터가 없습니다.\n\n"
        "1. 프로젝트 폴더의 `.env.example`을 복사해 `.env`로 만들고 `DATA_GO_KR_KEY`를 채워주세요.\n"
        "2. **데이터_업데이트.bat** 를 더블클릭하면 데이터를 받아옵니다.\n\n"
        "(터미널이 편하다면 `python scripts/fetch_transactions.py --region 강남구 --months 12` 처럼 "
        "특정 지역만 빠르게 먼저 받아볼 수도 있습니다.)"
    )
    st.stop()

sale = df[df["deal_type"] == "매매"].dropna(subset=["deal_date"])

# ============================================================
# 사이드바 — 모든 탭이 공유하는 필터
# ============================================================
with st.sidebar:
    st.header("필터")
    sido_list = sorted(sale["sido"].unique())
    sido_choice = st.selectbox("시/도", ["전체"] + sido_list, index=0)
    sigungu_pool = sale if sido_choice == "전체" else sale[sale["sido"] == sido_choice]

    sigungu_list = sorted(sigungu_pool["sigungu"].unique())
    sigungus = st.multiselect(
        "시군구 (여러 개 선택 가능)",
        sigungu_list,
        default=[],
        placeholder="전체 (선택 안 하면 위에서 고른 시/도 전체)",
        key=f"sigungu_select_{sido_choice}",  # 시/도 바뀌면 이전 선택이 안 남게 위젯을 새로 만든다
    )
    if not sigungus and sido_choice != "전체":
        sigungus = sigungu_list  # 시/도만 고르고 시군구는 안 골랐으면 그 시/도 전체로 취급

    if sigungus:
        apt_options = sorted(sale.loc[sale["sigungu"].isin(sigungus), "apt_name"].dropna().unique())
    else:
        apt_options = []
    apt_names = st.multiselect(
        "단지명",
        apt_options,
        default=[],
        placeholder="지역을 먼저 선택하세요" if not sigungus else "전체 단지 (선택 안 하면 지역 전체)",
        disabled=not sigungus,
    )

    st.divider()
    st.subheader("저평가 탭 조건")
    target_bucket = st.selectbox(
        "평형대 (전용면적 기준)",
        [b[2] for b in AREA_BUCKETS],
        help="예산은 이 평형대 기준으로 비교됩니다.",
    )
    building_type_options = sorted(sale["building_type"].dropna().unique())
    building_types = st.multiselect(
        "건물유형",
        building_type_options,
        default=["아파트"] if "아파트" in building_type_options else building_type_options,
        help="국토부 등록 정식 분류(아파트/주상복합/연립·다세대 등) 기준입니다. 평당가 수준이 유형마다 많이 "
        "달라서 같이 비교하면 왜곡되니 기본은 아파트만 봅니다.",
    )
    budget_mode = st.radio("예산 기준", ["거래총액(백만원)", "평당가(백만원/평)"])

    base_pool = sale[sale["area_bucket"] == target_bucket]
    if sigungus:
        base_pool = base_pool[base_pool["sigungu"].isin(sigungus)]
    if building_types:
        base_pool = base_pool[base_pool["building_type"].isin(building_types)]

    budget_min = budget_max = None
    if not base_pool.empty:
        if budget_mode == "거래총액(백만원)":
            # floor/ceil로 여유를 둔다 — int()로 그냥 자르면 "전체"를 선택해도 소수점 때문에
            # 최고가 거래 하나가 예산 범위 밖으로 밀려나 조용히 빠지는 경우가 있었다.
            lo, hi = math.floor(base_pool["price_mn"].min()), math.ceil(base_pool["price_mn"].max())
            presets = [
                (0, 300, "3억 이하"),
                (300, 500, "3~5억"),
                (500, 700, "5~7억"),
                (700, 1000, "7~10억"),
                (1000, 1500, "10~15억"),
                (1500, float("inf"), "15억 초과"),
            ]
        else:
            lo, hi = math.floor(base_pool["price_per_pyeong_mn"].min()), math.ceil(base_pool["price_per_pyeong_mn"].max())
            presets = [
                (0, 30, "3천만원 이하"),
                (30, 50, "3천~5천만원"),
                (50, 80, "5천~8천만원"),
                (80, 120, "8천만원~1.2억"),
                (120, float("inf"), "1.2억 초과"),
            ]
        if lo >= hi:
            hi = lo + 1

        preset_labels = ["전체"] + [p[2] for p in presets]
        budget_choice = st.segmented_control(
            "예산 구간 (빠른 선택)", preset_labels, default="전체", key=f"budget_{budget_mode}"
        )
        if budget_choice in (None, "전체"):
            preset_lo, preset_hi = lo, hi
        else:
            p_lo, p_hi, _ = next(p for p in presets if p[2] == budget_choice)
            preset_lo = max(lo, p_lo)
            preset_hi = hi if p_hi == float("inf") else min(hi, p_hi)

        unit_divisor = 100 if budget_mode == "거래총액(백만원)" else 1
        unit_label = "억원" if budget_mode == "거래총액(백만원)" else "백만원/평"
        step = 0.1 if budget_mode == "거래총액(백만원)" else 1.0

        st.caption(f"직접 입력 ({unit_label} 단위, 소수점 가능)")
        col_min, col_max = st.columns(2)
        input_key = f"{budget_mode}_{budget_choice}"
        with col_min:
            min_input = st.number_input(
                f"최소({unit_label})",
                min_value=round(lo / unit_divisor, 1),
                max_value=round(hi / unit_divisor, 1),
                value=round(preset_lo / unit_divisor, 1),
                step=step,
                key=f"min_{input_key}",
            )
        with col_max:
            max_input = st.number_input(
                f"최대({unit_label})",
                min_value=round(lo / unit_divisor, 1),
                max_value=round(hi / unit_divisor, 1),
                value=round(preset_hi / unit_divisor, 1),
                step=step,
                key=f"max_{input_key}",
            )
        budget_min, budget_max = min_input * unit_divisor, max_input * unit_divisor
        if budget_min > budget_max:
            budget_min, budget_max = budget_max, budget_min
        st.caption(f"선택: **{budget_min:,.0f} ~ {budget_max:,.0f}** ({budget_mode})")

    st.divider()
    st.caption(f"데이터 갱신: {data_last_updated() or '-'}")
    st.caption(f"전체 {len(sale):,}건 / {sale['sigungu'].nunique()}개 시군구")

# "평형대"는 저평가 탭 안에서 단일 선택(target_bucket)으로 따로 고르므로, 여기서는 전체 평형을 기본으로 둔다
# (사이드바에 똑같은 이름의 필터가 두 번 보이는 걸 방지).
bucket_options = [b[2] for b in AREA_BUCKETS]
scoped = sale[sale["area_bucket"].isin(bucket_options)]
if sigungus:
    scoped = scoped[scoped["sigungu"].isin(sigungus)]
if apt_names:
    scoped = scoped[scoped["apt_name"].isin(apt_names)]


# ============================================================
# 공통 헬퍼
# ============================================================
def compute_summary_metrics(d: pd.DataFrame, recent_months: int = 3) -> dict | None:
    """최근 N개월 평균 평당가와, 1년 전 같은 길이 구간 대비 상승률을 계산."""
    d = d.dropna(subset=["deal_date"])
    max_date = d["deal_date"].max()
    if pd.isna(max_date):
        return None

    recent_start = max_date - pd.DateOffset(months=recent_months)
    recent = d[d["deal_date"] > recent_start]

    baseline_start = recent_start - pd.DateOffset(years=1)
    baseline_end = max_date - pd.DateOffset(years=1)
    baseline = d[(d["deal_date"] > baseline_start) & (d["deal_date"] <= baseline_end)]

    recent_avg = recent["price_per_pyeong_mn"].mean()
    baseline_avg = baseline["price_per_pyeong_mn"].mean()
    yoy = None
    if pd.notna(baseline_avg) and baseline_avg:
        yoy = (recent_avg - baseline_avg) / baseline_avg * 100

    return {
        "recent_avg": recent_avg if pd.notna(recent_avg) else None,
        "yoy": yoy,
        "recent_count": len(recent),
        "total_count": len(d),
        "recent_start": recent_start,
        "max_date": max_date,
    }


def fmt_mn(v: float | None) -> str:
    return f"{v:,.1f}백만원" if v is not None else "정보 부족"


def price_col_config(label: str, help: str | None = None) -> st.column_config.NumberColumn:
    """가격(백만원) 컬럼에 천단위 콤마를 넣기 위한 공통 설정."""
    return st.column_config.NumberColumn(label, format="%,.1f", help=help)


def fmt_pct(v: float | None) -> str:
    return f"{v:+.1f}%" if v is not None and pd.notna(v) else "정보 부족"


# 점수구간 배지 색 — 낮음(회적색)→높음(초록) 톤을 최대한 차분하게(파스텔) 눌러서 씀.
SCORE_BUCKET_COLORS = {
    "40점 미만": ("#F6E9E9", "#8C3B34"),
    "40~54점": ("#F7EFE3", "#8A6229"),
    "55~69점": ("#F6F3E1", "#7A712A"),
    "70~84점": ("#E9F1EA", "#3F6B45"),
    "85점 이상": ("#DFEEE5", "#2A6146"),
    "정보없음": ("#EFEFEF", "#6B6B6B"),
}


def style_score_bucket(display: pd.DataFrame, column: str = "점수구간") -> "pd.io.formats.style.Styler":
    """점수구간 컬럼에 낮음→높음 톤의 차분한 배경색을 입혀서 한눈에 구분되게 한다."""

    def _apply(col: pd.Series) -> list[str]:
        styles = []
        for v in col:
            bg, fg = SCORE_BUCKET_COLORS.get(v, ("", ""))
            styles.append(f"background-color: {bg}; color: {fg};" if bg else "")
        return styles

    return display.style.apply(_apply, subset=[column])


# ============================================================
# 탭 구성
# ============================================================
tab_underval, tab_summary, tab_trend, tab_subway, tab_school, tab_household = st.tabs(
    [
        "저평가 단지 찾기",
        "시세 요약",
        "기간별 가격 추이",
        "역세권 분석",
        "학군 분석",
        "세대수별 상승률",
    ]
)

# ------------------------------------------------------------
# 저평가 단지 찾기
# ------------------------------------------------------------
with tab_underval:
    st.caption("이 탭은 비슷한 품질(단지점수)의 단지들끼리 묶어서, 그중 가격이 싼 단지를 찾습니다.")
    st.caption(
        "**단지점수**(세대수·연식·역세권·거래유동성·가격모멘텀을 종합한 0~100점, 선호도·브랜드 등 주관적 항목은 "
        "제외)가 비슷한 단지들을 한 그룹으로 묶고(같은 시군구·법정동 안에서만), 그 그룹 평균가보다 "
        "실거래가가 낮은 단지를 **저평가**로 표시합니다. "
        "즉 저평가율은 '가격'만의 비교이고, 단지점수는 그 비교 그룹을 정하는 '품질' 기준이라 서로 다른 지표예요 — "
        "표에서 두 값을 같이 보고, 점수도 높고 저평가율도 높은 단지가 가장 눈여겨볼 만합니다. "
        "학군(초등학교)은 배정학교가 확인되면 참고로 같이 보여드리지만, 진학실적 같은 객관적 품질지표가 "
        "없어서 점수에는 넣지 않았습니다. "
        "국토부 등록 동수가 1개인 단지는 대개 주상복합·오피스텔형(예: 'OO에클라트')이라 일반 아파트와 "
        "평당가 수준이 많이 달라, 아래 **건물유형** 필터로 기본은 아파트만 비교합니다."
    )

    with st.expander("단지점수 배점 설정 (직접 조정 가능)"):
        st.caption(
            "학군은 '배정학교가 어디인지'는 알지만 그 학교가 좋은지 나쁜지 판단할 객관적 데이터"
            "(진학실적 등)가 없어서(학교알리미 API에도 없음) 점수 항목에서는 뺐습니다. "
            "역세권은 최근접 지하철역까지 도보시간(분속 80m 환산 추정치)으로 반영됩니다."
        )
        col_w, col_s = st.columns([1, 2])
        with col_w:
            st.markdown("**항목별 가중치 (%)**")
            weight_df = st.data_editor(
                default_weight_table(),
                key="weight_editor",
                hide_index=True,
                use_container_width=True,
                disabled=["카테고리"],
            )
        with col_s:
            st.markdown("**구간별 점수 (0~100)**")
            score_df = st.data_editor(
                default_score_table(),
                key="score_editor",
                hide_index=True,
                use_container_width=True,
                disabled=["카테고리", "구간"],
                num_rows="fixed",
            )
        score_map = {(r["카테고리"], r["구간"]): r["점수"] for _, r in score_df.iterrows()}
        weight_map = dict(zip(weight_df["카테고리"], weight_df["가중치(%)"]))

    if base_pool.empty:
        st.info("이 조건(평형대·지역·건물유형)의 거래가 아직 없습니다. 왼쪽 사이드바에서 조건을 넓혀보세요.")
    else:
        # 특수거래 의심(직거래, 시세와 크게 동떨어진 거래) 제외
        outlier_flag = flag_price_outliers(base_pool)
        clean_pool = base_pool[~outlier_flag]
        excluded = base_pool[outlier_flag]

        max_date = clean_pool["deal_date"].max()
        recent = clean_pool[clean_pool["deal_date"] > max_date - pd.DateOffset(months=12)] if pd.notna(max_date) else clean_pool.iloc[0:0]

        # 가격모멘텀: 다른 단지와 비교하는 저평가율과 달리, 그 단지 자체의 최근 6개월 평단가가
        # 직전 6개월(6~12개월 전)보다 얼마나 올랐는지를 본다. 표본 확보를 위해 12개월 전체(recent)가
        # 아니라 clean_pool(특수거래만 뺀 전체 기간)에서 계산한다.
        if pd.notna(max_date):
            mom_recent = clean_pool[clean_pool["deal_date"] > max_date - pd.DateOffset(months=6)]
            mom_prior = clean_pool[
                (clean_pool["deal_date"] > max_date - pd.DateOffset(months=12))
                & (clean_pool["deal_date"] <= max_date - pd.DateOffset(months=6))
            ]
            mom_recent_price = mom_recent.groupby(["sigungu", "dong", "apt_name"])["price_per_pyeong_mn"].median()
            mom_prior_price = mom_prior.groupby(["sigungu", "dong", "apt_name"])["price_per_pyeong_mn"].median()
            momentum = ((mom_recent_price - mom_prior_price) / mom_prior_price * 100).rename("가격모멘텀(%)").reset_index()
        else:
            momentum = pd.DataFrame(columns=["sigungu", "dong", "apt_name", "가격모멘텀(%)"])

        MIN_RECENT = 1
        apt_stats = (
            recent.groupby(["sigungu", "dong", "apt_name"], as_index=False)
            .agg(
                최근평단가=("price_per_pyeong_mn", "median"),
                최근총액=("price_mn", "median"),
                거래건수=("price_per_pyeong_mn", "size"),
                세대수=("household_count", "first"),
                세대수구간=("household_bucket", "first"),
                준공연도=("build_year", "max"),
                연식구간=("age_bucket", lambda s: s.mode().iat[0] if not s.mode().empty else "정보없음"),
                배정초등학교=("elementary_schools", "first"),
                초등학교도보=("elementary_walk_minutes", "first"),
                배정중학군=("middle_schools", "first"),
                역세권구간=("walk_time_bucket", "first"),
                최근접역=("nearest_station", "first"),
                도보시간=("walk_minutes", "first"),
                건물유형=("building_type", "first"),
                위도=("lat", "first"),
                경도=("lon", "first"),
            )
        )
        apt_stats = apt_stats.merge(momentum, on=["sigungu", "dong", "apt_name"], how="left")
        apt_stats["유동성구간"] = _liquidity_bucket_vectorized(apt_stats["거래건수"])
        apt_stats["모멘텀구간"] = _momentum_bucket_vectorized(apt_stats["가격모멘텀(%)"])
        apt_stats["단지점수"] = apt_stats.apply(
            lambda r: compute_composite_score(
                pd.Series(
                    {
                        "household_bucket": r["세대수구간"],
                        "age_bucket": r["연식구간"],
                        "walk_time_bucket": r["역세권구간"],
                        "liquidity_bucket": r["유동성구간"],
                        "momentum_bucket": r["모멘텀구간"],
                    }
                ),
                score_map,
                weight_map,
            ),
            axis=1,
        )
        apt_stats = apt_stats[apt_stats["거래건수"] >= MIN_RECENT]
        apt_stats["점수구간"] = _score_bucket_vectorized(apt_stats["단지점수"])

        # 단지점수를 낼 수 없는(=세대수·연식·역세권·유동성·모멘텀 전부 정보없음) 단지는
        # "정보없음"끼리 서로 전혀 다른 단지들과 잘못 묶이게 되므로 아예 비교 대상에서 뺀다.
        no_info = apt_stats[apt_stats["단지점수"].isna()]
        apt_stats = apt_stats[apt_stats["단지점수"].notna()]

        if apt_stats.empty:
            st.info("최근 12개월 내 거래가 없어 비교할 단지가 없습니다. 평형대나 지역을 넓혀보세요.")
        else:
            # 같은 시군구·법정동 + 비슷한 단지점수(종합 품질) 구간 안에서만 비교.
            # 세대수·연식 같은 개별 스펙이 아니라, 그걸 다 합친 단지점수가 비슷한 단지끼리 묶어서
            # 그 안에서 가격이 싼 쪽을 저평가로 본다 (구 전체로 묶으면 동별 가격대 차이가 너무 커서 왜곡됨).
            group_keys = ["sigungu", "dong", "점수구간"]
            apt_stats["그룹평균평단가"] = apt_stats.groupby(group_keys)["최근평단가"].transform("mean")
            apt_stats["그룹표본수"] = apt_stats.groupby(group_keys)["apt_name"].transform("count")
            apt_stats["저평가율(%)"] = (
                (apt_stats["그룹평균평단가"] - apt_stats["최근평단가"]) / apt_stats["그룹평균평단가"] * 100
            ).round(1)

            # 그룹 표본이 적다고 리스트에서 숨기지는 않는다 — 대신 "그룹표본수" 컬럼을 그대로 보여줘서
            # 표본이 1~2개뿐이라 저평가율이 불안정할 수 있는 단지도 사용자가 직접 보고 판단하게 한다.
            MIN_GROUP_RELIABLE = 3
            comparable = apt_stats.copy()

            if budget_mode == "거래총액(백만원)":
                comparable = comparable[comparable["최근총액"].between(budget_min, budget_max)]
            else:
                comparable = comparable[comparable["최근평단가"].between(budget_min, budget_max)]

            comparable = comparable.sort_values("저평가율(%)", ascending=False)

            if comparable.empty:
                st.info("이 예산 범위·그룹 조건에 맞는 단지가 없습니다. 왼쪽 사이드바에서 예산 범위나 평형대를 조정해보세요.")
            else:
                low_sample_count = int((comparable["그룹표본수"] < MIN_GROUP_RELIABLE).sum())

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("전체 단지", f"{len(comparable):,}개 단지")
                m2.metric("최근 12개월 거래", f"{int(comparable['거래건수'].sum()):,}건")
                m3.metric("최고 저평가율", fmt_pct(comparable["저평가율(%)"].max()))
                m4.metric("특수거래 제외", f"{outlier_flag.sum():,}건")
                if low_sample_count > 0:
                    st.caption(
                        f"이 중 {low_sample_count:,}개는 같은 그룹(시군구·법정동·점수구간)에 단지가 3개 미만이라 "
                        f"'그룹표본수' 컬럼이 3 미만입니다 — 그룹평균이 단지 1~2개로만 계산된 거라 저평가율이 "
                        f"불안정할 수 있으니 참고만 해주세요. 시군구를 넓게 선택하면 표본이 늘어나 더 안정적으로 비교됩니다."
                    )
                st.caption(f"데이터 갱신: {data_last_updated() or '-'}")

                # 컬럼을 [식별정보] → [금액] → [저평가 평가] → [점수 산출 근거·상세 데이터] 순으로 묶어서 배치.
                display = comparable.rename(
                    columns={
                        "apt_name": "단지명",
                        "sigungu": "시군구",
                        "dong": "법정동",
                        "최근평단가": "최근평단가(백만원/평)",
                        "최근총액": "최근실거래가(백만원)",
                        "그룹평균평단가": "그룹평균평단가(백만원/평)",
                        "도보시간": "역까지도보(분)",
                        "초등학교도보": "초등학교도보(분)",
                        "유동성구간": "거래유동성",
                    }
                )[
                    [
                        "단지명", "시군구", "법정동", "건물유형",
                        "최근실거래가(백만원)", "최근평단가(백만원/평)", "그룹평균평단가(백만원/평)",
                        "저평가율(%)", "그룹표본수", "단지점수", "점수구간",
                        "세대수구간", "연식구간", "최근접역", "역까지도보(분)",
                        "배정초등학교", "초등학교도보(분)", "배정중학군", "거래유동성", "가격모멘텀(%)", "거래건수",
                    ]
                ].copy()
                display["최근접역"] = display["최근접역"].fillna("정보없음")
                display["배정초등학교"] = display["배정초등학교"].fillna("정보없음")
                display["배정중학군"] = display["배정중학군"].fillna("정보없음")

                st.subheader("전체 리스트")
                search_term = st.text_input("단지 검색", placeholder="단지명으로 검색...")
                if search_term:
                    display = display[display["단지명"].str.contains(search_term, case=False, na=False)]
                st.dataframe(
                    style_score_bucket(display),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "최근실거래가(백만원)": price_col_config("최근실거래가(백만원)"),
                        "최근평단가(백만원/평)": price_col_config("최근평단가(백만원/평)"),
                        "그룹평균평단가(백만원/평)": price_col_config(
                            "그룹평균평단가(백만원/평)",
                            help="'그룹' = 같은 시군구·법정동(동네) + 단지점수 구간(세대수·연식·역세권·유동성·모멘텀을 "
                            "종합한 품질 점수가 비슷한 단지들). 그 단지들의 평균 평당가입니다. 이 단지 평당가가 "
                            "이보다 낮을수록(=비슷한 품질인데 더 싸게 거래될수록) 저평가율이 높게 나와요.",
                        ),
                        "저평가율(%)": st.column_config.NumberColumn("저평가율(%)", format="%+.1f%%"),
                        "그룹표본수": st.column_config.NumberColumn(
                            "그룹표본수",
                            help="같은 시군구·법정동·점수구간 그룹 안에 단지가 몇 개 있는지. 3개 미만이면 "
                            "그룹평균(과 저평가율)이 단지 1~2개로만 계산된 거라 신뢰도가 낮습니다.",
                        ),
                        "역까지도보(분)": st.column_config.NumberColumn("역까지도보(분)", format="%.1f"),
                        "초등학교도보(분)": st.column_config.NumberColumn(
                            "초등학교도보(분)", format="%.1f", help="배정 초등학교까지 실제 직선거리 기준 도보시간(분속 80m 가정)."
                        ),
                        "가격모멘텀(%)": st.column_config.NumberColumn(
                            "가격모멘텀(%)", format="%+.1f%%",
                            help="이 단지 자체의 최근 6개월 평단가가 직전 6개월(6~12개월 전) 대비 얼마나 변했는지. "
                            "다른 단지와 비교하는 저평가율과는 별개 지표입니다.",
                        ),
                    },
                )

                st.caption(
                    "저평가율 = (같은 시군구·법정동·단지점수구간 단지들의 평균 평당가 − 이 단지의 최근 평당가) ÷ 그 평균 × 100. "
                    "즉 다른 단지의 '가격'이 아니라 '단지점수(품질)'가 비슷한 단지들과 비교합니다 — 같은 품질 구간인데 "
                    "더 싸게 거래되고 있으면 양수(+)로 저평가율이 나옵니다. "
                    "**그룹표본수**가 3 미만이면 그 평균이 단지 1~2개만으로 계산된 거라 저평가율을 참고 정도로만 봐주세요 "
                    "(숨기지 않고 그대로 보여드리니 표본수를 직접 확인하시면 됩니다). "
                    "**단지점수**(0~100)는 위 '단지점수 배점 설정'에서 조정한 가중치로 계산한 단지 자체의 조건 점수이자, "
                    "저평가율 그룹을 나누는 기준이기도 합니다."
                )

            with st.expander(f"단지점수를 계산할 수 없어 제외된 단지 ({len(no_info)}개)"):
                st.caption(
                    "세대수·연식·역세권·거래유동성·가격모멘텀이 전부 정보없음이라 단지점수를 계산할 수 없는 단지입니다 "
                    "(보통 실거래가의 단지명과 국토부 등록명 표기가 달라 세대수 등을 못 붙인 경우). "
                    "점수 구간을 정할 수 없으니 아예 비교 대상에서 뺐습니다."
                )
                st.dataframe(
                    no_info[["apt_name", "sigungu", "dong", "세대수구간", "연식구간"]].rename(
                        columns={"apt_name": "단지명", "sigungu": "시군구", "dong": "법정동"}
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

            with st.expander(f"특수거래 의심으로 제외된 거래 ({len(excluded)}건)"):
                st.caption("직거래(중개 없는 거래)이거나, 같은 단지·평형 중앙값 대비 35% 이상 벗어난 거래입니다.")
                st.dataframe(
                    excluded.sort_values("deal_date", ascending=False)[
                        ["deal_date", "apt_name", "price_mn", "price_per_pyeong_mn", "dealing_type"]
                    ].rename(
                        columns={
                            "deal_date": "계약일",
                            "apt_name": "단지명",
                            "price_mn": "거래금액(백만원)",
                            "price_per_pyeong_mn": "평당가(백만원/평)",
                            "dealing_type": "거래유형",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "거래금액(백만원)": price_col_config("거래금액(백만원)"),
                        "평당가(백만원/평)": price_col_config("평당가(백만원/평)"),
                    },
                )

# ------------------------------------------------------------
# 시세 요약
# ------------------------------------------------------------
with tab_summary:
    st.caption("이 탭은 선택한 지역·조건 전체의 시세를 한눈에 요약해서 보여줍니다.")
    st.caption(
        "**평당가** = 거래금액 ÷ (전용면적㎡÷3.3058), 단위는 백만원/평입니다 (100백만원 = 1억원). "
        "여기서 '평형'은 실제 전용면적 기준이며, 분양 때 흔히 쓰는 공급면적 기준 평형(예: '34평형')과는 "
        "다를 수 있어요."
    )

    if not sigungus:
        # ----- 아무 필터도 없을 때: 안내 + 추천 리스트 -----
        st.info("왼쪽 사이드바에서 **지역(시군구)** 을 선택하면 시세 요약이 나타납니다.")

        st.subheader("최근 거래가 활발한 단지 (전체 지역, 최근 6개월)")
        recent_cut = sale["deal_date"].max() - pd.DateOffset(months=6)
        recent_all = sale[sale["deal_date"] > recent_cut]
        top_active = (
            recent_all.groupby(["sigungu", "apt_name"], as_index=False)
            .size()
            .rename(columns={"size": "최근6개월 거래건수"})
            .sort_values("최근6개월 거래건수", ascending=False)
            .head(10)
        )
        st.dataframe(top_active, use_container_width=True, hide_index=True)

    elif not apt_names:
        # ----- 지역만 선택: 지역 요약 -----
        m = compute_summary_metrics(scoped)
        st.subheader(f"{', '.join(sigungus)} 시세 요약")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("최근 3개월 평균 평당가", fmt_mn(m["recent_avg"]) if m else "정보 부족")
        c2.metric("전년 동기 대비", fmt_pct(m["yoy"]) if m else "정보 부족")
        c3.metric("최근 3개월 거래건수", f"{m['recent_count']:,}건" if m else "0건")
        c4.metric("단지 수", f"{scoped['apt_name'].nunique():,}개")

        st.subheader("최근 상승률 상위 단지 (최근 3개월 vs 전년 동기)")
        rows = []
        for apt_name, g in scoped.groupby("apt_name"):
            gm = compute_summary_metrics(g)
            if gm and gm["yoy"] is not None and gm["recent_count"] >= 2:
                rows.append({"단지명": apt_name, "최근평당가(백만원)": gm["recent_avg"], "상승률(%)": gm["yoy"]})
        top_movers = pd.DataFrame(rows).sort_values("상승률(%)", ascending=False).head(10)
        if top_movers.empty:
            st.caption("최근·전년 동기 비교가 가능한 거래가 아직 충분하지 않습니다.")
        else:
            fig = px.bar(top_movers, x="단지명", y="상승률(%)", text="상승률(%)")
            fig.update_traces(texttemplate="%{text:.1f}%")
            st.plotly_chart(fig, use_container_width=True)

        with st.expander("상세 거래 내역 보기"):
            st.dataframe(
                scoped.sort_values("deal_date", ascending=False)[
                    ["deal_date", "apt_name", "area_exclusive_m2", "area_pyeong", "floor", "price_mn", "price_per_pyeong_mn"]
                ].rename(
                    columns={
                        "deal_date": "계약일",
                        "apt_name": "단지명",
                        "area_exclusive_m2": "전용면적(㎡)",
                        "area_pyeong": "전용평",
                        "floor": "층",
                        "price_mn": "거래금액(백만원)",
                        "price_per_pyeong_mn": "평당가(백만원/평)",
                    }
                ),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "거래금액(백만원)": price_col_config("거래금액(백만원)"),
                    "평당가(백만원/평)": price_col_config("평당가(백만원/평)"),
                },
            )

    else:
        # ----- 단지까지 선택 -----
        if len(apt_names) == 1:
            apt = apt_names[0]
            m = compute_summary_metrics(scoped)
            household = scoped["household_count"].dropna().iloc[0] if scoped["household_count"].notna().any() else None

            st.subheader(f"{apt} 시세 요약")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("최근 3개월 평균 평당가", fmt_mn(m["recent_avg"]) if m else "정보 부족")
            c2.metric("전년 동기 대비", fmt_pct(m["yoy"]) if m else "정보 부족")
            c3.metric("최근 3개월 거래건수", f"{m['recent_count']:,}건" if m else "0건")
            c4.metric("세대수", f"{household:,.0f}세대" if household else "정보 없음")

            monthly = (
                scoped.groupby("yyyymm", as_index=False)
                .agg(평당가=("price_per_pyeong_mn", "median"), 거래건수=("price_per_pyeong_mn", "size"))
                .sort_values("yyyymm")
            )
            if monthly["yyyymm"].nunique() <= 1:
                st.info("선택한 조건은 거래가 있었던 달이 하나뿐이라 추이를 보기 어려워요. 평형대를 넓혀보세요.")
            fig = px.line(monthly, x="yyyymm", y="평당가", markers=True)
            fig.update_layout(xaxis_title="계약년월", yaxis_title="평당가(백만원/평)")
            st.plotly_chart(fig, use_container_width=True)

            count_fig = px.bar(monthly, x="yyyymm", y="거래건수")
            count_fig.update_layout(xaxis_title="계약년월", yaxis_title="거래건수(건)")
            st.plotly_chart(count_fig, use_container_width=True)

        else:
            # 여러 단지 비교
            st.subheader("선택한 단지 비교")
            rows = []
            for apt_name, g in scoped.groupby("apt_name"):
                gm = compute_summary_metrics(g)
                if gm:
                    rows.append(
                        {
                            "단지명": apt_name,
                            "최근평당가(백만원)": gm["recent_avg"],
                            "최근거래건수": gm["recent_count"],
                            "상승률(%)": gm["yoy"],
                        }
                    )
            cmp_df = pd.DataFrame(rows).sort_values("상승률(%)", ascending=False, na_position="last")

            c1, c2 = st.columns(2)
            c1.metric("비교 단지 수", f"{len(cmp_df)}개")
            c2.metric("평균 상승률", fmt_pct(cmp_df["상승률(%)"].mean()) if not cmp_df.empty else "정보 부족")

            fig = px.bar(cmp_df.dropna(subset=["상승률(%)"]), x="단지명", y="상승률(%)", text="상승률(%)")
            fig.update_traces(texttemplate="%{text:.1f}%")
            st.plotly_chart(fig, use_container_width=True)

        with st.expander("상세 거래 내역 보기"):
            st.dataframe(
                scoped.sort_values("deal_date", ascending=False)[
                    ["deal_date", "apt_name", "area_exclusive_m2", "area_pyeong", "floor", "price_mn", "price_per_pyeong_mn"]
                ].rename(
                    columns={
                        "deal_date": "계약일",
                        "apt_name": "단지명",
                        "area_exclusive_m2": "전용면적(㎡)",
                        "area_pyeong": "전용평",
                        "floor": "층",
                        "price_mn": "거래금액(백만원)",
                        "price_per_pyeong_mn": "평당가(백만원/평)",
                    }
                ),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "거래금액(백만원)": price_col_config("거래금액(백만원)"),
                    "평당가(백만원/평)": price_col_config("평당가(백만원/평)"),
                },
            )

# ------------------------------------------------------------
# 나머지 탭 — 다음 단계에서 이 구조에 맞춰 채울 예정
# ------------------------------------------------------------
with tab_trend:
    st.caption("이 탭은 특정 단지(들)의 가격이 시간이 지나면서 어떻게 변해왔는지 그래프로 봅니다.")
    st.caption(
        "여러 단지·평형을 한 그래프에 겹쳐서 시간에 따른 가격 흐름을 비교합니다. "
        "사이드바에서 단지를 여러 개 고르면 한눈에 비교할 수 있어요."
    )

    if not apt_names:
        st.info("왼쪽 사이드바에서 지역과 단지를 하나 이상 선택하면 추이 그래프가 나타납니다.")
    else:
        trend = scoped.copy()
        if trend.empty:
            st.info("선택한 조건에 해당하는 거래가 없습니다.")
        else:
            trend_monthly = (
                trend.groupby(["apt_name", "area_bucket", "yyyymm"], as_index=False)
                .agg(평당가=("price_per_pyeong_mn", "median"), 거래건수=("price_per_pyeong_mn", "size"))
                .sort_values("yyyymm")
            )
            trend_monthly["series"] = trend_monthly["apt_name"] + " · " + trend_monthly["area_bucket"]

            if trend_monthly["yyyymm"].nunique() <= 1:
                st.info(
                    "선택한 조건은 거래가 있었던 달이 하나뿐이라 아직 '추이'라고 보기는 어려워요. "
                    "평형대를 넓히거나 다른 단지를 추가해보세요."
                )

            fig = px.line(trend_monthly, x="yyyymm", y="평당가", color="series", markers=True)
            fig.update_layout(xaxis_title="계약년월", yaxis_title="평당가(백만원/평)", legend_title="단지 · 평형대")
            st.plotly_chart(fig, use_container_width=True)

            st.subheader("단지·평형별 월별 거래건수")
            count_fig = px.bar(trend_monthly, x="yyyymm", y="거래건수", color="series", barmode="group")
            count_fig.update_layout(xaxis_title="계약년월", yaxis_title="거래건수(건)", legend_title="단지 · 평형대")
            st.plotly_chart(count_fig, use_container_width=True)

            with st.expander("상세 거래 내역 보기"):
                st.dataframe(
                    trend.sort_values("deal_date", ascending=False)[
                        ["deal_date", "apt_name", "dong", "area_exclusive_m2", "area_pyeong", "floor", "price_mn", "price_per_pyeong_mn"]
                    ].rename(
                        columns={
                            "deal_date": "계약일",
                            "apt_name": "단지명",
                            "dong": "법정동",
                            "area_exclusive_m2": "전용면적(㎡)",
                            "area_pyeong": "전용평",
                            "floor": "층",
                            "price_mn": "거래금액(백만원)",
                            "price_per_pyeong_mn": "평당가(백만원/평)",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "거래금액(백만원)": price_col_config("거래금액(백만원)"),
                        "평당가(백만원/평)": price_col_config("평당가(백만원/평)"),
                    },
                )

with tab_subway:
    st.caption("이 탭은 지하철역까지 거리(역세권)를 기준으로 단지들을 살펴봅니다.")
    st.caption(
        "단지 좌표와 지하철역 좌표 사이 직선거리를 분속 80m로 환산한 **추정 도보시간**입니다 "
        "(실제 도로 경로가 아니라 참고용이에요)."
    )

    subway_scope = df[df["deal_type"] == "매매"]
    if sigungus:
        subway_scope = subway_scope[subway_scope["sigungu"].isin(sigungus)]
    if apt_names:
        subway_scope = subway_scope[subway_scope["apt_name"].isin(apt_names)]

    subway_complexes = subway_scope.drop_duplicates(subset=["sigungu", "dong", "apt_name"])
    subway_coverage = subway_complexes["walk_minutes"].notna().mean() if not subway_complexes.empty else 0

    c1, c2 = st.columns(2)
    c1.metric("역세권 정보 커버리지", f"{subway_coverage:.0%}" if not subway_complexes.empty else "정보 부족")
    c2.metric("대상 단지 수", f"{len(subway_complexes):,}개")

    if subway_coverage == 0:
        st.info(
            "아직 역세권 데이터가 준비되지 않았습니다. "
            "`python scripts/parse_subway_stations.py` 와 `python scripts/compute_walk_time.py` 를 실행해주세요."
        )
    elif not sigungus:
        st.info("왼쪽 사이드바에서 지역을 선택하면 단지별 역세권 정보가 나타납니다.")
    else:
        sub_table = subway_complexes.dropna(subset=["walk_minutes"])[
            ["apt_name", "sigungu", "dong", "nearest_station", "nearest_station_line", "distance_m", "walk_minutes"]
        ].rename(
            columns={
                "apt_name": "단지명",
                "sigungu": "시군구",
                "dong": "법정동",
                "nearest_station": "최근접역",
                "nearest_station_line": "노선",
                "distance_m": "거리(m)",
                "walk_minutes": "도보시간(분)",
            }
        ).sort_values("도보시간(분)")
        st.dataframe(
            sub_table,
            use_container_width=True,
            hide_index=True,
            column_config={
                "거리(m)": st.column_config.NumberColumn("거리(m)", format="%,.0f"),
                "도보시간(분)": st.column_config.NumberColumn("도보시간(분)", format="%.1f"),
            },
        )

        map_data = subway_complexes.dropna(subset=["lat", "lon", "walk_minutes"])
        if not map_data.empty:
            st.subheader("역세권 지도")
            st.caption("초록에 가까울수록 역에서 가깝고, 빨강에 가까울수록 멉니다.")
            sub_map = px.scatter_map(
                map_data,
                lat="lat",
                lon="lon",
                color="walk_minutes",
                hover_name="apt_name",
                hover_data={"nearest_station": True, "walk_minutes": ":.1f", "lat": False, "lon": False},
                color_continuous_scale="RdYlGn_r",
                center={"lat": map_data["lat"].mean(), "lon": map_data["lon"].mean()},
                zoom=10,
                map_style="open-street-map",
                height=500,
            )
            sub_map.update_traces(marker=dict(size=12))
            sub_map.update_layout(margin={"r": 0, "t": 0, "l": 0, "b": 0})
            st.plotly_chart(sub_map, use_container_width=True)

with tab_school:
    st.caption("이 탭은 단지별 배정 초·중학교 정보를 확인합니다.")
    st.caption(
        "초등학교는 통학구역 경계로 배정 학교를 정확히 찾을 수 있어요. "
        "중학교는 서울·경기 대부분이 공동학군(추첨 배정)이라 특정 학교 하나로 확정할 수 없어서, "
        "**어느 학군(학교군)에 속하는지**로 대신 보여드립니다. 고등학교는 아직 준비 전입니다. "
        "(현재는 일부 지역만 좌표 변환·매칭이 끝나 있어요 — 진행 상황은 아래 커버리지 참고)"
    )

    school_scope = df[df["deal_type"] == "매매"]
    if sigungus:
        school_scope = school_scope[school_scope["sigungu"].isin(sigungus)]
    if apt_names:
        school_scope = school_scope[school_scope["apt_name"].isin(apt_names)]

    complexes = school_scope.drop_duplicates(subset=["sigungu", "dong", "apt_name"])
    coverage = complexes["elementary_schools"].notna().mean() if not complexes.empty else 0

    c1, c2 = st.columns(2)
    c1.metric("배정학교 매칭 커버리지", f"{coverage:.0%}" if not complexes.empty else "정보 부족")
    c2.metric("대상 단지 수", f"{len(complexes):,}개")

    if not sigungus:
        st.info("왼쪽 사이드바에서 지역을 선택하면 단지별 배정학교 목록이 나타납니다.")
    else:
        table = complexes[["apt_name", "sigungu", "dong", "elementary_schools", "elementary_match_type", "middle_schools"]].rename(
            columns={
                "apt_name": "단지명",
                "sigungu": "시군구",
                "dong": "법정동",
                "elementary_schools": "배정초등학교",
                "elementary_match_type": "매칭방식",
                "middle_schools": "중학군",
            }
        )
        table["배정초등학교"] = table["배정초등학교"].fillna("정보없음")
        table["중학군"] = table["중학군"].fillna("정보없음")
        table["매칭방식"] = table["매칭방식"].fillna("-")
        st.dataframe(table.sort_values("단지명"), use_container_width=True, hide_index=True)
        st.caption(
            "매칭방식 '단일배정'은 통학구역 폴리곤으로 확정된 경우, '최단거리추정'은 통학구역 밖이라 "
            "가장 가까운 학교로 대체 추정한 경우입니다."
        )

with tab_household:
    st.caption("이 탭은 세대수 규모(단지 크기)별로 최근 가격 상승률을 비교합니다.")
    st.caption(
        "몇 세대 이상 대단지가 소규모 단지보다 최근 더 많이 올랐는지 비교합니다. "
        "(세대수는 국토교통부 공동주택 기본정보 기준, 주상복합/오피스텔형은 아파트와 특성이 달라 기본 제외됩니다.)"
    )

    hh_scope = sale[sale["building_type"] == "아파트"]
    if sigungus:
        hh_scope = hh_scope[hh_scope["sigungu"].isin(sigungus)]

    col_hh1, col_hh2, col_hh3 = st.columns(3)
    with col_hh1:
        min_household = st.number_input("최소 세대수", min_value=0, value=0, step=100)
    with col_hh2:
        hh_recent_months = st.slider("최근 N개월 평균", 1, 24, 6, key="hh_months")
    with col_hh3:
        hh_min_count = st.number_input("비교 최소 거래건수(최근 구간)", min_value=1, value=2)

    if not sigungus:
        st.info("왼쪽 사이드바에서 지역을 하나 이상 선택하면 세대수별 비교가 나타납니다.")
    else:
        hh_scope = hh_scope[hh_scope["household_count"].fillna(0) >= min_household]
        hh_max_date = hh_scope["deal_date"].max()

        if pd.isna(hh_max_date):
            st.info("선택한 조건에 해당하는 거래가 없습니다. (세대수 정보가 없는 단지는 제외됩니다)")
        else:
            hh_recent_start = hh_max_date - pd.DateOffset(months=hh_recent_months)
            hh_prev_start = hh_recent_start - pd.DateOffset(months=hh_recent_months)
            hh_recent = hh_scope[hh_scope["deal_date"] > hh_recent_start]
            hh_prev = hh_scope[(hh_scope["deal_date"] > hh_prev_start) & (hh_scope["deal_date"] <= hh_recent_start)]

            def _hh_agg(d: pd.DataFrame, prefix: str) -> pd.DataFrame:
                return d.groupby(["apt_name", "sigungu", "household_count", "household_bucket"], as_index=False).agg(
                    **{f"{prefix}평균평당가": ("price_per_pyeong_mn", "mean"), f"{prefix}거래건수": ("price_per_pyeong_mn", "size")}
                )

            hh_merged = _hh_agg(hh_recent, "최근").merge(
                _hh_agg(hh_prev, "직전"), on=["apt_name", "sigungu", "household_count", "household_bucket"], how="left"
            )
            hh_merged = hh_merged[hh_merged["최근거래건수"] >= hh_min_count]
            hh_merged["변동률(%)"] = (
                (hh_merged["최근평균평당가"] - hh_merged["직전평균평당가"]) / hh_merged["직전평균평당가"] * 100
            ).round(1)

            st.caption(
                f"최근 {hh_recent_months}개월({hh_recent_start.date()} ~ {hh_max_date.date()}) vs "
                f"직전 {hh_recent_months}개월 평균 평당가(백만원/평) 비교"
            )

            st.subheader("세대수 구간별 평균 상승률")
            bucket_order = [b[2] for b in HOUSEHOLD_BUCKETS]
            bucket_stat = hh_merged.dropna(subset=["변동률(%)"]).groupby("household_bucket", as_index=False)["변동률(%)"].mean().round(1)
            if bucket_stat.empty:
                st.caption("최근·직전 구간 모두 거래가 있는 단지가 아직 부족합니다.")
            else:
                bucket_stat["household_bucket"] = pd.Categorical(bucket_stat["household_bucket"], categories=bucket_order, ordered=True)
                bucket_stat = bucket_stat.sort_values("household_bucket")
                bfig = px.bar(bucket_stat, x="household_bucket", y="변동률(%)", text="변동률(%)")
                bfig.update_traces(texttemplate="%{text:.1f}%")
                bfig.update_layout(xaxis_title="세대수 구간", yaxis_title="평균 변동률(%)")
                st.plotly_chart(bfig, use_container_width=True)

            st.subheader("단지별 상승률 순위")
            hh_display = hh_merged.rename(
                columns={
                    "apt_name": "단지명", "sigungu": "시군구", "household_count": "세대수", "household_bucket": "세대수구간",
                    "최근평균평당가": "최근평균평당가(백만원/평)", "직전평균평당가": "직전평균평당가(백만원/평)",
                }
            ).sort_values("변동률(%)", ascending=False, na_position="last")
            hh_display["세대수"] = hh_display["세대수"].astype("Int64")
            st.dataframe(
                hh_display,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "최근평균평당가(백만원/평)": price_col_config("최근평균평당가(백만원/평)"),
                    "직전평균평당가(백만원/평)": price_col_config("직전평균평당가(백만원/평)"),
                    "세대수": st.column_config.NumberColumn("세대수", format="%,d"),
                },
            )
