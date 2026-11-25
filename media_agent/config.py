"""中央配置 —— Pydantic Settings + .env

约定：
1. 字段名即 .env 键名（大小写不敏感），例如 ``api_key`` ↔ ``API_KEY``；
2. 缺 Key **不抛异常**（默认空串），由页面提示"未配置"，保证 ``import config`` 永不崩；
3. 目录由 :meth:`Settings.ensure_dirs` 创建，失败只告警。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- LLM 配置 ----------------
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model_name: str = "deepseek-flash"
    llm_timeout: int = 120

    # ---------------- 目录 ----------------
    cache_dir: str = "./.cache"
    report_dir: str = "./.cache/reports"
    plan_dir: str = "./.cache/plans"
    video_output_dir: str = "./.cache/videos"
    image_dir: str = "./.cache/images"
    image_output_dir: str = "./.cache/images"
    audio_dir: str = "./.cache/audio"

    # ---------------- 媒体工具链 ----------------
    ffmpeg_path: str = ""

    # ---------------- 外部素材 / 第三方项目 ----------------
    # 本地 douyin 采集项目（erma0/douyin）的目录；留空则数据复盘页提示用户自己填
    douyin_project_dir: str = ""
    # 视频剪辑默认背景音乐；留空则由页面让用户自己上传
    default_bgm_path: str = ""

    # ---------------- DeepAgents 编排层 ----------------
    deepagent_model: str = ""

    # ---------------- 热点监控（TrendRadar / NewsNow 协议）----------------
    trendradar_api_url: str = "https://newsnow.busiyi.world/api/s"

    # ---------------- TTS 语音合成 ----------------
    tts_enabled: bool = True
    tts_voice: str = "zh-CN-XiaoxiaoNeural"

    # ---------------- HeyGem / Duix Avatar 数字人 ----------------
    heygem_host: str = "http://localhost:8383"
    heygem_tts_host: str = "http://localhost:18180"
    heygem_tts_url: str = "http://localhost:18180"
    heygem_asr_url: str = "http://localhost:10095"
    heygem_data_dir: str = "c:/duix_avatar_data/face2face"
    heygem_tts_data_dir: str = "c:/duix_avatar_data/tts"

    # ---------------- 配图生成（可选，OpenAI 兼容接口）----------------
    image_gen_base_url: str = "https://api.siliconflow.cn/v1"
    image_gen_model: str = "Kwai-Kolors/Kolors"
    IMAGE_API_KEY: str = ""

    DATABASE_URL: str = "postgresql://postgres:password@localhost:5433/langgraph"

    # ---- 外部 MCP / 联网搜索 ----
    BOCHA_API_KEY: str = ""
    BOCHA_SEARCH_URL: str = "https://api.bochaai.com/v1/web-search"

    # ---- 数据采集规模控制（测试阶段保持 false：只留标题/链接/短摘要，不抓正文与配图）----
    COLLECT_FULL_TEXT: bool = False   # true=抓正文与 og:image，false=省 token、省库
    MAX_TEXT_CHARS: int = 400         # 单条正文最多保留多少字（COLLECT_FULL_TEXT=true 时生效）
    MAX_ITEMS_PER_SOURCE: int = 30    # 单个数据源最多保留多少条，超过直接截断

    ASR_MODE: str = "http"            # http=AutoDL 转写服务（推荐，贴链接即可）；ws=旧 WS 服务；local=本机 funasr
    ASR_HTTP_URL: str = "http://127.0.0.1:8000"   # AutoDL 实例内的 transcribe_server（走 SSH 隧道访问）
    ASR_API_TOKEN: str = ""           # 留空则读环境变量 / .cache/autodl.json 的 api_token
    FUNASR_WS_URL: str = "ws://192.168.4.9:6006"

    # ---- AutoDL 实例（FunASR 跑在 4090 上；密码放 .cache/autodl.json 或环境变量 AUTODL_PASSWORD）----
    AUTODL_HOST: str = "connect.nmb1.seetacloud.com"
    AUTODL_PORT: int = 17653
    AUTODL_USER: str = "root"
    AUTODL_REMOTE_DIR: str = "/root/autodl-tmp/asr_in"
    AUTODL_PASSWORD: str = ""         # 留空则读环境变量 / .cache/autodl.json

    @property
    def asr_ready(self) -> bool:
        """是否走 AutoDL 远程转写。"""
        mode = self.ASR_MODE.strip().lower()
        if mode == "http":
            return bool(self.ASR_HTTP_URL.strip())
        return bool(self.FUNASR_WS_URL.strip())

    @property
    def bocha_key_ok(self) -> bool:
        """页面判断"能不能联网检索"，避免把空 Key 发出去。"""
        return len(self.BOCHA_API_KEY.strip()) > 10

    @property
    def api_key_ok(self) -> bool:
        """页面上判断"能不能调模型"，避免把空 Key 直接甩给 SDK。"""
        return len(self.api_key.strip()) > 10

    def get_deepagent_model(self) -> str:
        """DeepAgent 模型名，默认复用 LLM 模型。"""
        return self.deepagent_model or self.model_name

    def get_heygem_tts_data_dir(self) -> str:
        """TTS 容器的数据目录（与 gen-video 的挂载目录不同，必须分开）。"""
        return self.heygem_tts_data_dir or os.path.join(
            os.path.dirname(self.heygem_data_dir.rstrip("/\\")), "tts"
        )

    def get_image_api_key(self) -> str:
        """配图用的 Key：优先 IMAGE_API_KEY，其次复用 LLM 的 Key。"""
        return (self.IMAGE_API_KEY or self.api_key).strip()

    def ensure_dirs(self) -> None:
        """创建运行期目录（幂等）。单个目录失败只告警，不影响启动。"""
        for raw in (
            self.cache_dir,
            self.report_dir,
            self.plan_dir,
            self.video_output_dir,
            self.image_dir,
            self.image_output_dir,
            self.audio_dir,
        ):
            try:
                Path(raw).mkdir(parents=True, exist_ok=True)
            except OSError as exc:  # 权限/磁盘满等
                log.warning("目录创建失败 %s: %s", raw, exc)


settings = Settings()

# 兼容旧写法：.env 里写 DEEPSEEK_API_KEY 也能用
if not settings.api_key:
    settings.api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()

settings.ensure_dirs()
