"""内容复刻工作流 — 提取 → 分析 → 仿写 → 标题"""
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from workflow.llm import llm_call
from tools.media_tools import download_video, extract_audio_text




class ReplicateState(TypedDict):
    source_url: str
    original_text: str
    viral_analysis: str
    rewritten: str
    titles: str
    media: dict            # 转写服务返回的元信息（标题/作者/时长/平台/时间轴）




#   节点
def node_extract(state: ReplicateState) -> dict:
    """提取视频文案：链接直接交给 AutoDL 转写服务（服务端自己下载），失败才回退本机下载。"""
    url = state["source_url"]
    text = ""
    media: dict = {}

    try:
        from tools.autodl_funasr import transcribe

        result = transcribe(url)          # ASR_MODE=http 时走 /api/transcribe_link
        if result.ok and result.text:
            print(f"[复刻/转写] {result.transport} · {result.brief}")
            text = result.text
            media = {
                "title": result.media.get("title", ""),
                "author": result.media.get("author", ""),
                "duration": result.media.get("duration", 0),
                "platform": result.media.get("platform", ""),
                "thumbnail": result.media.get("thumbnail", ""),
                "model": result.media.get("model", ""),
                "transport": result.transport,
                "elapsed": round(result.elapsed, 1),
                "segments": result.sentences,
            }
    except Exception as exc:  # noqa: BLE001 - 远程不可用不影响本机兜底
        print(f"[复刻/转写] 远程转写异常：{exc}")

    if not text:
        path = download_video(url)
        text = extract_audio_text(path)
    return {"original_text": text, "media": media}




def node_analyze(state: ReplicateState) -> dict:
    """分析爆款要素"""
    prompt = f"""拆解以下爆款文案的结构：


{state["original_text"]}


从以下维度分析：
1. **钩子类型**（前3句用了什么技巧：悬念/反差/痛点/利益）
2. **结构模板**（可复用的文案框架）
3. **情绪节奏**（每段的情绪高低点）
4. **金句亮点**（最容易引发共鸣的句子）
5. **互动引导**（点赞/评论/关注的引导方式）
"""
    return {"viral_analysis": llm_call(prompt, temperature=0.6)}




def node_rewrite(state: ReplicateState) -> dict:
    """仿写新文案"""
    prompt = f"""根据以下爆款分析，仿写一篇新文案：


【爆款要素分析】
{state["viral_analysis"]}


【参考原文】
{state["original_text"]}


要求：
- 保持相同结构模板和节奏
- 换成新主题（同类不同题）
- 保留钩子、转折、金句、互动引导
- 字数控制在原文的80%-120%
"""
    return {"rewritten": llm_call(prompt, temperature=0.8)}




def node_titles(state: ReplicateState) -> dict:
    """生成标题"""
    prompt = f"""为以下文案生成5个爆款标题：


{state["rewritten"]}


5种类型各一个：
1. 数字型（"3个方法..."）
2. 疑问型（"为什么..."）
3. 痛点型（"别再..."）
4. 悬念型（"原来..."）
5. 命令型（"一定要..."）
每个标题不超过25字。
"""
    return {"titles": llm_call(prompt, temperature=0.8)}




#   构建图
builder = StateGraph(ReplicateState)
builder.add_node("extract", node_extract)
builder.add_node("analyze", node_analyze)
builder.add_node("rewrite", node_rewrite)
builder.add_node("titles", node_titles)


builder.add_edge(START, "extract")
builder.add_edge("extract", "analyze")
builder.add_edge("analyze", "rewrite")
builder.add_edge("rewrite", "titles")
builder.add_edge("titles", END)


replicate_graph = builder.compile()




def run_replicate(url: str) -> dict:
    """运行内容复刻工作流"""
    return replicate_graph.invoke({"source_url": url})


def run_replicate_from_text(text: str) -> dict:
    """已有文案时，跳过下载/转写，直接分析 → 仿写 → 标题"""
    state: dict = {"source_url": "", "original_text": text}
    state.update(node_analyze(state))
    state.update(node_rewrite(state))
    state.update(node_titles(state))
    return state


def run_replicate_local(path: str) -> dict:
    """本地视频/音频：上传到 AutoDL 转写 → 分析 → 仿写 → 标题"""
    text = extract_audio_text(path)
    if not text or text.startswith("转写失败"):
        raise RuntimeError(text or "没有识别出文字")
    return run_replicate_from_text(text)


# ---------------------------------------------------------------- 转写落盘

def _replicate_dir():
    """转写结果本地目录：.cache/replicates/（已 gitignore）。"""
    from config import settings
    from pathlib import Path

    folder = Path(settings.cache_dir) / "replicates"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def transcribe_only(url: str) -> dict:
    """只做"链接 → 服务器 FunASR → 文本"，并把结果写到本地，供页面直接展示。

    Returns:
        {"ok", "text", "media", "saved_md", "saved_json", "error"}
    """
    import json
    import re
    import time

    from tools.autodl_funasr import transcribe

    result = transcribe(url)
    if not (result.ok and result.text):
        return {"ok": False, "text": "", "media": {}, "error": result.error or "转写为空"}

    media = {
        "title": result.media.get("title", ""),
        "author": result.media.get("author", ""),
        "duration": result.media.get("duration", 0),
        "platform": result.media.get("platform", ""),
        "thumbnail": result.media.get("thumbnail", ""),
        "url": url,
        "model": result.media.get("model", ""),
        "transport": result.transport,
        "elapsed": round(result.elapsed, 1),
        "segments": result.sentences,
        "inference_ms": result.inference_ms,
    }

    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", (media["title"] or "transcript"))[:40].strip("-") or "transcript"
    name = f"transcript_{time.strftime('%Y%m%d_%H%M%S')}_{slug}"
    folder = _replicate_dir()

    md_path = folder / f"{name}.md"
    json_path = folder / f"{name}.json"

    lines = [
        f"# {media['title'] or '转写结果'}",
        "",
        f"- 来源：{url}",
        f"- 平台 / 作者：{media['platform'] or '?'} / {media['author'] or '?'}",
        f"- 时长：{media['duration'] or 0}s",
        f"- 转写后端：{media['transport']} · {media['model']} · {media['elapsed']}s",
        "",
        "## 全文",
        "",
        result.text,
        "",
    ]
    segments = media["segments"] or []
    if segments:
        lines += ["## 时间轴", "", "| 开始(s) | 结束(s) | 文本 |", "| --- | --- | --- |"]
        for seg in segments:
            text = str(seg.get("text", "")).replace("|", "\\|")
            lines.append(f"| {float(seg.get('start') or 0):.2f} | {float(seg.get('end') or 0):.2f} | {text} |")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path.write_text(json.dumps({"text": result.text, "media": media}, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    return {
        "ok": True,
        "text": result.text,
        "media": media,
        "saved_md": str(md_path),
        "saved_json": str(json_path),
        "error": "",
    }