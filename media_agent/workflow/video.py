"""口播视频工作流 — 提词器 / 数字人视频生成


双模式：
- 📺 提词器模式：用户原文 → 提词器滚动显示（不做改写）
- 🔒 数字人模式：用户原文 → TTS 配音 → HeyGem 数字人出镜


用户台词原样使用，不做任何 LLM 改写。
"""
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from config import settings
import os




class VideoState(TypedDict):
    raw_script: str
    mode: str           # "teleprompter" | "heygem"
    avatar_path: str    # 数字人形象照片/视频路径
    teleprompter: str   # 提词器内容 = 用户原文
    audio_path: str
    video_path: str
    task_code: str
    heygem_msg: str




#  节点：提词器（原样保留用户台词）
def node_teleprompter(state: VideoState) -> dict:
    """用户输入即提词器内容，不做任何 LLM 改写"""
    return {"teleprompter": state["raw_script"]}




#   节点：生成视频（TTS + HeyGem）
def node_generate_video(state: VideoState) -> dict:
    """
    数字人模式：TTS 配音 → HeyGem 数字人出镜 → 输出 MP4
    提词器模式：跳过
    """
    mode = state.get("mode", "teleprompter")


    if mode == "teleprompter":
        return {"video_path": "", "audio_path": "", "task_code": "", "heygem_msg": ""}


    # Step 1: TTS 配音 — 优先声音克隆，降级 Edge TTS
    script = state["raw_script"]
    audio_path = ""
    voice_cloned = False
    avatar_video = state.get("avatar_path", "")


    # 1a: 尝试声音克隆（需模特视频 + TTS 服务）
    if avatar_video and os.path.exists(avatar_video):
        try:
            from tools.heygem_client import heygem_voice_clone, heygem_tts_with_cloned_voice
            clone_result = heygem_voice_clone(avatar_video)
            if clone_result["success"]:
                audio_name = f"voice_cloned_{hash(script) % 100000:05d}.wav"
                audio_path = os.path.join(settings.video_output_dir, audio_name)
                audio_path = heygem_tts_with_cloned_voice(
                    script,
                    clone_result["asr_format_audio_url"],
                    clone_result["reference_audio_text"],
                    audio_path,
                )
                if audio_path:
                    voice_cloned = True
                    print(f"[Video] 克隆声音 TTS 完成: {audio_path}")
                else:
                    print("[Video] 克隆声音 TTS 失败，降级 Edge TTS")
            else:
                print(f"[Video] 声音克隆失败: {clone_result['message']}，降级 Edge TTS")
        except Exception as e:
            print(f"[Video] 声音克隆异常: {e}，降级 Edge TTS")


    # 1b: 降级 Edge TTS
    if not audio_path:
        try:
            from tools.media_tools import generate_tts
            audio_name = f"voiceover_{hash(script) % 100000:05d}.mp3"
            audio_path = os.path.join(settings.video_output_dir, audio_name)
            os.makedirs(os.path.dirname(audio_path), exist_ok=True)
            audio_path = generate_tts(script, audio_path)
            if audio_path:
                print(f"[Video] Edge TTS 配音完成: {audio_path}")
        except Exception as e:
            print(f"[Video] Edge TTS 失败: {e}")


    video_path = ""
    heygem_msg = ""


    # Step 2: 按模式生成视频
    task_code = ""
    if mode == "heygem" and audio_path:
        from tools.heygem_client import heygem_health_check, heygem_submit_task


        if not heygem_health_check():
            heygem_msg = (
                "HeyGem 服务未启动。检查: `docker ps | grep avatar`\n"
                "部署: docker pull guiji2025/duix.avatar\n"
                "端口: gen-video(8383) / tts(18180) / asr(10095)"
            )
        else:
            avatar_video = state.get("avatar_path", "")
            if not avatar_video or not os.path.exists(avatar_video):
                heygem_msg = (
                    "请先上传一段模特视频！\n\n"
                    "HeyGem 需要一段 10-30 秒的正面说话视频（mp4），\n"
                    "用于提取面部运动特征来驱动口型。\n"
                    "照片只能生成静态画面，必须使用视频！\n\n"
                    "在左侧「📷 上传模特视频」处上传后重新点击生成。"
                )
            else:
                # 准备静音模特视频（HeyGem 需要不含音频的视频文件）
                from tools.heygem_client import prepare_model_video
                muted_video = prepare_model_video(avatar_video)
                if not muted_video:
                    # 如果 ffmpeg 不可用，直接用原视频
                    muted_video = avatar_video


                print(f"[Video] 模特视频: {muted_video}")
                submit = heygem_submit_task(
                    audio_path=os.path.abspath(audio_path),
                    video_path=os.path.abspath(muted_video),
                )
                if submit["success"]:
                    task_code = submit["task_code"]
                    heygem_msg = f"任务已提交 (code: {task_code})，约 2-3 分钟后点击刷新查看"
                else:
                    heygem_msg = submit["message"]


    return {
        "audio_path": audio_path,
        "video_path": video_path,
        "task_code": task_code,
        "heygem_msg": heygem_msg,
    }




# 构建图
builder = StateGraph(VideoState)
builder.add_node("teleprompter", node_teleprompter)
builder.add_node("generate", node_generate_video)


builder.add_edge(START, "teleprompter")
builder.add_edge("teleprompter", "generate")
builder.add_edge("generate", END)


video_graph = builder.compile()




def run_video(raw_script: str, mode: str = "teleprompter",
              avatar_path: str = "") -> dict:
    """运行口播视频工作流


    Args:
        raw_script: 用户原始台词（原样使用，不做改写）
        mode: "teleprompter" | "heygem"
        avatar_path: 数字人形象照片/视频路径


    Returns:
        {"teleprompter": str, "audio_path": str, "video_path": str,
         "task_code": str, "heygem_msg": str}
    """
    return video_graph.invoke({
        "raw_script": raw_script,
        "mode": mode,
        "avatar_path": avatar_path,
    })