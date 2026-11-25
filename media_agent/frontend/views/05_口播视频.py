"""show_video页面"""
import streamlit as st
import os, json, glob


HEYGEM_STATE_FILE = ".cache/heygem_state.json"


# 从统一配置读取（os.getenv 读不到 .env 文件，必须走 config.settings）
from config import settings
HEYGEM_DATA_DIR = settings.heygem_data_dir


# 允许的媒体格式
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".webm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}




def _list_avatars() -> list[dict]:
    """扫描 HeyGem 数据目录，列出所有数字人模特文件


    Returns:
        [{"path": str, "name": str, "type": "video"|"image", "size_mb": float}, ...]
    """
    avatars = []
    if not os.path.isdir(HEYGEM_DATA_DIR):
        return avatars


    seen = set()
    for f in sorted(os.listdir(HEYGEM_DATA_DIR), key=lambda x: os.path.getmtime(
            os.path.join(HEYGEM_DATA_DIR, x)), reverse=True):
        full = os.path.join(HEYGEM_DATA_DIR, f)
        if not os.path.isfile(full):
            continue
        name, ext = os.path.splitext(f)
        ext = ext.lower()


        # 跳过非媒体文件
        if ext not in VIDEO_EXTS and ext not in IMAGE_EXTS:
            continue
        # 跳过 muted 版本（静音视频是衍生物）
        if name.endswith("_muted"):
            continue
        # 跳过临时/中间文件
        if any(p in full.lower() for p in ["/temp/", "/log/", "/result/",
                                             "voice_", "voiceover_", "voice_cloned",
                                             "test_", "tts_", "long_tts"]):
            continue


        base = name  # 去掉 _muted 后的名字，用于去重
        if base in seen:
            continue
        seen.add(base)


        size_mb = os.path.getsize(full) / (1024 * 1024)
        avatars.append({
            "path": full,
            "name": f,
            "type": "video" if ext in VIDEO_EXTS else "image",
            "size_mb": size_mb,
        })


    return avatars




def _load_selected_avatar() -> str:
    """读取上次选中的模特路径"""
    try:
        if os.path.exists(HEYGEM_STATE_FILE):
            with open(HEYGEM_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            path = data.get("avatar_path", "")
            if path and os.path.exists(path):
                return path
    except Exception:
        pass
    return ""




def _save_selected_avatar(path):
    """保存选中的模特路径"""
    os.makedirs(os.path.dirname(HEYGEM_STATE_FILE), exist_ok=True)
    with open(HEYGEM_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"avatar_path": path}, f)




def _add_history(action: str, summary: str):
    from datetime import datetime
    st.session_state.history.append({
        "time": datetime.now().strftime("%H:%M"),
        "action": action,
        "summary": summary[:200],
    })




def show_video():
    """口播视频 — 提词器 / HeyGem 数字人出镜"""
    st.title("🎥 口播视频智能体")
    st.markdown("输入台词 → 提词器滚动 / 数字人出镜 → 视频下载")


    for key in ["hg_video_path", "hg_audio_path", "hg_task_code", "hg_msg"]:
        if key not in st.session_state:
            st.session_state[key] = ""


    mode = st.radio("📌 生成模式", [
        "📺 提词器模式（只看稿）",
        "🔒 数字人生成（HeyGem → MP4）",
    ], horizontal=True)


    # 从内容复刻页带过来的仿写文案（跨页：04 仿写 → 05 口播）
    rewritten = (st.session_state.get("rep_result") or {}).get("rewritten", "")
    if rewritten:
        st.caption("✍️ 台词已用「内容复刻」页的仿写文案预填，可直接改。")

    raw = st.text_area("✍️ 输入你的文案", height=180,
                       value=rewritten,
                       placeholder="直接输入你要说的台词，原样使用不做改写...")


    # 数字人模式：选择 / 上传模特
    avatar_path = ""
    if "数字人" in mode:
        st.markdown("#### 🎭 选择数字人模特")


        avatars = _list_avatars()
        saved = _load_selected_avatar()


        # 确定当前选中的索引
        selected_idx = 0
        for i, a in enumerate(avatars):
            if a["path"] == saved:
                selected_idx = i
                break


        if avatars:
            #   网格展示所有模特
            cols_per_row = 4
            for row_start in range(0, len(avatars), cols_per_row):
                row_avatars = avatars[row_start:row_start + cols_per_row]
                cols = st.columns(cols_per_row)
                for ci, a in enumerate(row_avatars):
                    idx = row_start + ci
                    with cols[ci]:
                        # 读取并显示缩略图/预览
                        try:
                            with open(a["path"], "rb") as vf:
                                media_bytes = vf.read()
                            if a["type"] == "image":
                                st.image(media_bytes, use_container_width=True)
                            else:
                                st.video(media_bytes)
                        except Exception:
                            st.warning(f"无法加载")


                        # 信息行
                        type_icon = "🎬" if a["type"] == "video" else "🖼️"
                        size_str = f"{a['size_mb']:.1f}MB" if a['size_mb'] < 1000 else f"{a['size_mb']/1024:.1f}GB"
                        selected = (idx == selected_idx)
                        star = "⭐ " if selected else ""
                        st.caption(f"{star}{type_icon} {a['name'][:25]}\n{size_str}")


                        # 选择 + 删除按钮
                        c_sel, c_del = st.columns([3, 1])
                        with c_sel:
                            btn_label = "✅ 已选中" if selected else "🔘 选择此模特"
                            if st.button(btn_label, key=f"sel_avatar_{idx}",
                                         use_container_width=True,
                                         type="primary" if not selected else "secondary",
                                         disabled=selected):
                                _save_selected_avatar(a["path"])
                                st.rerun()
                        with c_del:
                            # 删除确认弹窗
                            if st.button("🗑️", key=f"del_avatar_{idx}",
                                         help="删除此模特"):
                                st.session_state[f"confirm_del_{idx}"] = True


                        # 二次确认
                        if st.session_state.get(f"confirm_del_{idx}"):
                            st.warning(f"确认删除 `{a['name']}`？")
                            cc1, cc2 = st.columns(2)
                            with cc1:
                                if st.button("✅ 确认", key=f"confirm_yes_{idx}",
                                             type="primary"):
                                    try:
                                        os.remove(a["path"])
                                        # 如果删除的是当前选中，清除选择
                                        if selected:
                                            _save_selected_avatar("")
                                        st.success(f"已删除: {a['name']}")
                                        st.session_state.pop(f"confirm_del_{idx}", None)
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"删除失败: {e}")
                            with cc2:
                                if st.button("❌ 取消", key=f"confirm_no_{idx}"):
                                    st.session_state.pop(f"confirm_del_{idx}", None)
                                    st.rerun()


            # 当前选中的设置 avatar_path
            if saved and os.path.exists(saved):
                avatar_path = saved


            st.markdown("---")


        #   上传新模特
        with st.expander("📤 上传新模特", expanded=(len(avatars) == 0)):
            st.caption("上传后会保存到 HeyGem 数据目录，自动出现在上方列表")
            new_file = st.file_uploader(
                "选择照片或视频",
                type=["mp4", "mov", "avi", "png", "jpg", "jpeg"],
                help="上传一张正面照片或 10-30 秒正面说话视频",
                key="new_avatar_upload",
            )
            if new_file:
                os.makedirs(HEYGEM_DATA_DIR, exist_ok=True)
                suffix = os.path.splitext(new_file.name)[1]
                save_path = os.path.join(
                    HEYGEM_DATA_DIR,
                    f"avatar_{hash(new_file.name) % 100000:05d}{suffix}")
                with open(save_path, "wb") as f:
                    f.write(new_file.getvalue())
                _save_selected_avatar(save_path)
                st.success(f"✅ 已上传: {os.path.basename(save_path)}")
                st.rerun()


    # 开始生成
    if st.button("🎬 开始生成", type="primary", use_container_width=True):
        if not raw:
            st.warning("请输入台词")
            return


        mode_key = "heygem" if "数字人" in mode else "teleprompter"


        with st.spinner("处理中..."):
            from workflow.video import run_video
            result = run_video(raw, mode=mode_key, avatar_path=avatar_path)


            st.session_state["hg_script"] = raw
            st.session_state["hg_audio_path"] = result.get("audio_path", "")
            st.session_state["hg_video_path"] = result.get("video_path", "")
            st.session_state["hg_task_code"] = result.get("task_code", "")
            st.session_state["hg_msg"] = result.get("heygem_msg", "")
            st.session_state["hg_mode"] = mode_key
            st.session_state["hg_has_result"] = True
            st.rerun()


    # 渲染结果
    if st.session_state.get("hg_has_result"):
        mode_key = st.session_state.get("hg_mode", "teleprompter")
        script = st.session_state.get("hg_script", "")


        if mode_key == "teleprompter":
            # === 提词器：大字 + 每次 3 行 + JS 逐行推进 ===
            st.markdown("### 📺 提词器")


            speed = st.slider("⏱ 换行速度（秒/行）", min_value=1, max_value=15, value=5,
                             help="数值越小换行越快")


            lines = [l.strip() for l in script.split("\n") if l.strip()]
            if not lines:
                lines = [script]
            total = len(lines)


            import streamlit.components.v1 as components
            html = f"""<!DOCTYPE html>
            <html><head><meta charset="utf-8"><style>
            * {{ margin:0; padding:0; }}
            body {{ background:#000; font-family:'Microsoft YaHei','PingFang SC',sans-serif; }}
            .tp-line {{
                height: 106px; display: flex; align-items: center; justify-content: center;
                text-align: center; color: #fff; font-size: 50px; font-weight: bold;
                letter-spacing: 3px; padding: 0 12px;
            }}
            .tp-box {{ padding: 5px 0; height: 318px; overflow: hidden; }}
            </style></head><body><div class="tp-box">
            <div id="l1" class="tp-line"></div>
            <div id="l2" class="tp-line"></div>
            <div id="l3" class="tp-line"></div>
            </div><script>
            var lines = {json.dumps(lines)};
            var total = lines.length;
            var pos = 0;
            function show() {{
                document.getElementById('l1').textContent = lines[pos] || '';
                document.getElementById('l2').textContent = lines[pos+1] || '';
                document.getElementById('l3').textContent = lines[pos+2] || '';
                pos++;
                if (pos < total - 2) setTimeout(show, {speed * 1000});
            }}
            show();
            </script></body></html>"""
            components.html(html, height=330, scrolling=False)
            st.caption(f"💡 共 {total} 行，每次 3 行大字，{speed} 秒换一行 | 到底自动停止")


        else:
            # 数字人模式
            st.markdown("### 📝 台词（原样使用，未改写）")
            st.text_area("台词内容", script, height=150, key="script_display")
            st.download_button("📥 下载台词", script, file_name="台词.txt")


            st.markdown("### 🎥 视频")
            video_path = st.session_state.get("hg_video_path", "")
            audio_path = st.session_state.get("hg_audio_path", "")
            task_code = st.session_state.get("hg_task_code", "")


            if audio_path and os.path.exists(audio_path):
                st.audio(audio_path)
            if video_path and os.path.exists(video_path):
                st.video(video_path)
            elif task_code:
                st.info(st.session_state.get("hg_msg", "任务已提交，约 2-3 分钟完成"))
                st.caption(f"任务编号: `{task_code}`")
                if st.button("🔄 刷新进度（生成完毕后点击查看）"):
                    from tools.heygem_client import heygem_query_task
                    q = heygem_query_task(task_code)
                    if q["finished"] and q["video_path"]:
                        st.session_state["hg_video_path"] = q["video_path"]
                    st.rerun()


        # 重置
        if st.button("🗑️ 清除重新开始"):
            for k in ["hg_has_result", "hg_video_path", "hg_audio_path",
                      "hg_task_code", "hg_msg", "hg_script", "hg_mode"]:
                st.session_state.pop(k, None)
            st.rerun()


        _add_history("口播视频", script[:60])


show_video()