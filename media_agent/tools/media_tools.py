"""
媒体处理工具 — 视频下载 / 字幕提取 / 文章抓取 / 图片生成 / TTS 语音


所有函数均支持自动降级：真实依赖不可用时回退到 stub 实现。
"""


import os
import re
import shutil
from config import settings




def extract_url(text: str) -> str:
    """从混合文本中提取 URL（处理小红书/抖音等分享文本）


    小红书分享格式示例:
        "57 【标题】 emoji ID emoji https://www.xiaohongshu.com/..."


    Returns:
        提取到的第一个 HTTP(S) URL，未找到则返回原文本
    """
    if not text:
        return text


    # 如果本身就是纯 URL，直接返回
    text = text.strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text


    # 匹配 HTTP(S) URL
    url_pattern = r'https?://[^\s一-鿿　-〿＀-￯]+'
    matches = re.findall(url_pattern, text)
    if matches:
        url = matches[0].rstrip(".,;:!?）)】』\"'")
        return url


    return text




#  图片生成
def _check_openai_available() -> bool:
    """检查 openai 库是否可用"""
    try:
        import openai
        return True
    except ImportError:
        return False




def generate_image(prompt: str, output_path: str = None,
                   size: str = "1024x1024") -> str:
    """
    调用 OpenAI DALL-E / 兼容 API 生成图片并保存到本地。


    支持任何兼容 OpenAI 的 API（如 SiliconFlow、本地 Stable Diffusion 等）。


    Args:
        prompt: 图片提示词
        output_path: 输出路径，为空则自动生成
        size: 图片尺寸


    Returns:
        保存后的图片路径，失败返回空字符串
    """
    if not _check_openai_available():
        print("[图片生成] openai 未安装，跳过")
        return _stub_generate_image(prompt, output_path)


    api_key = settings.get_image_api_key()
    if not api_key or api_key == "your-api-key":
        print("[图片生成] 未配置 API Key，跳过")
        return _stub_generate_image(prompt, output_path)


    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url=settings.image_gen_base_url,
        )


        print(f"[图片生成] 调用 {settings.image_gen_model}: {prompt[:80]}...")
        response = client.images.generate(
            model=settings.image_gen_model,
            prompt=prompt,
            n=1,
            size=size,
        )


        image_url = response.data[0].url
        if not image_url:
            print("[图片生成] 返回 URL 为空")
            return _stub_generate_image(prompt, output_path)


        # 下载图片
        import httpx
        img_data = httpx.get(image_url, timeout=60).content


        if output_path is None:
            output_path = os.path.join(
                settings.image_output_dir,
                f"img_{hash(prompt) % 100000:05d}.png"
            )


        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(img_data)


        print(f"[图片生成] [OK] 已保存: {output_path}")
        return output_path


    except Exception as e:
        print(f"[图片生成] 失败: {e}")
        return _stub_generate_image(prompt, output_path)




def _stub_generate_image(prompt: str, output_path: str = None) -> str:
    """图片生成的 stub 降级 — 生成纯色占位图"""
    if output_path is None:
        output_path = os.path.join(
            settings.image_output_dir,
            f"placeholder_{hash(prompt) % 100000:05d}.png"
        )


    os.makedirs(os.path.dirname(output_path), exist_ok=True)


    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (1080, 1920), color=(20, 20, 30))
        draw = ImageDraw.Draw(img)
        # 绘制渐变色带模拟氛围
        for y in range(1920):
            r = int(20 + (y / 1920) * 30)
            g = int(20 + (y / 1920) * 25)
            b = int(30 + (y / 1920) * 50)
            draw.line([(0, y), (1080, y)], fill=(r, g, b))
        # 居中绘制提示文字
        text = prompt[:40] + "..." if len(prompt) > 40 else prompt
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 36)
        except Exception:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((1080 - tw) / 2, (1920 - th) / 2), text, fill=(200, 200, 220), font=font)
        img.save(output_path)
        print(f"[图片生成-stub] 占位图: {output_path}")
        return output_path
    except Exception as e:
        print(f"[图片生成-stub] [FAIL] 失败: {e}")
        return ""




#  TTS 语音合成
def _check_edge_tts_available() -> bool:
    """检查 edge-tts 是否可用"""
    try:
        import edge_tts
        return True
    except ImportError:
        return False




async def _generate_tts_async(text: str, output_path: str) -> bool:
    """Edge TTS 异步生成（内部实现）"""
    import edge_tts


    # 截断过长文本
    if len(text) > 3000:
        text = text[:3000]


    communicate = edge_tts.Communicate(
        text=text,
        voice=settings.tts_voice,
    )
    await communicate.save(output_path)
    return os.path.exists(output_path)




def generate_tts(text: str, output_path: str = None) -> str:
    """
    使用 Edge TTS 生成语音配音。


    Args:
        text: 要合成语音的文本
        output_path: 输出 mp3 路径


    Returns:
        音频文件路径，失败返回空字符串
    """
    if not settings.tts_enabled:
        print("[TTS] 已禁用")
        return ""


    if not _check_edge_tts_available():
        print("[TTS] edge-tts 未安装，跳过")
        return ""


    if output_path is None:
        output_path = os.path.join(
            settings.video_output_dir,
            f"tts_{hash(text) % 100000:05d}.mp3"
        )


    os.makedirs(os.path.dirname(output_path), exist_ok=True)


    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        success = loop.run_until_complete(_generate_tts_async(text, output_path))
        loop.close()


        if success:
            print(f"[TTS] [OK] 已生成: {output_path}")
            return output_path
        else:
            print("[TTS] [FAIL] 生成失败")
            return ""
    except Exception as e:
        print(f"[TTS] [FAIL] 异常: {e}")
        return ""




#  视频下载 — 三级降级：videodl → yt-dlp → stub
def _check_videodl_available() -> bool:
    """检查 videodl (videofetch) 库是否可用"""
    try:
        from videodl import videodl
        return True
    except ImportError:
        return False




def _check_ytdlp_available() -> bool:
    """检查 yt-dlp 是否可用"""
    return shutil.which("yt-dlp") is not None




def download_video_videodl(url: str, output_dir: str = None) -> str:
    """
    使用 videodl (CharlesPikachu/videodl) 下载视频。


    支持 100+ 平台：抖音、快手、小红书、B站、YouTube、TikTok、
    优酷、腾讯、爱奇艺、微博、知乎等。


    Args:
        url: 视频 URL
        output_dir: 输出目录（默认 videodl_outputs）


    Returns:
        下载后的视频路径，失败返回空字符串
    """
    if not _check_videodl_available():
        return ""


    from videodl import videodl


    if output_dir is None:
        output_dir = os.path.join(settings.video_output_dir, "downloads")
    os.makedirs(output_dir, exist_ok=True)


    try:
        # 创建 VideoClient，自动识别平台
        client = videodl.VideoClient(
            allowed_video_sources=[],
            init_video_clients_cfg={
                "BilibiliVideoClient": {"work_dir": output_dir},
                "DouyinVideoClient": {"work_dir": output_dir},
                # 更多平台会自动使用默认 work_dir
            },
            apply_common_video_clients_only=False,
        )


        # 1. 解析视频 URL → 获取下载链接
        print(f"[videodl] 解析: {url[:80]}...")
        video_infos = client.parsefromurl(url)


        if not video_infos:
            print("[videodl] 未找到可下载视频")
            return ""


        # 2. 下载视频（download 方法不返回值，文件路径存在 save_path 属性中）
        print(f"[videodl] 找到 {len(video_infos)} 个视频，开始下载...")
        client.download(video_infos)


        # 3. 从 VideoInfo.save_path 获取下载文件路径
        for info in video_infos:
            save_path = info.save_path if hasattr(info, 'save_path') else ""
            if save_path and os.path.isfile(save_path) and os.path.getsize(save_path) > 1024:
                print(f"[videodl] [OK] 下载完成: {save_path}")
                return save_path


        print("[videodl] 下载完成但未找到输出文件")
        return ""


    except Exception as e:
        print(f"[videodl] 失败: {e}")
        return ""




def download_video(url: str, output_dir: str = None) -> str:
    """
    下载视频到本地（三级降级：videodl → yt-dlp → stub）。


    Args:
        url: 视频 URL（支持混合文本，自动提取 URL）
        output_dir: 输出目录


    Returns:
        下载后的文件路径
    """
    # 从混合文本中提取纯 URL（兼容小红书/抖音分享格式）
    url = extract_url(url)


    if output_dir is None:
        output_dir = os.path.join(settings.video_output_dir, "downloads")
    os.makedirs(output_dir, exist_ok=True)


    # 第一级：videodl（100+ 平台，优先高清无水印）
    result = download_video_videodl(url, output_dir)
    if result:
        return result


    # 第二级：yt-dlp（Python API，自动用 imageio-ffmpeg 合成音视频；不依赖系统 PATH）
    try:
        result = _ytdlp_download(url, output_dir)
        if result:
            return result
    except Exception as exc:  # noqa: BLE001 - 下载失败要能降级而不是崩
        print(f"[下载] yt-dlp 异常: {type(exc).__name__}: {exc}")


    # 第三级：stub 降级
    return _stub_download_video(url, output_dir)


def _ytdlp_download(url: str, output_dir: str, max_height: int = 720) -> str:
    """用 yt-dlp 的 Python API 下载，返回本地文件路径（失败返回 ""）。

    - 优先 ≤720p 的 mp4（文件小、上传快、ASR 够用）
    - B站是 DASH 分离流，需要 ffmpeg 合并；本机 ffmpeg 不在 PATH，
      这里把 moviepy 自带的 imageio-ffmpeg 二进制交给 yt-dlp
    """
    try:
        import yt_dlp
    except ImportError:
        print("[下载] 未安装 yt-dlp（uv add yt-dlp）")
        return ""

    # 用户粘进来的多半是整段分享文案（"【标题】 https://..."），先抠出 URL
    url = extract_url(url or "")
    if not url.lower().startswith(("http://", "https://")):
        print(f"[下载] 不是有效链接：{url[:80]}")
        return ""

    ffmpeg_exe = None
    try:
        import imageio_ffmpeg

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - 没有也不影响单流视频
        ffmpeg_exe = None

    output_dir = output_dir or os.path.join(settings.video_output_dir, "downloads")
    os.makedirs(output_dir, exist_ok=True)
    template = os.path.join(output_dir, "%(id)s.%(ext)s")

    opts = {
        "outtmpl": template,
        "format": (
            f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/"
            f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"
        ),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "retries": 3,
        "socket_timeout": 30,
        "http_headers": {
            "Referer": "https://www.bilibili.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
            ),
        },
    }
    if ffmpeg_exe:
        opts["ffmpeg_location"] = ffmpeg_exe
    cookies = os.path.join(settings.douyin_project_dir, "config", "cookies.txt") if settings.douyin_project_dir else ""
    if cookies and os.path.exists(cookies):
        opts["cookiefile"] = cookies

    print(f"[下载] yt-dlp 解析: {url[:80]}…")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001 - 链接失效/需要登录/网络问题都走这里
        msg = str(exc).replace("\n", " ")[:200]
        print(f"[下载] yt-dlp 失败：{msg}")
        return ""

    try:
        path = ydl.prepare_filename(info) if info else ""
    except Exception:  # noqa: BLE001
        path = ""
    candidates = [path]
    if path:
        base = os.path.splitext(path)[0]
        candidates += [base + ext for ext in (".mp4", ".mkv", ".webm", ".m4a", ".flv")]
    for cand in candidates:
        if cand and os.path.exists(cand) and os.path.getsize(cand) > 1024:
            size_mb = os.path.getsize(cand) / 1024 / 1024
            print(f"[下载] [OK] yt-dlp: {cand} ({size_mb:.1f}MB)")
            return cand

    print("[下载] yt-dlp 完成但没找到输出文件")
    return ""




def _stub_download_video(url: str, output_dir: str) -> str:
    """模拟下载（无可用下载器时降级）— 不创建空文件避免 ffmpeg 连环失败"""
    filename = f"video_{hash(url) % 10000}.mp4"
    output_path = os.path.join(output_dir, filename)
    # 清理之前的空文件
    if os.path.exists(output_path):
        os.remove(output_path)
    print(f"[工具] 模拟下载(无可用下载器): {url[:60]}...")
    return ""  # 返回空表示下载失败，不再创建空文件




#  视频转音频 + 语音转文字 (ASR)
def _check_ffmpeg_available() -> bool:
    """检查 FFmpeg 是否可用"""
    return shutil.which("ffmpeg") is not None




def _check_funasr_available() -> bool:
    """检查 FunASR 是否可用"""
    try:
        from funasr import AutoModel  # noqa: F401
        return True
    except ImportError:
        return False




def video_to_audio(
    input_video: str,
    output_audio: str = None,
    bitrate: str = "192k",
    fps: int = 44100,
) -> str:
    """
    从视频中提取音频（使用 FFmpeg）。


    Args:
        input_video: 输入视频文件路径
        output_audio: 输出音频路径（默认与视频同目录，.mp3 后缀）
        bitrate: 音频码率（默认 192k）
        fps: 采样率（默认 44100 Hz）


    Returns:
        音频文件路径，失败返回空字符串
    """
    if not _check_ffmpeg_available():
        print("[video_to_audio] FFmpeg 未安装，跳过")
        return ""


    if output_audio is None:
        base = os.path.splitext(input_video)[0]
        output_audio = f"{base}_audio.mp3"


    try:
        import subprocess
        cmd = [
            "ffmpeg", "-y",
            "-i", input_video,
            "-vn",                    # 不要视频流
            "-b:a", bitrate,          # 音频码率
            "-ar", str(fps),           # 采样率
            "-ac", "1",               # 单声道（ASR 不需要立体声）
            output_audio,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)


        if os.path.exists(output_audio) and os.path.getsize(output_audio) > 0:
            print(f"[video_to_audio] [OK] {output_audio}")
            return output_audio
        else:
            print("[video_to_audio] 输出文件为空")
            return ""
    except Exception as e:
        print(f"[video_to_audio] 失败: {e}")
        return ""




# FunASR 模型（懒加载单例）
_funasr_model = None




def _get_funasr_model():
    """获取 FunASR 模型实例（懒加载，复用）"""
    global _funasr_model
    if _funasr_model is None:
        from funasr import AutoModel
        print("[FunASR] 加载模型 Fun-ASR-Nano-2512 + fsmn-vad ...")
        _funasr_model = AutoModel(
            model="FunAudioLLM/Fun-ASR-Nano-2512",
            vad_model="fsmn-vad",
            device="cuda",  # 有 GPU 用 "cuda"，无 GPU 改为 "cpu"
        )
    return _funasr_model




def _remote_asr_enabled() -> bool:
    """是否把转写交给 AutoDL 上的 FunASR（config.ASR_MODE=remote）。"""
    try:
        from config import settings
        return bool(settings.asr_ready)
    except Exception:  # noqa: BLE001 - 配置异常就当远程没开
        return False


def audio_to_text(audio_path: str) -> str:
    """
    音频转文字 — 优先 AutoDL 上的 FunASR（GPU），失败再退回本机 FunASR。

    远程链路：SFTP 上传 → WebSocket 推流 → 返回文本（见 tools/autodl_funasr.py）。
    远程不可用时行为与原来完全一致：本地 Fun-ASR-Nano + VAD。

    Args:
        audio_path: 音频文件路径或 URL

    Returns:
        转录文本，失败返回空字符串
    """
    # 第一优先：AutoDL 远程 FunASR
    if _remote_asr_enabled():
        try:
            from tools.autodl_funasr import transcribe
            result = transcribe(audio_path)
            if result.ok and result.text:
                print(f"[AutoDL/ASR] [OK] {result.brief}")
                return result.text
            print(f"[AutoDL/ASR] 远程未返回文本：{result.error or '识别为空'}，改走本机 FunASR")
        except Exception as exc:  # noqa: BLE001 - 远程失败不影响本地
            print(f"[AutoDL/ASR] 调用异常：{exc}，改走本机 FunASR")

    if not _check_funasr_available():
        print("[FunASR] 未安装 funasr，跳过")
        return ""


    try:
        model = _get_funasr_model()
        result = model.generate(input=audio_path)


        # FunASR 返回格式：[{"text": "识别结果", "timestamp": [...]}, ...]
        if result and isinstance(result, list) and len(result) > 0:
            text = result[0].get("text", "").strip()
            if text:
                print(f"[FunASR] [OK] 识别 {len(text)} 字")
                return text


        print("[FunASR] 未识别到语音内容")
        return ""
    except Exception as e:
        print(f"[FunASR] 失败: {e}")
        return ""




def extract_audio_text(video_path: str) -> str:
    """
    视频 → 音频 → 文本（完整流水线）。


    流程：
    1. FFmpeg 提取音频 (video_to_audio)
    2. FunASR 转为文字 (audio_to_text)
    失败时降级为 FunASR 直接处理视频，再失败返回演示文案。


    Args:
        video_path: 视频文件路径或 URL


    Returns:
        提取的文字
    """
    # 如果是 URL，直接传给 FunASR（FunASR 支持 URL 输入）
    if video_path.startswith("http://") or video_path.startswith("https://"):
        text = audio_to_text(video_path)
        if text:
            return text


    # 如果视频路径为空（下载失败），直接返回空
    if not video_path:
        print("[extract_audio_text] 视频路径为空，下载可能失败")
        return ""


    # 如果是 URL，直接传给 FunASR（FunASR 支持 URL 输入）
    if video_path.startswith("http://") or video_path.startswith("https://"):
        text = audio_to_text(video_path)
        if text:
            return text
        return ""


    # 本地文件不存在
    if not os.path.exists(video_path):
        print(f"[extract_audio_text] 视频文件不存在: {video_path}")
        return ""


    # 远程 AutoDL 可直接吃视频（服务器上有 ffmpeg），不必本地先提音频
    if _remote_asr_enabled():
        text = audio_to_text(video_path)
        if text:
            return text


    # Step 1: 视频 → 音频
    audio_path = video_to_audio(video_path)
    if audio_path:
        # Step 2: 音频 → 文本
        text = audio_to_text(audio_path)
        if text:
            return text


    # 降级：FunASR 直接处理本地视频
    if _check_funasr_available():
        text = audio_to_text(video_path)
        if text:
            return text


    return ""




#  文章抓取
def _check_trafilatura_available() -> bool:
    """检查 trafilatura 是否可用"""
    try:
        import trafilatura
        return True
    except ImportError:
        return False




def fetch_article(url: str) -> str:
    """
    抓取网页文章内容（优先 trafilatura，否则返回演示文案）。


    Args:
        url: 文章 URL


    Returns:
        提取的文章内容
    """
    if _check_trafilatura_available():
        try:
            import trafilatura
            downloaded = trafilatura.fetch_url(url)
            text = trafilatura.extract(downloaded)
            if text and len(text) > 50:
                print(f"[Trafilatura] [OK] 抓取 {len(text)} 字")
                return text
        except Exception as e:
            print(f"[Trafilatura] 失败: {e}")


    print(f"[工具] 模拟抓取: {url[:60]}...")
    return _demo_script()




#  演示文案
def _demo_script() -> str:
    """演示用示例文案"""
    return """你有没有发现，那些月入十万的人都有一个共同点？
他们从来不把时间浪费在重复劳动上。
我之前也是什么事都亲力亲为，忙了一天发现什么也没做成。
后来我学会了用AI工具，效率直接翻了5倍。
今天就把我最常用的3个AI工具分享给你。
第一个，用AI写周报，5分钟搞定以前2小时的工作...
第二个，用AI做数据分析，不用学Python也能做可视化...
第三个，用AI生成PPT，老板以为你加班了一整晚...
这三个工具搭配使用，每天至少帮你省3个小时。
关注我，下期分享更多效率提升技巧。"""