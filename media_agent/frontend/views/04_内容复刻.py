"""show_replicate页面"""
from pathlib import Path

import streamlit as st

from config import settings


def _add_history(action: str, summary: str):
    from datetime import datetime
    st.session_state.history.append({
        "time": datetime.now().strftime("%H:%M"),
        "action": action,
        "summary": summary[:200],
    })


def _asr_badge() -> None:
    """转写后端状态：默认只显示配置，点「检测」才真的去连（避免每次刷新都建隧道）。"""
    if not settings.asr_ready:
        st.caption("🗣️ 转写后端：本机 FunASR（ASR_MODE=local）")
        return

    try:
        from tools.autodl_funasr import asr_mode
        mode = asr_mode()
    except Exception:  # noqa: BLE001
        mode = (settings.ASR_MODE or "http").strip().lower()

    if mode == "http":
        label = (
            f"🗣️ 转写后端：**AutoDL 转写服务（HTTP）** · {settings.ASR_HTTP_URL} · "
            "贴链接即可，服务器自己下载 + 识别"
        )
    elif mode == "ws":
        label = f"🗣️ 转写后端：**AutoDL 远程 FunASR（WS）** · {settings.FUNASR_WS_URL}"
    else:
        label = (
            f"🗣️ 转写后端：**AutoDL（remote）** · 链接走 {settings.ASR_HTTP_URL}，"
            "失败回落 SFTP + WS"
        )
    left, right = st.columns([5, 1])
    with left:
        st.caption(label)
    with right:
        clicked = st.button("检测", key="asr_probe_btn", help="测试 AutoDL 上的转写服务")

    if clicked:
        with st.spinner("连接 AutoDL 上的转写服务…"):
            try:
                from tools.autodl_funasr import probe
                info = probe()
            except Exception as exc:  # noqa: BLE001
                info = {"ws_ok": False, "error": str(exc)}
        st.session_state["asr_probe"] = info

    info = st.session_state.get("asr_probe")
    if not info:
        return
    if info.get("ws_ok"):
        st.success(
            f"服务正常 · {info.get('protocol') or '?'} · 模型 {info.get('model') or '?'} · "
            f"device={info.get('device') or '?'}"
        )
    else:
        st.warning(f"连不上 AutoDL 转写服务，会自动退回本机 FunASR：{info.get('error')}")


def show_replicate():
    """内容复刻"""
    st.title("📝 内容复刻智能体")
    st.markdown("粘贴爆款视频/文章链接 → 提取文案 → 拆解爆款公式 → 仿写新文案")
    _asr_badge()

    url = st.text_input("📎 视频链接",
                        placeholder="粘贴抖音/B站链接...")

    uploaded = st.file_uploader(
        "或直接上传本地视频/音频（会上传到 AutoDL 转写）",
        type=["mp4", "mov", "mkv", "webm", "mp3", "wav", "m4a"],
        help="链接拿不到时用这个：文件先存到本地 .cache/videos/uploads，再 SFTP 传到服务器 /root/autodl-tmp/asr_in",
    )
    source = "url" if url else ("file" if uploaded else "")
    st.caption(f"当前输入：{'🔗 链接' if source == 'url' else ('📁 本地文件' if source == 'file' else '（未选择）')}")

    col_asr, col_all = st.columns([1, 1])
    transcribe_clicked = col_asr.button(
        "🗣️ 只转写（存本地）", use_container_width=True,
        help="把链接交给服务器 FunASR，只出文本不调模型；结果存 .cache/replicates/ 并显示在这里",
    )
    analyze_clicked = col_all.button("🔄 分析 & 仿写", type="primary", use_container_width=True)

    if transcribe_clicked:
        if not url:
            st.warning("先粘贴一个视频链接（只转写不支持本地上传，本地上传请用右边的「分析 & 仿写」）")
            return
        with st.spinner("提交给服务器转写中（服务器自己下载 + 识别，通常 3–15 秒）..."):
            from workflow.replicate import transcribe_only
            out = transcribe_only(url)
        st.session_state["rep_transcript"] = out
        if out["ok"]:
            _add_history("内容复刻·转写", (out["media"].get("title") or url)[:80])
            from db.session import save_replicate

            st.session_state["asr_record_id"] = save_replicate(
                url=url,
                transcript=out["text"],
            )

    # ========== 只转写的结果 ==========
    transcript = st.session_state.get("rep_transcript")
    if transcript:
        if not transcript.get("ok"):
            st.error(f"转写失败：{transcript.get('error')}")
        else:
            media = transcript["media"]
            st.success(
                f"已转写并保存 · {media.get('title') or ''} · "
                f"{float(media.get('duration') or 0):.1f}s → {len(transcript['text'])} 字"
            )
            c1, c2 = st.columns([3, 2])
            c1.caption(f"👤 {media.get('author') or '?'} · {media.get('platform') or '?'} · {media.get('transport')}")
            c2.caption(f"💾 {transcript['saved_md']}")
            if st.session_state.get("asr_record_id"):
                st.caption(f"🗄️ 已入库 · replicate_records #{st.session_state['asr_record_id']}")
            st.text_area("转写文本", transcript["text"], height=260, key="asr_text_only")
            segments = media.get("segments") or []
            if segments:
                with st.expander(f"🕒 时间轴（{len(segments)} 段）", expanded=False):
                    st.dataframe(
                        [
                            {
                                "开始(s)": round(float(s.get("start") or 0), 2),
                                "结束(s)": round(float(s.get("end") or 0), 2),
                                "文本": s.get("text", ""),
                            }
                            for s in segments
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )
            d1, d2 = st.columns(2)
            with open(transcript["saved_md"], encoding="utf-8") as handle:
                d1.download_button("📥 下载 Markdown", handle.read(),
                                   file_name=Path(transcript["saved_md"]).name,
                                   mime="text/markdown", use_container_width=True)
            with open(transcript["saved_json"], encoding="utf-8") as handle:
                d2.download_button("📥 下载 JSON（含时间轴）", handle.read(),
                                   file_name=Path(transcript["saved_json"]).name,
                                   mime="application/json", use_container_width=True)
            st.markdown("---")

    if analyze_clicked:
        if not source:
            st.warning("请粘贴链接，或上传一个本地视频/音频文件")
            return

        if source == "url":
            with st.spinner("服务器转写（约 10 秒）+ 拆解仿写中..."):
                from workflow.replicate import run_replicate
                result = run_replicate(url)
            st.session_state["rep_url"] = url
        else:
            import time

            from workflow.replicate import run_replicate_local

            name = f"{int(time.time())}_{uploaded.name}"
            local_path = Path(settings.video_output_dir) / "uploads" / name
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(uploaded.getbuffer())
            with st.spinner(f"上传 {uploaded.name} 到 AutoDL 并转写、分析仿写中..."):
                result = run_replicate_local(str(local_path))
            st.session_state["rep_url"] = f"本地文件：{uploaded.name}"

        # 存入 session_state 防止下载按钮触发 rerun 后丢失
        st.session_state["rep_result"] = result

        # 入库：一条视频一行（链接 / 本地视频 / 译文 / 拆解 / 仿写 / 标题）
        from db.session import save_replicate

        st.session_state["rep_record_id"] = save_replicate(
            url=url if source == "url" else "",
            local_video=str(local_path) if source == "file" else "",
            transcript=result.get("original_text", ""),
            breakdown=result.get("viral_analysis", ""),
            rewritten=result.get("rewritten", ""),
            titles=result.get("titles", ""),
        )


    # ========== 从 session_state 恢复结果 ==========
    result = st.session_state.get("rep_result")
    if result:
        original = result.get("original_text", "")
        analysis = result.get("viral_analysis", "")
        rewritten = result.get("rewritten", "")
        titles = result.get("titles", "")
        media = result.get("media") or {}

        record_id = st.session_state.get("rep_record_id")
        if record_id:
            st.caption(f"💾 已入库 · replicate_records #{record_id}")

        tabs = st.tabs(["📄 原文案", "🔍 爆款拆解", "✍️ 仿写文案", "🏷️ 标题"])
        with tabs[0]:
            if media:
                c1, c2, c3 = st.columns([3, 2, 2])
                c1.markdown(f"**{media.get('title') or '（无标题）'}**")
                c2.caption(f"👤 {media.get('author') or '未知'} · {media.get('platform') or ''}")
                duration = media.get("duration") or 0
                c3.caption(
                    f"⏱️ {duration:.1f}s · {media.get('transport') or ''} · "
                    f"{media.get('elapsed') or 0}s · {media.get('model') or ''}"
                )
                segments = media.get("segments") or []
                if segments:
                    with st.expander(f"🕒 时间轴（{len(segments)} 段）", expanded=False):
                        st.dataframe(
                            [
                                {
                                    "开始(s)": round(float(s.get("start") or 0), 2),
                                    "结束(s)": round(float(s.get("end") or 0), 2),
                                    "文本": s.get("text", ""),
                                }
                                for s in segments
                            ],
                            use_container_width=True,
                            hide_index=True,
                        )
            else:
                st.caption("未取得视频元信息（可能是本机 FunASR 兜底或手工上传）")
            st.text_area("原文", original, height=200, key="orig_text")
        with tabs[1]:
            st.markdown(analysis)
        with tabs[2]:
            st.text_area("仿写结果", rewritten, height=200, key="new_text")
            st.download_button("📥 下载仿写文案", rewritten,
                               file_name="仿写文案.txt")
        with tabs[3]:
            st.markdown(titles)


        # === 完整报告下载 ===
        st.markdown("---")
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M")


        report = f"""# 📝 内容复刻报告


> 生成时间：{now}
> 来源链接：{st.session_state.get("rep_url", "")}


---


## 📄 原文案


{original}


---


## 🔍 爆款拆解


{analysis}


---


## ✍️ 仿写文案


{rewritten}


---


## 🏷️ 标题方案


{titles}
"""
        st.download_button("📥 下载完整复刻报告", report,
                          file_name=f"内容复刻报告_{datetime.now().strftime('%Y%m%d_%H%M')}.md",
                          mime="text/markdown", use_container_width=True)


        _add_history("内容复刻", st.session_state.get("rep_url", "")[:60])

    _recent_records()


def _recent_records() -> None:
    """最近入库的内容复刻记录（ID / 链接 / 本地视频 / 译文 / 拆解 / 仿写 / 标题）。"""
    try:
        from db.session import _rows

        rows = _rows(
            "SELECT id, created_at, url, local_video, length(transcript) AS transcript_len,"
            " length(breakdown) AS breakdown_len, length(rewritten) AS rewritten_len,"
            " length(titles) AS titles_len FROM replicate_records ORDER BY id DESC",
            limit=5,
        )
    except Exception:  # noqa: BLE001 - 看历史失败不该影响主流程
        return
    if not rows:
        return
    with st.expander(f"🗄️ 最近入库（replicate_records，共 {len(rows)} 条）", expanded=False):
        st.dataframe(
            [
                {
                    "ID": r["id"],
                    "时间": str(r["created_at"])[:19],
                    "网页链接": r["url"] or "",
                    "本地视频": r["local_video"] or "",
                    "译文": f"{r['transcript_len']} 字",
                    "拆解": f"{r['breakdown_len']} 字",
                    "仿写": f"{r['rewritten_len']} 字",
                    "标题": f"{r['titles_len']} 字",
                }
                for r in rows
            ],
            use_container_width=True,
            hide_index=True,
        )


show_replicate()