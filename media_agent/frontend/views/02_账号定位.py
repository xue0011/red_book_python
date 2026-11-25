"""show_positioning页面"""
import streamlit as st


def _add_history(action: str, summary: str):
    """记录操作历史"""
    from datetime import datetime
    import streamlit as st
    st.session_state.history.append({
        "time": datetime.now().strftime("%H:%M"),
        "action": action,
        "summary": summary[:200],
    })






def show_positioning():
    """账号定位"""
    st.title("🎯 账号定位智能体")
    st.markdown("输入你的背景信息，AI帮你制定专属的账号定位策划方案")


    col1, col2 = st.columns(2)
    with col1:
        job = st.text_input("你的职业", placeholder="程序员 / 设计师 / 运营 / 学生...")
        skills = st.text_area("核心技能", placeholder="Python开发、视频剪辑、公开演讲...",
                               height=100)
    with col2:
        interests = st.text_area("兴趣领域", placeholder="科技测评、美食探店、知识分享...",
                                  height=100)
        platform = st.selectbox("目标平台", ["抖音", "小红书", "B站", "视频号", "全平台"])


    if st.button("🚀 生成定位方案", type="primary", use_container_width=True):
        if not job and not skills:
            st.warning("请至少填写职业或技能再生成")
            return


        user_input = f"职业：{job}\n技能：{skills}\n兴趣：{interests}\n目标平台：{platform}"


        with st.spinner("AI 正在分析你的画像..."):
            from workflow.positioning import run_positioning
            result = run_positioning(user_input)


        # 存入 session_state 防止下载按钮触发 rerun 后丢失
        st.session_state["pos_result"] = result
        st.session_state["pos_input"] = user_input


    #   从 session_state 恢复结果
    result = st.session_state.get("pos_result")
    if result:
        st.success("✅ 方案生成完成！")


        tab1, tab2, tab3 = st.tabs(["📊 画像分析", "🔍 对标账号", "📝 完整方案"])
        with tab1:
            st.markdown(result.get("profile", "分析中..."))
        with tab2:
            st.markdown(result.get("competitors", "搜索中..."))
        with tab3:
            plan = result.get("plan", "")
            st.markdown(plan)
            st.download_button("📥 下载方案", plan,
                               file_name="账号定位方案.md",
                               mime="text/markdown")


        _add_history("账号定位", st.session_state.get("pos_input", ""))

    # 关键设计：结果存入 session_state，下载按钮触发 Streamlit rerun 时从缓存恢复，页面不会变空白。


show_positioning()