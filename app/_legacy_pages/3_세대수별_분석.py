import pandas as pd
import plotly.express as px
import streamlit as st

from utils.data_loader import HOUSEHOLD_BUCKETS, load_transactions_with_household

st.set_page_config(page_title="세대수별 분석", page_icon="🏙️", layout="wide")
st.title("세대수별 가격 상승률 분석")
st.caption("몇 세대 이상 대단지가 최근 얼마나 올랐는지 비교합니다. (세대수는 국토교통부 공동주택 기본정보 기준)")

df = load_transactions_with_household()
if df.empty:
    st.warning("데이터가 없습니다. 먼저 데이터_업데이트.bat를 실행하세요.")
    st.stop()

sale = df[df["deal_type"] == "매매"].dropna(subset=["deal_date"])

if sale["household_count"].notna().sum() == 0:
    st.warning(
        "단지 세대수 정보가 아직 없습니다. "
        "`python scripts/fetch_complex_master.py` 를 먼저 실행해서 세대수 데이터를 받아주세요."
    )
    st.stop()

_match_rate = sale["household_count"].notna().mean()
st.caption(
    f"실거래가 단지명과 국토부 등록 단지명을 자동으로 맞춰 세대수를 붙였습니다 "
    f"(현재 매칭률 {_match_rate:.0%}). 표기가 많이 달라 못 붙은 단지는 세대수 분석에서 제외됩니다."
)

with st.sidebar:
    st.header("필터")
    sigungu_list = sorted(sale["sigungu"].unique())
    sigungus = st.multiselect("시군구 (여러 개 선택 가능)", sigungu_list, default=sigungu_list[:1])
    min_household = st.number_input("최소 세대수", min_value=0, value=0, step=100)
    recent_months = st.slider("최근 N개월 평균", 1, 24, 6)
    min_count = st.number_input("비교에 포함할 최소 거래건수(최근 구간 기준)", min_value=1, value=2)

if not sigungus:
    st.info("왼쪽에서 시군구를 하나 이상 선택해주세요.")
    st.stop()

scoped = sale[sale["sigungu"].isin(sigungus) & (sale["household_count"].fillna(0) >= min_household)]
max_date = scoped["deal_date"].max()

if pd.isna(max_date):
    st.info("선택한 조건에 해당하는 거래가 없습니다. (세대수 정보가 없는 단지는 제외됩니다)")
    st.stop()

recent_start = max_date - pd.DateOffset(months=recent_months)
prev_start = recent_start - pd.DateOffset(months=recent_months)
recent = scoped[scoped["deal_date"] > recent_start]
prev = scoped[(scoped["deal_date"] > prev_start) & (scoped["deal_date"] <= recent_start)]


def _agg(d: pd.DataFrame, prefix: str) -> pd.DataFrame:
    return d.groupby(["apt_name", "sigungu", "household_count", "household_bucket"], as_index=False).agg(
        **{f"{prefix}평균평당가": ("price_per_pyeong_10k", "mean"), f"{prefix}거래건수": ("price_per_pyeong_10k", "size")}
    )


recent_agg = _agg(recent, "최근")
prev_agg = _agg(prev, "직전")
merged = recent_agg.merge(prev_agg, on=["apt_name", "sigungu", "household_count", "household_bucket"], how="left")
merged = merged[merged["최근거래건수"] >= min_count]
merged["변동률(%)"] = (
    (merged["최근평균평당가"] - merged["직전평균평당가"]) / merged["직전평균평당가"] * 100
).round(1)

st.caption(
    f"최근 {recent_months}개월({recent_start.date()} ~ {max_date.date()}) vs "
    f"직전 {recent_months}개월 평균 평당가(만원/평) 비교"
)

st.subheader("세대수 구간별 평균 상승률")
bucket_order = [b[2] for b in HOUSEHOLD_BUCKETS]
bucket_stat = (
    merged.dropna(subset=["변동률(%)"])
    .groupby("household_bucket", as_index=False)["변동률(%)"]
    .mean()
    .round(1)
)
bucket_stat["household_bucket"] = pd.Categorical(bucket_stat["household_bucket"], categories=bucket_order, ordered=True)
bucket_stat = bucket_stat.sort_values("household_bucket")
fig = px.bar(bucket_stat, x="household_bucket", y="변동률(%)", text="변동률(%)")
fig.update_layout(xaxis_title="세대수 구간", yaxis_title="평균 변동률(%)")
st.plotly_chart(fig, use_container_width=True)

st.subheader("단지별 상승률 순위")
display = merged.rename(
    columns={"apt_name": "단지명", "sigungu": "시군구", "household_count": "세대수", "household_bucket": "세대수구간"}
).sort_values("변동률(%)", ascending=False, na_position="last")
display["세대수"] = display["세대수"].astype("Int64")
st.dataframe(display, use_container_width=True, hide_index=True)
