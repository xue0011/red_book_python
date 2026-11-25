"""可复用的轻量 UI 组件（纯 Streamlit，不引第三方 CSS 框架）。"""
from __future__ import annotations

import html
from typing import Any, Iterable

import streamlit as st

from config import settings


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def page_header(title: str, subtitle: str = "") -> None:
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def hero_card(label: str, text: str, footnote: str = "") -> None:
    """深色结论卡片：一句话定位这类"先看结论"的内容。"""
    note = f'<div style="font-size:12px;opacity:.65;margin-top:10px;">{esc(footnote)}</div>' if footnote else ""
    st.markdown(
        f"""
        <div style="padding:22px 26px;border-radius:16px;
                    background:linear-gradient(135deg,#111827 0%,#374151 100%);
                    color:#fff;margin-bottom:6px;">
          <div style="font-size:12px;letter-spacing:3px;opacity:.65;">{esc(label)}</div>
          <div style="font-size:26px;font-weight:700;line-height:1.45;margin-top:10px;">{esc(text)}</div>
          {note}
        </div>
        """,
        unsafe_allow_html=True,
    )


def chips(items: Iterable[str], color: str = "#eef2ff") -> None:
    """标签胶囊列表。"""
    values = [esc(item) for item in items if str(item).strip()]
    if not values:
        st.caption("—")
        return
    spans = "".join(
        f'<span style="display:inline-block;padding:5px 12px;margin:0 8px 8px 0;'
        f'border-radius:999px;background:{color};font-size:13px;">{item}</span>'
        for item in values
    )
    st.markdown(f"<div>{spans}</div>", unsafe_allow_html=True)


def bullets(items: Iterable[str]) -> None:
    values = [str(item).strip() for item in items if str(item).strip()]
    if not values:
        st.caption("—")
        return
    st.markdown("\n".join(f"- {value}" for value in values))


def card_grid(cards: list[dict[str, Any]], per_row: int = 3) -> None:
    """卡片墙：每张卡片 = {icon, title, desc, status}。"""
    if not cards:
        return
    for start in range(0, len(cards), per_row):
        row = cards[start : start + per_row]
        columns = st.columns(per_row, gap="medium")
        for column, card in zip(columns, row):
            with column:
                with st.container(border=True):
                    st.markdown(f"### {card.get('icon', '')} {card.get('title', '')}")
                    st.caption(card.get("desc", ""))
                    st.markdown(f"`{card.get('status', '规划中')}`")


def sidebar_footer() -> None:
    """侧边栏底部：模型信息 + Key 状态。"""
    with st.sidebar:
        st.divider()
        if not settings.api_key_ok:
            st.warning("未检测到 DEEPSEEK_API_KEY")
