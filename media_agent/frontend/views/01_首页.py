"""首页：应用启动页（六个模块入口 + 本次操作记录 + 版本进度）。

原来 `frontend/home.py: show_home()` 与这里重复，已合并到本文件；
`frontend/home.py` 可删除，本项目不再有重复首页组件。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from config import settings
from frontend.components.ui import sidebar_footer

# 六个模块：图标 / 标题 / 一句话说明 / 内部流程 / 页面路径
MODULES = [
    ("🎯", "账号定位", "输入背景信息，AI 生成专属定位方案",
     "分析画像 → 对标账号 → 输出方案", "views/02_账号定位.py"),
    ("🔥", "热点监控", "实时追踪平台热点，筛选赛道相关选题",
     "TrendRadar 抓取 → LLM 筛选 → 选题建议", "views/03_热点监控.py"),
    ("📝", "内容复刻", "下载爆款视频，拆解结构后仿写新文案",
     "yt-dlp 下载 → FunASR 转文字 → LLM 仿写", "views/04_内容复刻.py"),
    ("🎥", "口播视频", "文案 → 口播优化 → 数字人出镜",
     "文案 → TTS 配音 → HeyGem 数字人 → 4K 视频", "views/05_口播视频.py"),
    ("🎬", "视频剪辑", "口播视频后期：字幕 / 特效 / 转场",
     "素材 → FunASR 字幕 → 叠加层 → 渲染 MP4", "views/06_视频剪辑.py"),
    ("📊", "数据复盘", "多维数据诊断，输出优化策略",
     "采集 → 漏斗分析 → 质量评估 → 改进方案", "views/07_数据复盘.py"),
]

STAGES = """
| 版本 | 目标 | 状态 |
|---|---|---|
| **v1** | 工程骨架 + 账号定位 + 热点监控 | 🟡 待验收 |
| **v2** | 内容复刻 / 口播视频 + 跨页传参 | 🟢 已实现，待联调 |
| **v3** | 视频剪辑 / 数据复盘 + 体验优化 | 🟢 已实现，待联调 |
"""


def _module_card(icon: str, title: str, desc: str, flow: str, page: str) -> None:
    """一张可点击的模块卡（点标题即跳转）。"""
    with st.container(border=True):
        st.markdown(f"### {icon} {title}")
        st.markdown(f"<p style='color:#555;font-size:14px;margin:2px 0 10px 0'>{desc}</p>",
                    unsafe_allow_html=True)
        st.caption(f"⚙️ {flow}")
        st.page_link(page, label="进入 →", icon=icon)


def show_home() -> None:
    """首页内容（应用启动时的默认页）。"""
    st.title("🎬 自媒体运营 AI Agent")
    st.markdown(
        "一个本地可跑的工作台：**账号定位 → 热点监控 → 内容复刻 → 口播视频 → 视频剪辑 → 数据复盘**，"
        "六件事共用一套 LangGraph + DeepSeek 流水线。"
    )

    st.divider()

    # 六个模块：3 列 × 2 行
    for row in range(0, len(MODULES), 3):
        cols = st.columns(3, gap="medium")
        for col, item in zip(cols, MODULES[row:row + 3]):
            with col:
                _module_card(*item)

    st.divider()

    left, right = st.columns([3, 2], gap="large")

    with left:
        st.subheader("快速开始")
        st.markdown(
            "1. 先在 **账号定位** 里填一次背景（结果会自动带给热点监控）\n"
            "2. 每天在 **热点监控** 里挑 1 个选题开拍\n"
            "3. 拍完用 **口播视频 / 视频剪辑** 出片\n"
            "4. 发出去 24 小时后回 **数据复盘** 看漏斗"
        )
        if st.session_state.get("bridge", {}).get("positioning"):
            track = st.session_state["bridge"]["positioning"].get("track", "")
            st.success(f"已带入定位赛道：{track or '（未填写）'}", icon="📌")

    # 本次操作记录（各页面 _add_history 写入）
    history = st.session_state.get("history") or []
    if history:
        with st.expander(f"📋 本次操作记录（{len(history)} 条）", expanded=False):
            for h in reversed(history[-20:]):
                st.caption(f"🕐 {h['time']} | {h['action']} | {h['summary'][:100]}")

    if not settings.api_key_ok:
        st.warning(
            "未检测到 `DEEPSEEK_API_KEY`。请复制 `.env.example` 为 `.env`，"
            "填入 `DEEPSEEK_API_KEY=sk-...` 后重启应用；否则生成会直接报错。"
        )

    sidebar_footer()


show_home()
