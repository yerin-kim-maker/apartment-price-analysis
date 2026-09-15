import plotly.express as px
import streamlit as st

from utils.data_loader import AREA_BUCKETS, load_transactions

st.set_page_config(page_title="기간별 가격추이", page_icon="📈", layout="wide")
st.title("기간별·단지별·평형별 가격 추이")
st.caption(
    "왼쪽 사이드바에서 **시군구 → 단지명**을 고르면, 그 단지가 시간이 지나면서 얼마에 거래됐는지 "
    "선 그래프로 보여줍니다. 단지를 여러 개 고르면 한 그래프에 겹쳐서 비교할 수 있어요."
)

df = load_transactions()
if df.empty:
    st.warning("데이터가 없습니다. 먼저 데이터_업데이트.bat를 실행하세요.")
    st.stop()

sale_only = df[df["deal_type"] == "매매"].dropna(subset=["deal_date"])

with st.sidebar:
    st.header("필터")
    sigungu_list = sorted(sale_only["sigungu"].unique())
    sigungu = st.selectbox("시군구", sigungu_list)
    apt_options = sorted(sale_only.loc[sale_only["sigungu"] == sigungu, "apt_name"].dropna().unique())
    apt_names = st.multiselect(
        "단지명 (여러 개 선택하면 함께 비교됩니다)",
        apt_options,
        default=apt_options[:1] if apt_options else [],
    )
    bucket_options = [b[2] for b in AREA_BUCKETS]
    buckets = st.multiselect("평형대", bucket_options, default=bucket_options)
    metric = st.radio("가격 기준", ["평당가(만원/평)", "거래총액(만원)"])

if not apt_names:
    st.info("왼쪽에서 단지를 하나 이상 선택해주세요.")
    st.stop()

filtered = sale_only[sale_only["apt_name"].isin(apt_names) & sale_only["area_bucket"].isin(buckets)]

if filtered.empty:
    st.info("선택한 조건에 해당하는 거래가 없습니다.")
    st.stop()

y_col = "price_per_pyeong_10k" if metric.startswith("평당가") else "price_10k"

monthly = (
    filtered.groupby(["apt_name", "area_bucket", "yyyymm"], as_index=False)
    .agg(price=(y_col, "median"), 거래건수=(y_col, "size"))
    .sort_values("yyyymm")
)
monthly["series"] = monthly["apt_name"] + " · " + monthly["area_bucket"]

if monthly["yyyymm"].nunique() <= 1:
    st.info(
        "선택한 조건은 거래가 있었던 달이 하나뿐이라 아직 '추이'라고 보기는 어려워요. "
        "평형대를 더 넓게 선택하거나, 기간을 늘려 데이터를 더 받아보면 선이 이어집니다."
    )

fig = px.line(monthly, x="yyyymm", y="price", color="series", markers=True)
fig.update_layout(xaxis_title="계약년월", yaxis_title=metric, legend_title="단지 · 평형대")
st.plotly_chart(fig, use_container_width=True)

st.subheader("단지·평형별 월별 거래건수")
count_fig = px.bar(monthly, x="yyyymm", y="거래건수", color="series", barmode="group")
count_fig.update_layout(xaxis_title="계약년월", yaxis_title="거래건수", legend_title="단지 · 평형대")
st.plotly_chart(count_fig, use_container_width=True)

total_by_month = filtered.groupby("yyyymm", as_index=False).size().rename(columns={"size": "거래건수"})
st.caption(
    f"선택한 단지·평형 전체 합산 거래건수: {filtered.shape[0]:,}건 "
    f"(기간 내 월평균 {total_by_month['거래건수'].mean():.1f}건)"
)

st.subheader("거래 내역")
st.dataframe(
    filtered.sort_values("deal_date", ascending=False)[
        ["deal_date", "apt_name", "dong", "area_exclusive_m2", "area_pyeong", "floor", "price_10k", "price_per_pyeong_10k"]
    ].rename(
        columns={
            "deal_date": "계약일",
            "apt_name": "단지명",
            "dong": "법정동",
            "area_exclusive_m2": "전용면적(㎡)",
            "area_pyeong": "전용평",
            "floor": "층",
            "price_10k": "거래금액(만원)",
            "price_per_pyeong_10k": "평당가(만원)",
        }
    ),
    use_container_width=True,
    hide_index=True,
)
