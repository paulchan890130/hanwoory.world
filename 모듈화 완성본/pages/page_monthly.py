# pages/page_monthly.py

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

from .page_daily import load_daily   # 일일 데이터 로드 재사용


def render():
    """
    월간결산 페이지 렌더링.
    app.py 에서 current_page_to_display == PAGE_MONTHLY 일 때 호출.
    """

    st.subheader("📅 월간결산")

    # 1) 구글 시트 전체 일일결산 데이터 로드
    all_daily = load_daily()
    if not all_daily:
        st.info("일일결산 데이터가 없습니다.")
        return

    df = pd.DataFrame(all_daily)

    if "date" not in df.columns:
        st.warning("일일결산 데이터에 'date' 컬럼이 없습니다.")
        return

    # 날짜 타입 변환
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    if df.empty:
        st.info("유효한 날짜 데이터가 없습니다.")
        return

    # 숫자 컬럼 안전 변환
    for col in ["income_cash", "income_etc", "exp_cash", "exp_etc"]:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # 2) ‘수익’·‘매출’ 컬럼 추가
    df["수익"] = (
        df["income_cash"].fillna(0)
        + df["income_etc"].fillna(0)
        - df["exp_cash"].fillna(0)
        - df["exp_etc"].fillna(0)
    )
    df["매출"] = (
        df["income_cash"].fillna(0)
        + df["income_etc"].fillna(0)
    )

    # 3) 월 단위 키(YYYY-MM) 생성
    df["month"] = df["date"].dt.to_period("M").astype(str)

    # 4) 월별 집계 테이블 준비
    monthly_summary = (
        df.groupby("month")
        .agg(
            현금입금=("income_cash", "sum"),
            기타입금=("income_etc", "sum"),
            현금지출=("exp_cash", "sum"),
            기타지출=("exp_etc", "sum"),
            매출=("매출", "sum"),
            순수익=("수익", "sum"),
        )
        .reset_index()
        .sort_values("month")
    )

    if monthly_summary.empty:
        st.info("집계할 월간 데이터가 없습니다.")
        return

    # 5) 분석할 월 선택박스 (기본: 가장 최근 달)
    months = monthly_summary["month"].tolist()
    selected_month = st.selectbox(
        "🔎 분석할 월 선택",
        options=months,
        index=len(months) - 1,
        format_func=lambda x: x.replace("-", "년 ") + "월",
    )

    # 6) 선택된 월 데이터만 필터
    df_sel = df[df["month"] == selected_month].copy()
    if df_sel.empty:
        st.info("선택한 월에 해당하는 데이터가 없습니다.")
        return

    # 7) 전체 월 요약 테이블 출력
    st.markdown("### 📊 월별 요약")
    st.dataframe(
        monthly_summary.rename(columns={"month": "월"}).style.format(
            {
                "현금입금": "{:,} 원",
                "기타입금": "{:,} 원",
                "현금지출": "{:,} 원",
                "기타지출": "{:,} 원",
                "매출": "{:,} 원",
                "순수익": "{:,} 원",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    # 8) 월별 순수익 추이 (라인 차트)
    fig1, ax1 = plt.subplots(figsize=(10, 4))
    ax1.plot(
        monthly_summary["month"],
        monthly_summary["순수익"],
        marker="o",
        linewidth=2,
    )
    ax1.set_title("월별 순수익 추이", fontsize=14)
    ax1.set_xlabel("월", fontsize=12)
    ax1.set_ylabel("순수익 (원)", fontsize=12)
    ax1.grid(True, linestyle="--", alpha=0.5)
    ax1.tick_params(axis="x", rotation=45)
    st.pyplot(fig1)

    # 9) 선택월 요일별 순수익 (바 차트)
    order_en = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    order_ko = ["월", "화", "수", "목", "금", "토", "일"]

    df_sel["weekday"] = df_sel["date"].dt.day_name()
    weekday_sum = (
        df_sel.groupby("weekday")["수익"]
        .sum()
        .reindex(order_en)
        .fillna(0)
    )

    fig2, ax2 = plt.subplots(figsize=(8, 4))
    bars = ax2.bar(order_ko, weekday_sum.values)
    ax2.set_title(f"{selected_month.replace('-', '년 ')}월 요일별 순수익", fontsize=14)
    ax2.set_xlabel("요일", fontsize=12)
    ax2.set_ylabel("순수익 (원)", fontsize=12)
    ax2.grid(axis="y", linestyle="--", alpha=0.5)
    for bar in bars:
        h = bar.get_height()
        ax2.text(
            bar.get_x() + bar.get_width() / 2,
            h * 1.01,
            f"{int(h):,}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    st.pyplot(fig2)

    # 10) 업무 분류별 순수익
    mapping = {
        "출입국": "출입국",
        "등록": "출입국",
        "연장": "출입국",
        "변경": "출입국",
        "전자민원": "전자민원",
        "공증": "공증",
        "영주권": "영주",
        # 나머지는 모두 '기타'
    }

    df_sel["class_cat"] = (
        df_sel["category"]
        .fillna("기타")
        .apply(lambda x: mapping.get(x, "기타"))
    )

    categories = ["출입국", "전자민원", "공증", "영주", "기타"]

    task_sum = (
        df_sel.groupby("class_cat")["수익"]
        .sum()
        .reindex(categories, fill_value=0)
    )

    fig3, ax3 = plt.subplots(figsize=(8, 4))
    bars3 = ax3.bar(task_sum.index, task_sum.values)
    ax3.set_title(f"{selected_month.replace('-', '년 ')}월 업무별 순수익", fontsize=14)
    ax3.set_xlabel("업무 분류", fontsize=12)
    ax3.set_ylabel("순수익 (원)", fontsize=12)
    ax3.grid(axis="y", linestyle="--", alpha=0.5)
    for bar in bars3:
        h = bar.get_height()
        ax3.text(
            bar.get_x() + bar.get_width() / 2,
            h * 1.01,
            f"{int(h):,}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    st.pyplot(fig3)

    # 11) 선택월 시간대별 매출(= 순수익 기준) 바 차트
    def classify_time(t):
        try:
            h = int(str(t).split(":")[0])
            if h < 11:
                return "오전 (00-11시)"
            elif h < 14:
                return "점심 (11-14시)"
            elif h < 18:
                return "오후 (14-18시)"
            else:
                return "저녁 (18-24시)"
        except Exception:
            return "시간정보없음"

    df_sel["time_group"] = df_sel["time"].apply(classify_time)
    time_order = [
        "오전 (00-11시)",
        "점심 (11-14시)",
        "오후 (14-18시)",
        "저녁 (18-24시)",
        "시간정보없음",
    ]
    time_profit = (
        df_sel.groupby("time_group")["수익"]
        .sum()
        .reindex(time_order)
        .fillna(0)
    )

    fig4, ax4 = plt.subplots(figsize=(8, 4))
    bars4 = ax4.bar(time_order, time_profit.values)
    ax4.set_title(f"{selected_month.replace('-', '년 ')}월 시간대별 순수익", fontsize=14)
    ax4.set_xlabel("시간대", fontsize=12)
    ax4.set_ylabel("순수익 (원)", fontsize=12)
    ax4.grid(axis="y", linestyle="--", alpha=0.5)
    ax4.tick_params(axis="x", rotation=45)

    for bar, val in zip(bars4, time_profit.values):
        ax4.text(
            bar.get_x() + bar.get_width() / 2,
            val * 1.01,
            f"{int(val):,}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    st.pyplot(fig4)
