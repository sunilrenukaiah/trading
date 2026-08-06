"""Streamlit page — pattern formulas, explanations, and example charts."""

from __future__ import annotations

import streamlit as st

from app.services.pattern_definitions import (
    build_pattern_example_chart,
    list_pattern_definitions,
    pattern_categories,
)


def _signal_badge(signal: str) -> str:
    s = signal.upper()
    if s == "BULLISH":
        return "🟢 Bullish"
    if s == "BEARISH":
        return "🔴 Bearish"
    return "⚪ Both directions"


def render_pattern_definitions_page() -> None:
    definitions = list_pattern_definitions()
    categories = pattern_categories()

    st.caption(
        "Reference for every pattern used in backtesting and recommendations. "
        "Formulas match the rules in the strategy registry; charts are synthetic examples "
        "that illustrate each pattern shape (not live market data)."
    )

    col_filter, col_search = st.columns([1, 2])
    with col_filter:
        category = st.selectbox("Category", ["All"] + categories, key="pattern_def_category")
    with col_search:
        query = st.text_input("Search patterns", placeholder="e.g. morning star, RSI, Bollinger", key="pattern_def_search")

    filtered = definitions
    if category != "All":
        filtered = [row for row in filtered if row.category == category]
    if query.strip():
        q = query.strip().lower()
        filtered = [
            row
            for row in filtered
            if q in row.name.lower() or q in row.pattern_id.lower() or q in row.formula.lower()
        ]

    if not filtered:
        st.warning("No patterns match your filters.")
        return

    st.metric("Patterns shown", len(filtered))

    pattern_labels = {f"{row.name} ({row.category})": row.pattern_id for row in filtered}
    label = st.selectbox("Select pattern", list(pattern_labels.keys()), key="pattern_def_select")
    selected = next(row for row in filtered if row.pattern_id == pattern_labels[label])

    st.subheader(selected.name)
    badge_col, meta_col = st.columns([1, 3])
    with badge_col:
        st.markdown(f"**Signal:** {_signal_badge(selected.signal)}")
        st.markdown(f"**Category:** {selected.category}")
    with meta_col:
        st.markdown(f"**Pattern ID:** `{selected.pattern_id}` · **Lookback:** {selected.lookback_days} bars")

    st.markdown("#### Formula")
    st.markdown(selected.formula.replace("\n", "\n\n"))

    st.markdown("#### How it calculates")
    st.write(selected.explanation)

    st.markdown("#### Example")
    st.caption("Highlighted region marks the pattern-forming candles. Arrow shows typical bias after detection.")
    fig = build_pattern_example_chart(selected.pattern_id)
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("All patterns in this view"):
        summary_rows = [
            {
                "Pattern": row.name,
                "Category": row.category,
                "Signal": row.signal.title(),
                "ID": row.pattern_id,
            }
            for row in filtered
        ]
        st.dataframe(summary_rows, use_container_width=True, hide_index=True)
