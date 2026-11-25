"""
Duix Avatar (HeyGem) 数字人 API 客户端
实现模特视频声音提取与克隆、克隆声音 TTS、静音视频生成、Docker 路径转换，以及数字人任务的提交和进度查询


API 端点:
  8383: POST /easy/submit  — 提交视频生成任务
  8383: GET  /easy/query   — 查询任务进度
  18180: POST /v1/preprocess_and_tran — 声音克隆预处理
  18180: POST /v1/invoke              — 克隆声音 TTS 合成


Docker 部署:
  docker pull guiji2025/heygem.ai
  # 三个容器: gen-video(8383) / tts(18180) / asr(10095)


配置在 config.py → .env:
  HEYGEM_HOST=http://localhost:8383
  HEYGEM_TTS_HOST=http://localhost:18180
  HEYGEM_DATA_DIR=c:/duix_avatar_data/face2face
"""
import os
import time
import uuid
import shutil
import subprocess
import httpx
from config import settings


# 从统一配置读取（全部来自 .env / config.py，不再用 os.getenv ——
# 因为 os.getenv 读不到 .env 文件，.env 只被 pydantic-settings 加载进 Settings）
HEYGEM_HOST = settings.heygem_host
HEYGEM_TTS_HOST = settings.heygem_tts_host
HEYGEM_DATA_DIR = settings.heygem_data_dir
# TTS 服务有独立的挂载目录（与 gen-video 不同！）
HEYGEM_TTS_DATA_DIR = settings.get_heygem_tts_data_dir()




#  声音克隆 + 克隆声音 TTS
def heygem_voice_clone(source_video: str, lang: str = "zh") -> dict:
    """
    从模特视频中提取声音并克隆。


    流程：
    1. FFmpeg 提取视频音轨 → wav
    2. 复制到 TTS 服务数据目录
    3. 调用 /v1/preprocess_and_tran 进行声音预处理


    Args:
        source_video: 模特视频路径
        lang: 语言（zh/en/ja/ko）


    Returns:
        {"success": bool, "asr_format_audio_url": str,
         "reference_audio_text": str, "message": str}
    """
    if not os.path.exists(source_video):
        return {"success": False, "message": "视频文件不存在"}


    # 1. 提取音频为 wav → 必须放到 TTS 服务的挂载目录！
    audio_name = f"voice_{uuid.uuid4().hex[:8]}.wav"
    os.makedirs(HEYGEM_TTS_DATA_DIR, exist_ok=True)
    audio_path = os.path.join(HEYGEM_TTS_DATA_DIR, audio_name)


    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", source_video,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            audio_path,
        ], check=True, capture_output=True, timeout=30)


        if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1024:
            return {"success": False, "message": "音频提取失败或文件过小"}
    except Exception as e:
        return {"success": False, "message": f"音频提取异常: {e}"}


    # 2. TTS API 需要相对路径（相对于 /code/data/ 即 voice/data 目录）
    container_audio = audio_name


    # 3. 调用声音预处理 API
    try:
        print(f"[HeyGem-Voice] 声音克隆预处理: {container_audio}")
        resp = httpx.post(
            f"{HEYGEM_TTS_HOST}/v1/preprocess_and_tran",
            json={
                "format": "wav",
                "reference_audio": container_audio,
                "lang": lang,
            },
            timeout=60,
        )
        data = resp.json()
        # 处理可能返回 list（验证错误）的情况
        if isinstance(data, list):
            error_msg = data[0].get("msg", str(data)) if data else "未知错误"
            return {"success": False, "message": f"API 返回错误: {error_msg}"}


        asr_url = data.get("asr_format_audio_url", "")
        ref_text = data.get("reference_audio_text", "")


        if asr_url and ref_text:
            print(f"[HeyGem-Voice] 声音克隆成功: text={ref_text[:50]}...")
            return {
                "success": True,
                "asr_format_audio_url": asr_url,
                "reference_audio_text": ref_text,
                "message": "声音克隆成功",
            }


        # 常见错误处理
        msg = data.get("msg", "")
        if "asr failed" in str(msg).lower():
            return {"success": False,
                    "message": "ASR 识别失败。请确保模特视频中有人声且语音清晰、无背景音乐。"}
        if "file not exists" in str(msg).lower():
            return {"success": False,
                    "message": f"音频文件未找到。请检查 TTS 数据目录: {HEYGEM_TTS_DATA_DIR}"}
        return {"success": False, "message": f"预处理失败: {msg}"}


    except httpx.ConnectError:
        return {"success": False, "message": f"TTS 服务 ({HEYGEM_TTS_HOST}) 不可达"}
    except Exception as e:
        return {"success": False, "message": f"声音克隆异常: {e}"}




def heygem_tts_with_cloned_voice(
    text: str,
    asr_format_audio_url: str,
    reference_audio_text: str,
    output_path: str = None,
) -> str:
    """
    使用克隆的声音合成 TTS 音频。


    Args:
        text: 要合成语音的文本
        asr_format_audio_url: 声音克隆返回的预处理音频路径
        reference_audio_text: 声音克隆返回的参考文本
        output_path: 输出音频路径


    Returns:
        wav 音频文件路径，失败返回空字符串
    """
    if output_path is None:
        # TTS 输出放到 gen-video 的共享目录，方便后续视频合成
        output_path = os.path.join(HEYGEM_DATA_DIR,
                                   f"tts_cloned_{uuid.uuid4().hex[:8]}.wav")


    try:
        speaker_id = str(uuid.uuid4())
        print(f"[HeyGem-TTS] 合成: {text[:50]}...")


        resp = httpx.post(
            f"{HEYGEM_TTS_HOST}/v1/invoke",
            json={
                "speaker": speaker_id,
                "text": text,
                "format": "wav",
                "topP": 0.7,
                "max_new_tokens": 1024,
                "chunk_length": 100,
                "repetition_penalty": 1.2,
                "temperature": 0.7,
                "need_asr": False,
                "streaming": False,
                "is_fixed_seed": 0,
                "is_norm": 0,
                "reference_audio": asr_format_audio_url,
                "reference_text": reference_audio_text,
            },
            timeout=120,
        )


        if resp.status_code == 200:
            # 响应是音频二进制数据
            content_type = resp.headers.get("content-type", "")
            if "audio" in content_type or "octet-stream" in content_type:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                if os.path.getsize(output_path) > 1024:
                    print(f"[HeyGem-TTS] [OK] 克隆声音 TTS: {output_path}")
                    return output_path


            # 也可能是 JSON 包含音频路径
            try:
                data = resp.json()
                audio_url = data.get("audio_url", data.get("result", ""))
                if audio_url:
                    return audio_url
            except Exception:
                pass


        print(f"[HeyGem-TTS] 合成失败: HTTP {resp.status_code}")
        return ""


    except httpx.ConnectError:
        print(f"[HeyGem-TTS] TTS 服务 ({HEYGEM_TTS_HOST}) 不可达")
        return ""
    except Exception as e:
        print(f"[HeyGem-TTS] 异常: {e}")
        return ""




#  视频合成
def heygem_health_check() -> bool:
    """检查 HeyGem 视频生成服务是否可达"""
    try:
        resp = httpx.get(f"{HEYGEM_HOST}/easy/query?code=health_check", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False




def prepare_model_video(source_video: str) -> str:
    """
    从模特视频中提取静音版本（用于 HeyGem video_url）。


    HeyGem 需要一段不含音频的模特视频来提取面部运动特征。
    用 FFmpeg 去除音轨，保留视频流。


    Args:
        source_video: 原始模特视频路径（10-30秒正面说话）


    Returns:
        静音视频路径，失败返回空字符串
    """
    import subprocess
    if not os.path.exists(source_video):
        return ""
    muted = os.path.splitext(source_video)[0] + "_muted.mp4"
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", source_video,
            "-c:v", "copy", "-an",  # 复制视频流，去除音频
            muted,
        ], check=True, capture_output=True, timeout=30)
        if os.path.exists(muted) and os.path.getsize(muted) > 1024:
            print(f"[HeyGem] 静音视频已准备: {muted}")
            return muted
    except Exception as e:
        print(f"[HeyGem] 静音视频提取失败: {e}")
    return ""




def _to_container_path(host_path: str) -> str:
    """
    将宿主机路径转换为容器内路径。


    容器挂载: /code/data → 宿主机 HEYGEM_DATA_DIR
    如果文件不在挂载目录下，复制过去。
    """
    # 如果已经是容器路径格式，直接返回
    if host_path.startswith("/code/"):
        return host_path


    abs_path = os.path.abspath(host_path)
    data_dir = os.path.abspath(HEYGEM_DATA_DIR)


    # 在挂载目录下 → 转换为容器路径
    if abs_path.startswith(data_dir):
        rel = os.path.relpath(abs_path, data_dir)
        return f"/code/data/{rel.replace(os.sep, '/')}"


    # 不在挂载目录 → 复制到挂载目录
    os.makedirs(data_dir, exist_ok=True)
    basename = os.path.basename(abs_path)
    dst = os.path.join(data_dir, basename)
    if abs_path != dst:
        import shutil
        shutil.copy2(abs_path, dst)
        print(f"[HeyGem] 文件已复制到共享目录: {dst}")
    return f"/code/data/{basename}"




def heygem_submit_task(
    audio_path: str = None,
    video_path: str = None,
    chaofen: int = 0,
    watermark: int = 0,
) -> dict:
    """
    仅提交任务，不等待。返回 task_code 用于后续查询。


    Returns:
        {"success": bool, "task_code": str, "message": str}
    """
    if not audio_path or not os.path.exists(audio_path):
        return {"success": False, "task_code": "", "message": "音频文件不存在"}


    # HeyGem 必须有模特视频（不是照片！），用于提取面部运动特征
    if not video_path or not os.path.exists(video_path):
        return {"success": False, "task_code": "",
                "message": "请先上传一段模特视频（10-30秒正面说话视频）。HeyGem 需要从视频中提取面部运动特征来驱动口型，照片只能生成静态画面。"}


    task_code = f"task_{uuid.uuid4().hex[:12]}"
    container_audio = _to_container_path(audio_path)
    container_video = _to_container_path(video_path)


    try:
        print(f"[HeyGem] 提交任务: {task_code}")
        print(f"[HeyGem]   形象: {container_video}")
        print(f"[HeyGem]   音频: {container_audio}")
        resp = httpx.post(
            f"{HEYGEM_HOST}/easy/submit",
            json={
                "audio_url": container_audio,
                "video_url": container_video,
                "code": task_code,
                "chaofen": chaofen,
                "watermark_switch": watermark,
                "pn": 1,
            },
            timeout=30,
        )
        data = resp.json()
        api_code = data.get("code", 0)
        api_msg = data.get("msg", "")


        # code=10000 → 任务创建成功
        if api_code == 10000:
            print(f"[HeyGem] 任务已创建: {task_code}")
            return {"success": True, "task_code": task_code,
                    "message": "任务已提交，正在生成数字人视频..."}


        # code=10001 → 忙碌中（已有任务在处理）
        if api_code == 10001:
            print(f"[HeyGem] 服务忙碌: {api_msg}")
            return {"success": False, "task_code": "",
                    "message": "HeyGem 正在处理其他任务，请等待当前任务完成后重试（约3-5分钟）。"}


        # 其他错误
        return {"success": False, "task_code": "",
                "message": f"提交失败 (code={api_code}): {api_msg}"}
    except httpx.ConnectError:
        return {"success": False, "task_code": "",
                "message": "无法连接 HeyGem (8383)。请检查: docker ps | grep avatar"}
    except Exception as e:
        return {"success": False, "task_code": "", "message": f"异常: {e}"}




def heygem_query_task(task_code: str) -> dict:
    """
    查询任务进度 + 如果完成则返回视频路径。


    Returns:
        {"status": int, "progress": int, "msg": str,
         "video_path": str, "finished": bool}
    """
    try:
        resp = httpx.get(f"{HEYGEM_HOST}/easy/query?code={task_code}", timeout=10)
        qdata = resp.json()
        api_code = qdata.get("code", 0)


        # code=10004 → 任务不存在（可能被清除或未成功创建）
        if api_code == 10004:
            return {"status": -1, "progress": 0, "msg": "任务不存在或已被清除",
                    "video_path": "", "finished": False}


        status_data = qdata.get("data", {})
        status_code = status_data.get("status", 1)
        progress = status_data.get("progress", 0)
        msg = status_data.get("msg", "")
        result = status_data.get("result", "")


        video_path = ""
        finished = (status_code == 2 or any(w in str(msg) for w in ["完成","success","合成"]))


        if finished:
            temp_dir = os.path.join(HEYGEM_DATA_DIR, "temp")
            # 1. 搜 temp/{task_code}/ 子目录
            task_subdir = os.path.join(temp_dir, task_code)
            if os.path.isdir(task_subdir):
                for root, dirs, files in os.walk(task_subdir):
                    for f in files:
                        if f.endswith(('.avi', '.mp4', '.mov')):
                            fpath = os.path.join(root, f)
                            if os.path.getsize(fpath) > 100 * 1024:
                                video_path = fpath
                                break
                    if video_path: break
            # 2. 搜 temp/ 下以 task_code 开头的文件（如 task_xxx-r.mp4）
            if not video_path and os.path.isdir(temp_dir):
                for f in sorted(os.listdir(temp_dir),
                                key=lambda x: os.path.getmtime(os.path.join(temp_dir, x)),
                                reverse=True):
                    if f.startswith(task_code) and f.endswith(('.avi','.mp4','.mov')):
                        fpath = os.path.join(temp_dir, f)
                        if os.path.getsize(fpath) > 100 * 1024:
                            video_path = fpath
                            break
            # 3. 搜 result/ 目录
            if not video_path:
                result_dir = os.path.join(HEYGEM_DATA_DIR, "result")
                if os.path.isdir(result_dir):
                    for root, dirs, files in os.walk(result_dir):
                        for f in files:
                            if f.endswith(('.avi', '.mp4', '.mov')):
                                fpath = os.path.join(root, f)
                                if os.path.getsize(fpath) > 100 * 1024:
                                    video_path = fpath
                                    break
                        if video_path: break


        return {
            "status": status_code, "progress": progress, "msg": msg,
            "video_path": video_path, "finished": finished,
        }
    except Exception as e:
        return {"status": -1, "progress": 0, "msg": str(e),
                "video_path": "", "finished": False}




def heygem_generate_video(
    avatar_id: str = None,
    audio_path: str = None,
    video_path: str = None,
    output_dir: str = None,
    chaofen: int = 0,
    watermark: int = 0,
) -> dict:
    """
    提交数字人视频生成任务，轮询直到完成。


    Args:
        avatar_id: 数字人形象 ID（用于生成唯一任务 code）
        audio_path: 音频文件路径（TTS 输出）
        video_path: 参考视频/图片路径（数字人形象源）
        output_dir: 输出目录（保存生成的视频）
        chaofen: 超分辨率增强 (0=关, 1=开)
        watermark: 水印 (0=关, 1=开)


    Returns:
        {"success": bool, "video_path": str, "message": str}
    """
    if not audio_path or not os.path.exists(audio_path):
        return {"success": False, "video_path": "", "message": "音频文件不存在"}


    # 生成唯一任务 code
    task_code = f"task_{uuid.uuid4().hex[:12]}"


    # 转换路径为容器可访问路径
    container_audio = _to_container_path(audio_path)
    container_video = ""
    if video_path and os.path.exists(video_path):
        container_video = _to_container_path(video_path)


    # 如果没有指定参考视频，使用默认
    if not container_video:
        container_video = "/code/data/default_avatar.mp4"


    try:
        # Step 1: 提交任务
        print(f"[HeyGem] 提交任务: code={task_code}")
        print(f"[HeyGem]   音频: {container_audio}")
        print(f"[HeyGem]   视频: {container_video}")


        resp = httpx.post(
            f"{HEYGEM_HOST}/easy/submit",
            json={
                "audio_url": container_audio,
                "video_url": container_video,
                "code": task_code,
                "chaofen": chaofen,
                "watermark_switch": watermark,
                "pn": 1,
            },
            timeout=30,
        )
        data = resp.json()
        if not data.get("success"):
            return {"success": False, "video_path": "",
                    "message": f"任务提交失败: {data.get('msg', '未知错误')}"}


        print(f"[HeyGem] 任务已提交，等待生成...")


        # Step 2: 轮询查询进度（HeyGem 状态码: 1=进行中, 2=完成, 3=失败）
        last_progress = -1
        for attempt in range(180):  # 最多等 6 分钟
            time.sleep(2)
            try:
                qr = httpx.get(
                    f"{HEYGEM_HOST}/easy/query?code={task_code}", timeout=10
                )
                qdata = qr.json()
            except Exception:
                continue


            if not qdata.get("success"):
                continue


            status_data = qdata.get("data", {})
            status_code = status_data.get("status", 1)
            progress = status_data.get("progress", 0)
            msg = status_data.get("msg", "")


            # 打印进度（只在变化时）
            if progress != last_progress:
                print(f"[HeyGem] 进度: {progress}% - {msg}")
                last_progress = progress


            # 状态码 2 = 完成
            if status_code == 2 or "完成" in msg or "success" in msg.lower():
                result_val = status_data.get("result", "")
                print(f"[HeyGem] [OK] 生成完成! result={result_val}")


                # 输出文件在 temp/{task_code}/result.avi 或 result.mp4
                search_paths = [
                    os.path.join(HEYGEM_DATA_DIR, "temp", task_code, "result.avi"),
                    os.path.join(HEYGEM_DATA_DIR, "temp", task_code, "result.mp4"),
                    os.path.join(HEYGEM_DATA_DIR, "result", f"{task_code}.mp4"),
                ]


                # 也尝试从 result 字段解析路径
                if result_val and isinstance(result_val, str) and result_val:
                    result_val = result_val.replace("/code/data/", "")
                    search_paths.insert(0, os.path.join(HEYGEM_DATA_DIR, result_val))


                for sp in search_paths:
                    if os.path.exists(sp) and os.path.getsize(sp) > 1024:
                        print(f"[HeyGem] 视频文件: {sp}")
                        return {"success": True, "video_path": sp,
                                "message": "视频生成成功"}


                # 兜底：扫描 temp 目录下最新的大文件
                temp_dir = os.path.join(HEYGEM_DATA_DIR, "temp")
                if os.path.isdir(temp_dir):
                    for root, dirs, files in os.walk(temp_dir):
                        for f in files:
                            if f.endswith(('.avi', '.mp4', '.mov')):
                                fpath = os.path.join(root, f)
                                if os.path.getsize(fpath) > 1024:
                                    print(f"[HeyGem] 找到: {fpath}")
                                    return {"success": True, "video_path": fpath,
                                            "message": "视频生成成功"}


                return {"success": True, "video_path": "",
                        "message": f"生成完成，请在 {HEYGEM_DATA_DIR}/temp/{task_code}/ 查看输出文件。"}


            # 状态码 3 = 失败
            if status_code == 3 or "fail" in msg.lower() or "error" in msg.lower():
                return {"success": False, "video_path": "",
                        "message": f"生成失败: {msg}"}


        # 超时
        return {"success": False, "video_path": "",
                "message": f"视频生成超时（6分钟），当前进度: {last_progress}%。请稍后在 {HEYGEM_DATA_DIR}/temp/{task_code}/ 查看。"}


    except httpx.ConnectError:
        return {"success": False, "video_path": "",
                "message": "无法连接 HeyGem (8383)。请检查 Docker: docker ps | grep avatar"}
    except Exception as e:
        return {"success": False, "video_path": "",
                "message": f"生成异常: {e}"}