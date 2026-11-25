"""show_hot_topic页面"""
import streamlit as st
import json, pandas as pd

from db.session import HotTopicRecord, latest_positioning, save_record


def _track_hint() -> str:
    """赛道提示：优先用「账号定位」页刚跑出来的结论，其次用数据库里最近一条。"""
    raw = st.session_state.get("pos_input", "") or ""
    if not raw:
        record = latest_positioning()
        raw = (record or {}).get("user_input", "") or ""

    picked = []
    for line in raw.splitlines():
        for prefix in ("技能：", "技能:", "兴趣：", "兴趣:"):
            if line.startswith(prefix):
                picked += [part.strip() for part in line.split("：", 1)[-1].split("、") if part.strip()]
    return " / ".join(picked[:3])


def _save_hot(result: dict, platform: str, field: str) -> None:
    """把这次热点监控写进 hot_topic_records（数据库没接通时静默跳过）。"""
    try:
        save_record(HotTopicRecord(
            platform=platform,
            account_field=field,
            raw_count=len(result.get("raw_topics") or []),
            filtered=result.get("filtered", ""),
            suggestions=result.get("suggestions", ""),
        ))
    except Exception as exc:  # noqa: BLE001 - 存储失败不影响看结果
        st.caption(f"（入库失败：{exc}）")


def _add_history(action: str, summary: str):
    from datetime import datetime
    st.session_state.history.append({
        "time": datetime.now().strftime("%H:%M"),
        "action": action,
        "summary": summary[:200],
    })


def show_hot_topic():
    """热点监控"""
    st.title("🔥 热点监控智能体")
    st.markdown("实时追踪平台热点，AI筛选与你的赛道相关的选题机会")


    col1, col2 = st.columns(2)
    with col1:
        platform = st.selectbox("选择平台", ["抖音", "小红书", "全部"])
    with col2:
        hint = _track_hint()
        field = st.text_input("你的赛道/领域",
                              value=hint,
                              placeholder="科技测评 / 职场成长 / 美妆护肤...")
    if hint:
        st.caption("🧭 赛道取自「账号定位」页的结论，可直接改。")


    if st.button("🔍 获取热点选题", type="primary", use_container_width=True):
        if not field:
            st.warning("请输入你的赛道")
            return


        with st.spinner(f"正在抓取{platform}热点并分析..."):
            from workflow.hot_topic import run_hot_topic
            result = run_hot_topic(platform, field)


        # 存入 session_state 防止刷新丢失
        st.session_state["ht_result"] = result
        st.session_state["ht_platform"] = platform
        st.session_state["ht_field"] = field
        _save_hot(result, platform, field)
        _add_history("热点监控", f"{platform} · {field} · 采集 {len(result.get('raw_topics') or [])} 条")


    #   从 session_state 恢复结果
    result = st.session_state.get("ht_result")
    if result:
        raw_topics = result.get("raw_topics", [])


        #   原始采集数据
        if raw_topics:
            with st.expander(f"📡 采集到的原始数据（{len(raw_topics)} 条）", expanded=True):
                rows = [{"排名": t.get("rank",""), "标题": t.get("title",""),
                         "平台": t.get("platform",""), "热度": t.get("heat","")}
                        for t in raw_topics]
                st.dataframe(pd.DataFrame(rows), use_container_width=True)


        #   AI 筛选结果
        st.markdown("### 📋 赛道相关热点筛选")
        st.markdown(result.get("filtered", ""))


        #   选题建议
        st.markdown("### 💡 选题创作建议")
        st.markdown(result.get("suggestions", ""))


        #   下载完整报告
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        report = f"""# 🔥 热点监控报告
> 生成时间：{now}  平台：{platform}  赛道：{field}


## 📡 原始数据（{len(raw_topics)} 条）
{"| 排名 | 标题 | 平台 | 热度 |" if raw_topics else ""}
""" + "\n".join(
    f"| {t.get('rank','')} | {t.get('title','')} | {t.get('platform','')} | {t.get('heat','')} |"
    for t in raw_topics
) + f"""


## 📋 赛道相关热点筛选
{result.get('filtered', '')}


## 💡 选题创作建议
{result.get('suggestions', '')}
"""
        st.download_button("📥 下载完整热点报告", report,
                          file_name=f"热点监控报告_{datetime.now().strftime('%Y%m%d_%H%M')}.md",
                          mime="text/markdown", use_container_width=True)


        _add_history("热点监控", f"{platform} - {field}")


show_hot_topic()