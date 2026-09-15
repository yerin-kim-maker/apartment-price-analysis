import pandas as pd
import streamlit as st

from utils.data_loader import AREA_BUCKETS, load_transactions

st.set_page_config(page_title="단지·평형별 비교", page_icon="🏘️", layout="wide")
st.title("단지·평형별 가격 비교")
st.caption(
    "1번 페이지처럼 단지를 직접 고르지 않고, **시군구를 고르면 그 안의 모든 단지를 자동으로 랭킹**으로 보여줍니다. "
    "예: 최근 6개월 평균 평당가를 그 직전 6개월과 비교해서, 변동률(%)이 높은 순으로 정렬합니다."
)

df = load_transactions()
if df.empty:
    st.warning("데이터가 없습니다. 먼저 데이터_업데이트.bat를 실행하세요.")
    st.stop()

sale = df[df["deal_type"] == "매매"].dropna(subset=["deal_date"])

with st.sidebar:
    st.header("필터")
    sigungu_list = sorted(sale["sigungu"].unique())
    sigungus = st.multiselect("시군구 (여러 개 선택 가능)", sigungu_list, default=sigungu_list[:1])
    bucket_options = [b[2] for b in AREA_BUCKETS]
    buckets = st.multiselect("평형대", bucket_options, default=bucket_options)
    recent_months = st.slider("최근 N개월 평균", 1, 24, 6)
    min_count = st.number_input("비교에 포함할 최소 거래건수(최근 구간 기준)", min_value=1, value=2)

if not sigungus:
    st.info("왼쪽에서 시군구를 하나 이상 선택해주세요.")
    st.stop()

scoped = sale[sale["sigungu"].isin(sigungus) & sale["area_bucket"].isin(buckets)]
max_date = scoped["deal_date"].max()

if pd.isna(max_date):
    st.info("선택한 조건에 해당하는 거래가 없습니다.")
    st.stop()

recent_start = max_date - pd.DateOffset(months=recent_months)
prev_start = recent_start - pd.DateOffset(months=recent_months)

recent = scoped[scoped["deal_date"] > recent_start]
prev = scoped[(scoped["deal_date"] > prev_start) & (scoped["deal_date"] <= recent_start)]


def _agg(d: pd.DataFrame, prefix: str) -> pd.DataFrame:
    return d.groupby(["apt_name", "sigungu", "area_bucket"], as_index=False).agg(
        **{f"{prefix}평균평당가": ("price_per_pyeong_10k", "mean"), f"{prefix}거래건수": ("price_per_pyeong_10k", "size")}
    )


recent_agg = _agg(recent, "최근")
prev_agg = _agg(prev, "직전")

merged = recent_agg.merge(prev_agg, on=["apt_name", "sigungu", "area_bucket"], how="left")
merged = merged[merged["최근거래건수"] >= min_count]
merged["변동률(%)"] = (
    (merged["최근평균평당가"] - merged["직전평균평당가"]) / merged["직전평균평당가"] * 100
).round(1)
merged["최근평균평당가"] = merged["최근평균평당가"].round(0)
merged["직전평균평당가"] = merged["직전평균평당가"].round(0)

st.caption(
    f"최근 {recent_months}개월({recent_start.date()} ~ {max_date.date()}) vs "
    f"직전 {recent_months}개월 평균 평당가(만원/평) 비교. 직전 구간에 거래가 없으면 변동률은 비어 있습니다."
)

display = merged.rename(
    columns={"apt_name": "단지명", "sigungu": "시군구", "area_bucket": "평형대"}
).sort_values("변동률(%)", ascending=False, na_position="last")

st.dataframe(display, use_container_width=True, hide_index=True)
