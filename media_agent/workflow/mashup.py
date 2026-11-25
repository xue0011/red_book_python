"""
视频剪辑工作流 — deepagents + video-use skill


deepagent 全权负责：加速 → 转录 → HyperFrames → 排布 → BGM → 渲染
"""
from typing import TypedDict
import os, sys, json, re, subprocess, time


# Windows 控制台强制 UTF-8（防止日志中文变繁体）
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # type: ignore
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')  # type: ignore
    except Exception:
        pass


# subprocess monkey-patch: UTF-8 + Windows 防卡
_orig_init = subprocess.Popen.__init__
def _patched_init(self, *args, **kwargs):
    if kwargs.get('text') or kwargs.get('universal_newlines'):
        kwargs.setdefault('encoding', 'utf-8')
        kwargs.setdefault('errors', 'replace')
    # Windows: CREATE_NO_WINDOW 防止控制台阻塞，bufsize 防止管道阻塞
    if sys.platform == 'win32':
        kwargs.setdefault('creationflags', subprocess.CREATE_NO_WINDOW)
        kwargs.setdefault('bufsize', 102400 * 1024)  # 1MB 缓冲区，防 ffmpeg stderr 撑爆管道
    _orig_init(self, *args, **kwargs)
subprocess.Popen.__init__ = _patched_init


from langgraph.graph import StateGraph, START, END
from config import settings




class MashupState(TypedDict):
    input_video: str
    edit_style: str
    edit_requirements: str
    output_video: str
    editor_log: str
    steps: str
    edit_start: float




# BGM 默认值：优先 .env 的 DEFAULT_BGM_PATH；没配就留空（由页面显式传入）
DEFAULT_BGM = getattr(settings, "default_bgm_path", "") or ""


# 用项目根目录定位 .cache（绝对路径，防止 CWD 不同导致路径错误）
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CACHE_DIR = os.path.join(_PROJECT_ROOT, ".cache", "videos")


#  路径清洗 — 修复 Git Bash / Windows 路径混用产生的畸形路径
def normalize_path(p: str) -> str:
    """修复 Git Bash / Windows 路径混用导致的各种畸形路径。


    常见畸形及修复：
      C:\\c\\Users\\...  →  C:\\Users\\...
      C:/c/Users/...    →  C:\\Users\\...
      /c/Users/...      →  C:\\Users\\...
      /d/project/...    →  D:\\project\\...
      C:\\d\\Data\\...    →  D:\\Data\\... (当 C: 后面的盘符字母与 C: 不同时)
    """
    if not p or not isinstance(p, str):
        return p


    original = p


    # 1) 修复 Windows 盘符 + Git Bash 盘符前缀叠加：
    #    C:\\c\\Users\\... → C:\\Users\\...（盘符字母相同）
    #    C:\\d\\Data\\...  → D:\\Data\\...（盘符字母不同，以第二个为准）
    #    匹配: <盘符1>:\ 或 :/  然后 <盘符2>\ 或 /
    m = re.match(r'^([A-Za-z]):[\\/]([a-z])([\\/].+)$', p)
    if m:
        drive1 = m.group(1).lower()
        drive2 = m.group(2).lower()
        rest = m.group(3)
        if drive1 == drive2:
            # 同一个盘符重复：C:\c\Users → C:\Users
            p = f'{m.group(1)}:{rest}'
        else:
            # 不同盘符：可能是 LLM 把目标盘符误拼成 "当前盘符:\目标盘符\..."
            p = f'{m.group(2).upper()}:{rest}'


    # 2) 修复纯 Git Bash 路径：/c/Users/... → C:\\Users\\...
    if sys.platform == 'win32':
        m2 = re.match(r'^/([a-zA-Z])/(.+)$', p)
        if m2:
            drive = m2.group(1).upper()
            rest = m2.group(2)
            p = f'{drive}:\\{rest}'


    # 3) 统一斜杠方向（Windows）
    if sys.platform == 'win32' and '\\' not in p and '/' in p:
        p = p.replace('/', '\\')
        # 修复 /videos、/videos/output.mp4 → \videos\... 被 Windows 误判为 C:\videos\...
        # 原始输入以 / 开头但不是 /c/ 格式（Git Bash 路径）→ 去掉前导反斜杠，恢复为相对路径
        if original.startswith('/') and not re.match(r'^/[a-zA-Z]/', original):
            p = p.lstrip('\\')


    # 4) 最终规范化
    try:
        p = os.path.normpath(p)
    except Exception:
        pass


    if p != original:
        print(f"[PathFix] 路径已修复: {original!r} → {p!r}")
    return p




def safe_abspath(p: str) -> str:
    """安全的 abspath：先清洗再解析，防畸形路径。"""
    return os.path.abspath(normalize_path(p))




#  deepagent 懒加载
_editor_agent = None


def _get_editor_agent():
    global _editor_agent
    if _editor_agent is not None:
        return _editor_agent


    from deepagents import create_deep_agent
    from deepagents.backends import LocalShellBackend
    from langchain_openai import ChatOpenAI


    model = ChatOpenAI(
        model=settings.get_deepagent_model(),
        api_key=settings.api_key,
        base_url=settings.base_url,
        temperature=0.5,
    )


    skills_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".skills"))


    # skill 必须放在后端沙箱内（LocalShellBackend 的 root_dir=.cache），
    # 所以每次启动把项目 .skills/ 同步一份到 .cache/skills/，传相对路径 "skills"
    _cache_root = os.path.join(_PROJECT_ROOT, ".cache")
    _skills_link = os.path.join(_cache_root, "skills")
    _skill_names = []
    if os.path.isdir(skills_dir):
        try:
            import shutil
            shutil.copytree(skills_dir, _skills_link, dirs_exist_ok=True)
            _skill_names = [
                d for d in os.listdir(_skills_link)
                if os.path.isfile(os.path.join(_skills_link, d, "SKILL.md"))
            ]
        except Exception as exc:
            print(f"[deepagents] skill 同步失败：{type(exc).__name__}: {exc}")


    env = os.environ.copy()
    for p in [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]:
        if os.path.exists(p):
            env["CHROMIUM_PATH"] = p
            env["PUPPETEER_EXECUTABLE_PATH"] = p
            break


    # 禁用 MSYS/Git Bash 路径自动转换
    # 防止 Git Bash 把 /c/Users/... 转成 C:\Users\... 后又与盘符叠加
    env["MSYS_NO_PATHCONV"] = "1"
    env["MSYS2_ARG_CONV_EXCL"] = "*"
    # 强制 Windows 原生路径风格（不生成 /c/Users 格式）
    env["GIT_BASH_PATH_CONV"] = "none"


    # 注入项目关键路径，让 agent 引用而非自己拼
    env["PROJECT_ROOT"] = _PROJECT_ROOT
    env["CACHE_DIR"] = CACHE_DIR  # 绝对路径，供 Python 代码 fallback 使用
    env["WORK_DIR"] = "videos"  # 沙箱内相对路径，供 agent 文件操作使用


    # 项目根下的 .cache 目录
    _cache_dir = os.path.join(_PROJECT_ROOT, ".cache")
    os.makedirs(_cache_dir, exist_ok=True)


    backend = LocalShellBackend(
        root_dir=_cache_dir,
        virtual_mode=True,
        inherit_env=True,
        timeout=300,
        max_output_bytes=5000000,
        env=env,
    )


    # monkey-patch: 清洗 Git Bash 路径 /c/Users/... → C:\Users\...
    # 否则 virtual_mode 会把 /c/Users/... 当成虚拟路径拼到 .cache 下面
    _orig_resolve = backend._resolve_path


    def _patched_resolve(key: str):
        cleaned = normalize_path(key)
        # 如果清洗后是沙箱内的绝对路径，转为相对路径让 virtual_mode 正常处理
        if os.path.isabs(cleaned):
            try:
                rel = os.path.relpath(cleaned, _cache_dir)
                if not rel.startswith(".."):
                    cleaned = rel
            except ValueError:
                pass  # 不同盘符，保持原样让后端正常拒绝
        return _orig_resolve(cleaned)


    backend._resolve_path = _patched_resolve


    _editor_agent = create_deep_agent(
        model=model,
        skills=["skills"] if _skill_names else [],
        backend=backend,
        system_prompt=(
            "你是视频剪辑专家。\n"
            f"你有一个可用 skill：{'、'.join(_skill_names) if _skill_names else '(未安装)'}。"
            "开工前先读 skills/video-use/SKILL.md，按里面的步骤和硬约束做；能复用就复制 "
            "skills/video-use/templates/mashup_template.py 到 videos/ 下改参数，不要从零手写。\n"
            "本项目的硬性要求（与 skill 一致，违反即返工）：\n"
            "  1) 画布尺寸必须是 (w, h + h//3)：口播放 (0,0)，素材带放 (0,h) 高 h//3；"
            "CompositeVideoClip(..., size=(w, h + h//3))，写成 (w,h) 会把底部素材裁掉；\n"
            "  2) 字幕只能用 SubtitlesClip 加载 SRT，严禁 for 循环 + TextClip 拼字幕；"
            "make_textclip 里自己做一张全画布透明图定位到口播区域；\n"
            "  3) 禁止 .with_mask()/.set_mask()/.to_mask()，禁止 resized((w,h)) 这类改宽高比的缩放；\n"
            "  4) BGM 音量 0.1（10%），口播音量 1.0，用 CompositeAudioClip 混音。\n"
            "所有视频处理必须使用 moviepy 库！示例：from moviepy import VideoFileClip, AudioFileClip, CompositeVideoClip, CompositeAudioClip, ImageClip, concatenate_videoclips, vfx, afx\n"
            "禁止写 subprocess.run(['ffmpeg',...]) 或 subprocess.run(['ffprobe',...])！禁止在脚本中直接调用 ffmpeg/ffprobe 子进程命令！\n"
            "moviepy 2.x 的写法（老 API 已经删了，用错必报 AttributeError）：\n"
            "  - 加速: clip.with_effects([vfx.MultiplySpeed(1.3)])   ← 不是 vfx.SpeedX\n"
            "  - 音量: clip.with_effects([afx.MultiplyVolume(0.1)])  ← 不是 afx.VolumeX\n"
            "  - 裁剪: clip.subclipped(start, end)\n"
            "  - 缩放: clip.resized(height=426) 或 clip.resized(width=720)\n"
            "  - 拼接: concatenate_videoclips([clip1, clip2, ...])\n"
            "  - 字幕字体必须给字体文件路径（如 C:/Windows/Fonts/msyhbd.ttc），给 \"Microsoft YaHei\" 会报 pillow cannot open resource\n"
            "  - 输出: clip.write_videofile('output.mp4', codec='libx264', audio_codec='aac')\n"
            "语音转文字（转录）必须使用 funasr，model='iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch'，示例：\n"
            "  from funasr import AutoModel\n"
            "  model = AutoModel(model='iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch')\n"
            "  res = model.generate(input=audio_path, batch_size_s=300)\n"
            "  结果在 res[0]['text'] 中，每段有 timestamp，可直接生成 SRT 字幕。\n"
            "素材保持宽高比禁拉伸，输出分辨率与源视频完全一致。\n"
            "npx 加 --yes，禁 playwright install。\n"
            "HyperFrames 生成 HTML 时，每个文件 `<head>` 内必须加：\n"
            "  `<style>body{font-family:'Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif}</style>`\n"
            "  否则中文全变方框！不写这条 style 的中文内容等于白做。\n"
            "\n"
            "=== ⚠️ 沙箱路径规则（每次文件操作前必须遵守，违反者输出会被丢弃） ===\n"
            "你运行在一个文件沙箱中，当前工作目录就是沙箱根目录。\n"
            "所有文件操作（write、ls、read、execute）都只能访问沙箱内的文件。\n"
            "\n"
            "【输出目录】\n"
            "  os.environ['WORK_DIR'] = 'videos'（沙箱内的相对路径）\n"
            "  所有中间文件和最终输出都放在 videos/ 下。\n"
            "\n"
            "【Python 脚本内】\n"
            "  用 os.environ['WORK_DIR'] 构造路径：\n"
            "    out = os.path.join(os.environ['WORK_DIR'], 'output.mp4')\n"
            "  写文件前创建目录：\n"
            "    os.makedirs(os.path.dirname(out), exist_ok=True)\n"
            "\n"
            "【后端工具（write/ls/read）】\n"
            "  同样用 os.environ['WORK_DIR'] 拼接相对路径。\n"
            "  例如: ls videos/  或  write('videos/script.py', content)\n"
            "\n"
            "【外部文件（源视频、BGM）】\n"
            "  源视频和 BGM 的绝对路径会通过环境变量或任务提示传入。\n"
            "  在 Python 脚本中直接使用这些绝对路径读取即可，不需要复制到沙箱。\n"
            "\n"
            "【严禁】\n"
            "  禁止手写任何含盘符的路径（C:、D: 等）。\n"
            "  禁止使用 /c/Users 这种 Linux 风格绝对路径。\n"
            "  所有输出路径一律用 os.path.join(os.environ['WORK_DIR'], '文件名')。\n"
        ),
    )
    print("[deepagents] 视频剪辑 Agent 已初始化")
    return _editor_agent




#  Node: deepagent 剪辑（全权负责）
def node_edit_video(state: MashupState) -> dict:
    video = safe_abspath(state["input_video"])
    edit_requirements_raw = state.get("edit_requirements", "")
    edit_start = time.time()


    if not os.path.exists(video):
        return {"editor_log": f"文件不存在: {video}", "output_video": ""}


    try:
        req = json.loads(edit_requirements_raw) if edit_requirements_raw else {}
    except Exception:
        req = {}
    materials = req.get("materials", [])
    bgm_path = req.get("bgm_path", DEFAULT_BGM)
    extra_req = req.get("extra_requirements", "")
    open_caption = (req.get("open_caption") or "").strip()
    climax_caption = (req.get("climax_caption") or "").strip()
    cliff_at = req.get("climax_at") or ""


    materials_desc = ""
    if materials:
        materials_desc = "\n\n## 穿插素材\n"
        for i, mp in enumerate(materials):
            mp = normalize_path(mp)
            mt = "视频" if os.path.splitext(mp)[1].lower() in (".mp4", ".mov", ".avi") else "图片"
            materials_desc += f"{i+1}. [{mt}] `{os.path.abspath(mp)}`\n"


    bgm_info = f"\nBGM: `{bgm_path}`" if os.path.exists(bgm_path) else ""


    prompt = f"""## 剪辑任务（口播 + 图片 + BGM → 成片）

### 输入
- 口播视频：`{os.path.abspath(video)}`
{materials_desc}{bgm_info}

### 你要做的
把上面三样东西合成一条成片：上面是口播，下面是图片素材带，口播区域带 SRT 字幕，BGM 压低铺底。
用户补充要求：{extra_req if extra_req else "治愈系混剪，素材带轮播"}

### 用户给定的文案（原样使用，不要改写）
- 开头文案（0.5 秒起，约 3 秒）：{open_caption or "（未指定，可省略）"}
- 高潮文案{("（约第 " + str(cliff_at) + " 秒）") if cliff_at else ""}：{climax_caption or "（未指定，可省略）"}

### 执行方式（必须按这个顺序）
1. 先读 `skills/video-use/SKILL.md`，里面有版式、字幕、蒙版、混音四条硬约束。
2. 把 `skills/video-use/templates/mashup_template.py` 复制到 `videos/mashup_run.py`，改里面的输入路径与文案参数。
3. 如果需要字幕而手上没有 SRT：用 funasr 转写口播拿到 segments，写成标准 SRT 文件，再传给模板的 `--srt`。
4. 用 `python videos/mashup_run.py ...` 跑（解释器用 `{sys.executable}`），出错就看报错改脚本，不要换方案。
5. 产出必须是 `videos/mashup_final.mp4`，最后报告输出绝对路径、分辨率、时长。

### 版本硬约束（写错必返工）
- 画布 `(w, h + h//3)`；口播 (0,0)；素材带 (0,h) 高 h//3；`CompositeVideoClip(..., size=(w, h + h//3))`
- 字幕只用 `SubtitlesClip` 加载 SRT，禁止 for 循环 TextClip
- 禁止 `.with_mask()/.set_mask()/.to_mask()`，禁止 `resized((w,h))`
- BGM 音量 0.1，口播 1.0
- moviepy 2.x：`vfx.MultiplySpeed` / `afx.MultiplyVolume`（没有 SpeedX / VolumeX）
- TextClip 字体给文件路径 `C:/Windows/Fonts/msyhbd.ttc`（给字体名会报 pillow cannot open resource）

不要做 HyperFrames 动画素材，不要额外找素材，用户只给了口播、图片、BGM。"""


    steps_log = []
    tool_count = 0
    print("[Edit] 启动 deepagent（主线程流式）...")


    agent = _get_editor_agent()
    last_chunk = {}


    try:
        for chunk in agent.stream(
            {"messages": [{"role": "user", "content": prompt}]},
            stream_mode="values",
        ):
            last_chunk = chunk
            msgs = chunk.get("messages", [])
            if not msgs:
                continue
            last = msgs[-1]


            # 打印 agent 思考
            if hasattr(last, "content") and last.content and hasattr(last, "type"):
                c = str(last.content).strip()
                if c and c not in steps_log:
                    steps_log.append(c)
                    role = getattr(last, "type", "?")
                    emoji = {"ai": "🤖", "tool": "🔧", "human": "👤"}.get(role, "📝")
                    print(f"  [{emoji}] {c[:300]}")


            # 打印工具调用详情
            if hasattr(last, "tool_calls") and last.tool_calls:
                for tc in last.tool_calls:
                    tool_count += 1
                    name = tc.get("name", "?")
                    args = tc.get("args", {})
                    steps_log.append(f"[tool] {name}")
                    cmd = args.get("command", args.get("cmd", ""))
                    if cmd:
                        print(f"  [{tool_count}] ⚡ {name}: {cmd[:200]}")
                    elif name == "task":
                        print(f"  [{tool_count}] 🚀 task → {str(args.get('description', args.get('subagent_type', '')))[:150]}")
                    elif name in ("write_file", "write"):
                        print(f"  [{tool_count}] 📄 {name}: {args.get('file_path', args.get('path', '?'))}")
                    elif name in ("read_file", "read"):
                        print(f"  [{tool_count}] 📖 {name}: {args.get('file_path', args.get('path', '?'))}")
                    elif name == "write_todos":
                        for td in args.get("todos", [])[:5]:
                            print(f"  [{tool_count}] 📋 todo: {td.get('content', str(td)[:100])}")
                    else:
                        print(f"  [{tool_count}] 🔨 {name}")


            # 打印工具返回
            if hasattr(last, "type") and last.type == "tool":
                result = str(last.content)[:200] if hasattr(last, "content") else ""
                if result:
                    print(f"  [↩] → {result}")


    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[Edit] 异常: {e}")
        return {"editor_log": f"剪辑出错: {e}", "steps": json.dumps(steps_log, ensure_ascii=False), "output_video": ""}


    # 清理根目录散落的中间文件（先清洗路径再处理）
    import glob as _glob
    for _pat in ["*.wav", "*.txt", "concat_list*", "speedup*.mp4", "assembled*.mp4",
                 "final_with_bgm*.mp4"]:
        for _f in _glob.glob(_pat):
            _f = normalize_path(_f)
            if os.path.isfile(_f) and CACHE_DIR not in normalize_path(os.path.abspath(_f)):
                try:
                    _dst = os.path.join(CACHE_DIR, os.path.basename(_f))
                    os.replace(_f, _dst)
                    print(f"  [Clean] {_f} → {_dst}")
                except Exception:
                    pass


    # 提取输出路径（先清洗再验证）
    last_msg = ""
    output_video = ""
    for m in reversed(last_chunk.get("messages", [])):
        c = str(getattr(m, "content", ""))
        if c:
            last_msg = c
            for word in c.replace(',', ' ').replace('\n', ' ').split():
                word = normalize_path(word.strip('`"\'[]()'))
                if word.endswith('.mp4') and os.path.isfile(word):
                    if os.path.getmtime(word) > edit_start - 10:
                        inp_sz = os.path.getsize(state["input_video"])
                        if os.path.getsize(word) != inp_sz:
                            output_video = word
                            break
            if output_video:
                break


    return {
        "editor_log": last_msg[:2000] if last_msg else "deepagent 完成",
        "steps": json.dumps(steps_log, ensure_ascii=False),
        "output_video": output_video,
    }




#  Node: 兜底查找
def node_find_output(state: MashupState) -> dict:
    out = normalize_path(state.get("output_video", ""))
    inp = safe_abspath(state["input_video"])
    if out and os.path.isfile(out) and safe_abspath(out) != inp:
        return {"output_video": safe_abspath(out)}


    # 用绝对路径搜索（CACHE_DIR 已是绝对路径）
    candidates = [
        os.path.join(CACHE_DIR, "mashup_final.mp4"),
        os.path.join(CACHE_DIR, "final_with_bgm.mp4"),
    ]
    for candidate in candidates:
        candidate = normalize_path(candidate)
        if os.path.isfile(candidate) and safe_abspath(candidate) != inp:
            return {"output_video": safe_abspath(candidate)}


    import glob
    for sd in [CACHE_DIR, os.path.join(CACHE_DIR, "edit")]:
        for f in sorted(glob.glob(os.path.join(sd, "*.mp4")),
                        key=os.path.getmtime, reverse=True):
            f = normalize_path(f)
            f_abs = safe_abspath(f)
            if f_abs == inp:
                continue
            try:
                if os.path.getsize(f) < 100 * 1024:
                    continue
                if os.path.getsize(f) == os.path.getsize(state["input_video"]):
                    continue
            except OSError:
                continue
            return {"output_video": f_abs}
    return {}




#  Graph
builder = StateGraph(MashupState)
builder.add_node("edit", node_edit_video)
builder.add_node("find", node_find_output)


builder.add_edge(START, "edit")
builder.add_edge("edit", "find")
builder.add_edge("find", END)


mashup_graph = builder.compile()




def run_mashup(input_video: str, edit_requirements: str = "", **_kwargs) -> dict:
    return mashup_graph.invoke({
        "input_video": input_video,
        "edit_requirements": edit_requirements,
    })