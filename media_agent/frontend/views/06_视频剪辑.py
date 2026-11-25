"""视频剪辑页面 — 本地确定性渲染（moviepy）+ 可选 deepagents 智能体

默认走 workflow.mashup_local.render_mashup：固定工序、可复现、不需要模型。
勾选"交给 deepagent"才走 workflow.mashup 的智能体编排（需要另装 deepagents）。
"""
import json
import os
import time

import streamlit as st

from config import settings

CACHE_DIR = ".cache/mashup"
MATERIALS_DIR = os.path.join(CACHE_DIR, "materials")
BGM_CACHE = os.path.join(CACHE_DIR, "bgm.json")

# 默认 BGM 由 config.py / .env 的 DEFAULT_BGM_PATH 指定
DEFAULT_BGM = settings.default_bgm_path or ""

VIDEO_EXT = (".mp4", ".mov", ".avi", ".mkv", ".webm")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")

OPEN_CAPTION = "不知何时起，我开始迷恋上了天空"
CLIMAX_CAPTION = "我们永远碰不到星星，或我们已成为了星星"


# ── 小工具 ────────────────────────────────────────────────────────────
def _abs(p: str) -> str:
    """把相对路径按项目根解析成绝对路径（页面 CWD 不一定是项目根）。"""
    if not p:
        return ""
    if os.path.isabs(p):
        return p
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.abspath(os.path.join(root, p))


def _load_bgm_path() -> str:
    try:
        if os.path.exists(BGM_CACHE):
            with open(BGM_CACHE, "r", encoding="utf-8") as f:
                return _abs(json.load(f).get("bgm_path", DEFAULT_BGM))
    except Exception:
        pass
    return _abs(DEFAULT_BGM)


def _save_bgm_path(path: str) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(BGM_CACHE, "w", encoding="utf-8") as f:
        json.dump({"bgm_path": _abs(path)}, f, ensure_ascii=False)


def _add_history(action: str, summary: str) -> None:
    from datetime import datetime

    history = st.session_state.setdefault("history", [])
    history.append({
        "time": datetime.now().strftime("%H:%M"),
        "action": action,
        "summary": summary[:200],
    })


def _list_materials() -> list[str]:
    if not os.path.isdir(MATERIALS_DIR):
        return []
    files = [
        f for f in os.listdir(MATERIALS_DIR)
        if os.path.splitext(f)[1].lower() in IMAGE_EXT + VIDEO_EXT
    ]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(MATERIALS_DIR, f)), reverse=True)
    return [os.path.join(MATERIALS_DIR, f) for f in files]


def _save_uploads(files) -> list[str]:
    """把上传的文件落盘；同名同大小直接复用，**不删除已有素材**。"""
    os.makedirs(MATERIALS_DIR, exist_ok=True)
    saved = []
    for mf in files or []:
        dst = os.path.join(MATERIALS_DIR, mf.name)
        try:
            if not (os.path.exists(dst) and os.path.getsize(dst) == len(mf.getvalue())):
                with open(dst, "wb") as f:
                    f.write(mf.getvalue())
            saved.append(dst)
        except Exception as exc:  # noqa: BLE001
            st.warning(f"素材写入失败：{mf.name}（{exc}）")
    return saved


def _resolve_video(uploaded, link: str, path_text: str) -> tuple[str, str]:
    """按 上传 > 链接 > 路径 的优先级得到本地视频路径，返回 (路径, 来源说明)。"""
    if uploaded is not None:
        os.makedirs(CACHE_DIR, exist_ok=True)
        suffix = os.path.splitext(uploaded.name)[1] or ".mp4"
        dst = os.path.join(CACHE_DIR, f"input_{abs(hash(uploaded.name)) % 100000:05d}{suffix}")
        need = not (os.path.exists(dst) and os.path.getsize(dst) == len(uploaded.getvalue()))
        if need:
            with open(dst, "wb") as f:
                f.write(uploaded.getvalue())
        return dst, f"上传：{uploaded.name}"

    if link.strip():
        # 用户往往直接粘整段分享文案（"【标题】 https://..."），先抠出真 URL
        from tools.media_tools import _ytdlp_download, extract_url

        clean = extract_url(link.strip())
        if not clean.lower().startswith(("http://", "https://")):
            st.error("没从这段文字里认出链接，请确认里面包含 https://… 开头的地址。")
            return "", ""

        cache_dir = os.path.join(".cache", "videos", "downloads")
        os.makedirs(cache_dir, exist_ok=True)
        # 同一个链接在会话里只下载一次（页面每次交互都会 rerun）
        memo = st.session_state.setdefault("mashup_dl", {})
        hit = memo.get(clean)
        if hit and os.path.exists(hit):
            return hit, f"下载（缓存）：{os.path.basename(hit)}"

        with st.spinner("正在下载口播视频（yt-dlp）…"):
            try:
                downloaded = _ytdlp_download(clean, cache_dir)
            except Exception as exc:  # noqa: BLE001 - 下载器内部已兜底，这里再保一层
                st.error(f"下载异常：{exc}")
                return "", ""
        if downloaded and os.path.exists(downloaded):
            p = _abs(downloaded)
            memo[clean] = p
            return p, f"下载：{os.path.basename(p)}"
        st.error("视频下载失败：链接可能失效，或需要登录（B站大会员/私密视频）。可以改为上传文件。")
        return "", ""

    p = _abs(path_text.strip())
    if p and os.path.exists(p):
        return p, f"本地路径：{os.path.basename(p)}"
    return "", ""


# ── 页面 ──────────────────────────────────────────────────────────────
def show_mashup():
    st.title("🎬 视频剪辑")
    st.caption(
        "口播视频 + 素材图 → 素材卡穿插（上下排布、光晕过渡）→ 5:20 手机屏特效 → "
        "BGM 铺底 → 一条成片。整条链路是固定工序，不经过大模型，几秒到几分钟出片。"
    )

    os.makedirs(MATERIALS_DIR, exist_ok=True)

    # 1) 口播视频
    st.markdown("### 📁 口播视频")
    tab_upload, tab_link, tab_path = st.tabs(["上传文件", "粘贴链接", "本机路径"])
    with tab_upload:
        video_file = st.file_uploader(
            "上传口播视频", type=["mp4", "mov", "avi", "mkv", "webm"],
            label_visibility="collapsed",
        )
    with tab_link:
        video_link = st.text_input(
            "视频链接（B站 / 抖音 / YouTube）",
            placeholder="https://www.bilibili.com/video/BV...",
            label_visibility="collapsed",
        )
        st.caption("下载由 yt-dlp 完成，首次会慢一点；会员/私密视频可能拿不到。")
    with tab_path:
        video_path_text = st.text_input(
            "本机视频路径", placeholder=r"例: E:\videos\口播.mp4",
            label_visibility="collapsed",
        )

    video_path, video_from = _resolve_video(video_file, video_link, video_path_text)
    if video_path:
        st.success(f"✅ 口播：{video_from}")
        st.video(video_path)
    else:
        st.info("还没有口播视频：上传一个，或贴一条链接。")

    # 2) 素材
    st.markdown("### 🖼️ 穿插素材")
    st.caption("图片会做成圆角柔光卡，上下交替出现；视频素材此版本暂不参与（只取封面不做）。")
    material_files = st.file_uploader(
        "上传素材（可多选）",
        type=["png", "jpg", "jpeg", "gif", "webp", "mp4", "mov"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )
    _save_uploads(material_files)

    materials = _list_materials()
    images = [m for m in materials if os.path.splitext(m)[1].lower() in IMAGE_EXT]
    if images:
        cols = st.columns(min(4, len(images)))
        for i, p in enumerate(images[:8]):
            with cols[i % len(cols)]:
                st.image(p, caption=os.path.basename(p), use_container_width=True)
        st.caption(f"📂 已就绪 {len(images)} 张图片素材（旧的会自动保留，不会被覆盖清空）")
    else:
        st.warning("还没有图片素材，成片里不会有穿插画面。")

    # 3) 背景音乐
    st.markdown("### 🎵 背景音乐")
    current_bgm = _load_bgm_path()
    col_bgm1, col_bgm2 = st.columns([3, 1])
    with col_bgm1:
        bgm_path_input = st.text_input(
            "BGM 文件路径", value=current_bgm,
            placeholder=r"例: D:\music\5.20AM.mp3", key="bgm_path",
        )
    with col_bgm2:
        bgm_file = st.file_uploader("📁 上传", type=["wav", "mp3", "m4a", "ogg"])
        if bgm_file:
            dst = os.path.join(CACHE_DIR, bgm_file.name)
            with open(dst, "wb") as f:
                f.write(bgm_file.getvalue())
            _save_bgm_path(dst)
            st.success("已更新 BGM")
            st.rerun()

    if bgm_path_input and bgm_path_input != current_bgm:
        _save_bgm_path(bgm_path_input)
    bgm_path = _abs(bgm_path_input) or current_bgm
    if bgm_path and os.path.exists(bgm_path):
        st.caption(f"✅ BGM：`{os.path.basename(bgm_path)}`")
    elif bgm_path:
        st.warning(f"⚠️ BGM 文件不存在：`{bgm_path}`")

    # 4) 文案与节奏
    st.markdown("### ✏️ 文案与节奏")
    c1, c2 = st.columns(2)
    with c1:
        open_caption = st.text_input("开场文案", value=OPEN_CAPTION)
    with c2:
        climax_caption = st.text_input("高潮文案", value=CLIMAX_CAPTION)
    c3, c4, c5 = st.columns(3)
    with c3:
        climax_auto = st.number_input(
            "高潮时间点（秒，0=自动取 68%）", min_value=0.0, max_value=3600.0,
            value=0.0, step=1.0,
        )
    with c4:
        speed = st.slider("整体倍速", 0.8, 1.5, 1.0, 0.05)
    with c5:
        card_sec = st.slider("单张素材停留（秒）", 2.0, 8.0, 3.6, 0.2)

    with st.expander("🎛️ 剪辑方式与额外要求", expanded=False):
        mode = st.radio(
            "剪辑方式",
            ["AI 智能体（读 skill 自己写脚本）", "本地模板（快、可复现）"],
            index=0,
            horizontal=True,
            help="智能体会先读 skills/video-use/SKILL.md，再基于模板改参数；本地模板不调模型，几十秒出片。",
        )
        use_agent = mode.startswith("AI")
        extra_requirements = st.text_area(
            "额外要求（给智能体看的自然语言）",
            placeholder="例：00:10-00:20 剪掉；素材带只放前 3 张；高潮文案放在 12 秒",
            height=70,
        )

    # 5) 开始
    if st.button("🎬 开始剪辑", type="primary", use_container_width=True):
        if not video_path:
            st.error("请先提供口播视频：上传文件 / 贴链接 / 填本机路径，三者其一。")
            return
        if not os.path.exists(video_path):
            st.error(f"视频文件不存在：{video_path}")
            return

        started = time.time()
        with st.status("开始剪辑…", expanded=True) as status:
            st.write(f"口播：`{os.path.basename(video_path)}`")

            if use_agent:
                from workflow.mashup import mashup_graph

                st.write("模式：deepagent 智能体")
                result = mashup_graph.invoke({
                    "input_video": video_path,
                    "edit_style": "",
                    "edit_requirements": json.dumps({
                        "materials": images,
                        "bgm_path": bgm_path,
                        "open_caption": open_caption,
                        "climax_caption": climax_caption,
                        "climax_at": climax_auto,
                        "extra_requirements": extra_requirements,
                    }, ensure_ascii=False),
                })
                output = result.get("output_video", "")
                log = result.get("editor_log", "")
                ok = bool(output and os.path.exists(output))
            else:
                from workflow.mashup_local import render_mashup

                st.write("模式：本地确定性渲染（不调模型）")
                prog = st.progress(0.0)

                def on_step(msg: str) -> None:
                    st.write(msg)

                result = render_mashup(
                    video_path, images, bgm_path,
                    open_caption=open_caption,
                    climax_caption=climax_caption,
                    climax_at=climax_auto or None,
                    speed=float(speed),
                    card_duration=float(card_sec),
                    progress=on_step,
                )
                prog.progress(1.0)
                output = result.get("output", "")
                log = result.get("log", "")
                ok = bool(result.get("ok"))

            if ok:
                status.update(label=f"✅ 剪辑完成（{time.time() - started:.1f}s）", state="complete")
            else:
                status.update(label="❌ 剪辑失败", state="error")
                st.error(result.get("error", "未知错误"))

        if log:
            with st.expander("📝 剪辑日志", expanded=not ok):
                st.code(log[-4000:], language="text")

        if ok and os.path.exists(output):
            st.video(output)
            size_mb = os.path.getsize(output) / 1024 / 1024
            st.caption(f"输出：`{output}` · {size_mb:.1f} MB")
            with open(output, "rb") as f:
                st.download_button(
                    "⬇️ 下载成片", f.read(),
                    file_name=os.path.basename(output), mime="video/mp4",
                )
            _add_history("视频剪辑", f"{os.path.basename(output)} · {size_mb:.1f} MB")


show_mashup()
