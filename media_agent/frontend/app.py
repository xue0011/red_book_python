"""自媒体运营 AI Agent —— Streamlit 入口（前端层）。

运行：``uv run streamlit run frontend/app.py``
（在项目根目录执行，``.env`` 与 ``./.cache`` 都按根目录解析）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from config import settings

st.set_page_config(
    page_title="自媒体运营 AI Agent",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 各页面都会往这个列表里追加"本次操作记录"（见 _add_history），这里统一初始化
st.session_state.setdefault("history", [])

with st.sidebar:
    st.caption(f"模型 `{settings.model_name}`")
    try:
        from db.session import health as _db_health

        st.caption(f"数据库 · {_db_health()}")
    except Exception as exc:  # noqa: BLE001 - 数据库只是可选依赖
        st.caption(f"数据库 · 未接通（{type(exc).__name__}）")
    if not settings.api_key_ok:
        st.warning("未配置 DEEPSEEK_API_KEY", icon="⚠️")

# 页面路径相对于本文件所在目录（frontend/）
navigation = {
    "总览": [
        st.Page("views/01_首页.py", title="首页", icon="🏠", default=True),
    ],
    "创作": [
        st.Page("views/02_账号定位.py", title="账号定位", icon="🎯"),
        st.Page("views/03_热点监控.py", title="热点监控", icon="🔥"),
        st.Page("views/04_内容复刻.py", title="内容复刻", icon="🧬"),
    ],
    "生产": [
        st.Page("views/05_口播视频.py", title="口播视频", icon="🎙️"),
        st.Page("views/06_视频剪辑.py", title="视频剪辑", icon="✂️"),
    ],
    "复盘": [
        st.Page("views/07_数据复盘.py", title="数据复盘", icon="📊"),
    ],
}


st.navigation(navigation).run()
