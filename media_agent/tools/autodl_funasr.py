"""AutoDL 远程 FunASR 转写 — 内容复刻节点的语音转文字后端。

链路（和页面上的「内容复刻」一致）：

    视频/音频（本地文件或 URL）
        │  ① 可选：yt-dlp 下载（由 tools.media_tools.download_video 负责）
        │  ② SFTP 上传到 AutoDL 实例（留档 + 便于排查）
        │  ③ WebSocket 推流给实例上的 FunASR 服务（默认 6006 端口）
        ▼
    {"text": "...", "sentences": [...], "inference_ms": 1234}

服务端协议（/root/funasr_ws/funasr_ws_server.py，协议版本 1）：

    服务端 → {"type": "ready", "protocol": 1, "device": "cuda:0", ...}
    客户端 → {"type": "start", "filename": "xxx.wav"}
    服务端 → {"type": "started", "session_id": "..."}
    客户端 → <二进制音频分块> * N
    客户端 → {"type": "eof"}
    服务端 → {"type": "result", "text": "...", "sentences": [...],
              "inference_ms": 1234, "queue_ms": 0, "audio_bytes": 98765}
    服务端 → {"type": "error", "code": "...", "message": "..."}   # 出错时

设计要点：
- 传输层与业务层分开：`_ws_transcribe()` 只负责协议，`transcribe_file()` 负责上传+降级。
- 任何异常都不向上抛（除非调用方显式要求）：返回 `TranscribeResult(ok=False, error=...)`，
  让 `media_tools` 能无缝降级到本地 FunASR。
- 密码不写进代码：优先环境变量 `AUTODL_PASSWORD`，其次本地 `.cache/autodl.json`（已被 gitignore）。

命令行用法：

    # 转写本地文件（自动上传）
    uv run python -m tools.autodl_funasr D:\\video\\demo.mp4

    # 转写 URL（先下载再上传，走完整链路）
    uv run python -m tools.autodl_funasr "https://v.douyin.com/xxxx/" --download

    # 只探测服务状态
    uv run python -m tools.autodl_funasr --probe
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from urllib.parse import urlparse

from config import settings

# ---------------------------------------------------------------- 常量

CHUNK_BYTES = 1 << 20          # 每次推 1MB
DEFAULT_TIMEOUT = 300.0        # 单次转写总超时（秒）
PROBE_TIMEOUT = 3.0
ALLOWED_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".mp4", ".webm", ".aac"}
STATE_FILE = Path(".cache/autodl.json")   # 本机私有配置（gitignore）


# ---------------------------------------------------------------- 配置

def _state() -> dict:
    """读取本机 AutoDL 配置：环境变量优先，其次 .cache/autodl.json。"""
    data: dict = {}
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}

    return {
        "host": os.getenv("AUTODL_HOST") or data.get("host", ""),
        "port": int(os.getenv("AUTODL_PORT") or data.get("port", 22)),
        "user": os.getenv("AUTODL_USER") or data.get("user", "root"),
        "password": os.getenv("AUTODL_PASSWORD") or data.get("password", ""),
        "remote_dir": os.getenv("AUTODL_REMOTE_DIR") or data.get("remote_dir", "/root/autodl-tmp/asr_in"),
        "ws_url": os.getenv("FUNASR_WS_URL") or data.get("ws_url") or settings.FUNASR_WS_URL,
        "asr_mode": os.getenv("ASR_MODE") or data.get("asr_mode") or settings.ASR_MODE,
        "http_url": os.getenv("ASR_HTTP_URL") or data.get("http_url") or settings.ASR_HTTP_URL,
        "api_token": os.getenv("ASR_API_TOKEN") or data.get("api_token") or settings.ASR_API_TOKEN,
    }


def asr_mode() -> str:
    """当前转写后端：http（推荐）/ remote（旧 WS）/ local（本机 funasr）。"""
    mode = (_state()["asr_mode"] or "http").lower()
    return "remote" if mode == "ws" else mode


def ws_url() -> str:
    return _state()["ws_url"] or "ws://127.0.0.1:6006"


def http_url() -> str:
    return (_state()["http_url"] or "http://127.0.0.1:8000").rstrip("/")


def api_token() -> str:
    return (_state()["api_token"] or "").strip()


def save_credentials(host: str = "", port: int = 0, user: str = "",
                     password: str = "", remote_dir: str = "",
                     ws: str = "", http: str = "", token: str = "") -> Path:
    """把 AutoDL 连接信息写进 .cache/autodl.json（本机私有，不进 git）。"""
    data = _state()
    for key, value in (
        ("host", host), ("port", port), ("user", user), ("password", password),
        ("remote_dir", remote_dir), ("ws_url", ws), ("http_url", http),
        ("api_token", token),
    ):
        if value:
            data[key] = value
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return STATE_FILE


# ---------------------------------------------------------------- 结果对象

@dataclass
class TranscribeResult:
    """一次转写的结果。ok=False 时只有 error 有意义。"""

    ok: bool = False
    text: str = ""
    sentences: list[dict] = field(default_factory=list)
    transport: str = ""          # http | ws | local | none
    remote_path: str = ""        # 上传到 AutoDL 的路径
    audio_bytes: int = 0
    inference_ms: int = 0
    elapsed: float = 0.0
    error: str = ""
    media: dict = field(default_factory=dict)   # http 后端返回：标题/作者/时长/封面/平台

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def brief(self) -> str:
        if not self.ok:
            return f"失败：{self.error}"
        size = self.audio_bytes
        size_str = ""
        if size:
            size_str = f" · {size/1024/1024:.1f}MB" if size >= 1024 * 1024 else f" · {size/1024:.0f}KB"
        return (
            f"{self.transport}{size_str} · "
            f"推理 {self.inference_ms/1000:.1f}s · 全链路 {self.elapsed:.1f}s · {len(self.text)} 字"
        )


# ---------------------------------------------------------------- SSH 隧道

class SSHTunnel:
    """把 AutoDL 实例上的 127.0.0.1:6006 映射到本机随机端口。

    AutoDL 的公网地址通常只开 SSH 端口，FunASR 的 6006 只能在实例内部访问，
    所以默认走 SSH 隧道；如果 .cache/autodl.json 里配了可达的 ws_url，也可以直连。
    """

    def __init__(self, remote_host: str = "127.0.0.1", remote_port: int = 6006) -> None:
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.local_port = 0
        self._client = None
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self, timeout: float = 20.0) -> int:
        import paramiko

        conf = _state()
        if not conf["host"] or not conf["password"]:
            raise RuntimeError("缺少 AutoDL 连接信息，无法建立 SSH 隧道")

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._client.connect(conf["host"], port=conf["port"], username=conf["user"],
                             password=conf["password"], timeout=timeout,
                             banner_timeout=timeout, auth_timeout=timeout)
        transport = self._client.get_transport()
        if transport is None or not transport.is_active():
            raise RuntimeError("SSH 连接建立失败")

        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(8)
        self.local_port = self._server.getsockname()[1]

        self._thread = threading.Thread(target=self._accept_loop, args=(transport,), daemon=True)
        self._thread.start()
        return self.local_port

    def _accept_loop(self, transport) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._relay, args=(transport, conn, addr), daemon=True).start()

    def _relay(self, transport, conn: socket.socket, addr) -> None:
        """一条本地连接 <-> 一条 SSH 通道，双向搬运字节。"""
        try:
            channel = transport.open_channel(
                "direct-tcpip", (self.remote_host, self.remote_port), addr
            )
        except Exception:
            conn.close()
            return

        def pump(src, dst):
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception:
                pass
            finally:
                for closer in (dst, src):
                    try:
                        closer.close()
                    except Exception:
                        pass

        threading.Thread(target=pump, args=(conn, channel), daemon=True).start()
        pump(channel, conn)

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._server:
                self._server.close()
        except OSError:
            pass
        try:
            if self._client:
                self._client.close()
        except Exception:  # noqa: BLE001
            pass

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.local_port}"


_TUNNELS: dict[int, SSHTunnel] = {}


def _tunneled_url(remote_port: int = 6006) -> str:
    """复用（或新建）SSH 隧道，返回本机可连的 ws/http 基址。"""
    tunnel = _TUNNELS.get(remote_port)
    if tunnel is None or not tunnel._thread or not tunnel._thread.is_alive():
        tunnel = SSHTunnel(remote_port=remote_port)
        tunnel.start()
        _TUNNELS[remote_port] = tunnel
    return f"ws://127.0.0.1:{tunnel.local_port}"


def _tunneled_http_base(remote_port: int = 8000) -> str:
    """HTTP 服务同样只能从实例内部访问，走隧道换成本机端口。"""
    ws_like = _tunneled_url(remote_port)
    return "http://" + ws_like[len("ws://"):]


def _handshake(url: str, timeout: float = 5.0) -> dict:
    """做一次完整握手，返回 ready 消息；失败抛异常。"""
    from websockets.sync.client import connect as ws_connect

    with ws_connect(url, open_timeout=timeout) as ws:
        message = ws.recv(timeout=timeout)
    if isinstance(message, bytes):
        raise RuntimeError("服务端未返回 JSON")
    info = json.loads(message)
    if info.get("type") != "ready":
        raise RuntimeError(f"非 ready 响应：{info}")
    return info


def resolve_ws_url(remote_port: int = 6006) -> tuple[str, str]:
    """返回 (真能握手的 ws 地址, 说明)。

    直接用配置里的地址（默认 6006 的公网映射）握手；握手不通就退到 SSH 隧道。
    注意：TCP 能连上不代表 ws 能用，所以这里必须真握手，不能只看端口。
    """
    direct = ws_url()
    try:
        _handshake(direct)
        return direct, "直连"
    except Exception:  # noqa: BLE001 - 直连不可用属预期
        pass

    return _tunneled_url(remote_port), "SSH 隧道"


# ---------------------------------------------------------------- 传输层

def _ws_transcribe(path: Path, url: str, timeout: float = DEFAULT_TIMEOUT) -> TranscribeResult:
    """按协议推流转写（同步客户端，出错抛异常由上层兜住）。"""
    from websockets.sync.client import connect as ws_connect

    name = path.name
    started = time.monotonic()
    with ws_connect(url, open_timeout=10, close_timeout=5, max_size=None) as ws:
        ready = ws.recv(timeout=15)
        if isinstance(ready, bytes):
            raise RuntimeError("握手失败：服务端未返回 ready JSON")
        info = json.loads(ready)
        if info.get("type") != "ready":
            raise RuntimeError(f"握手失败：{info}")

        ws.send(json.dumps({"type": "start", "filename": name}, ensure_ascii=False))
        ack = json.loads(ws.recv(timeout=15))
        if ack.get("type") != "started":
            raise RuntimeError(f"start 未被接受：{ack}")

        sent = 0
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(CHUNK_BYTES)
                if not chunk:
                    break
                ws.send(chunk)
                sent += len(chunk)
        ws.send(json.dumps({"type": "eof"}))

        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"等待结果超时（{timeout:.0f}s）")
            message = ws.recv(timeout=left)
            if isinstance(message, bytes):
                continue
            payload = json.loads(message)
            kind = payload.get("type")
            if kind == "result":
                return TranscribeResult(
                    ok=True,
                    text=str(payload.get("text") or "").strip(),
                    sentences=payload.get("sentences") or [],
                    transport="ws",
                    audio_bytes=int(payload.get("audio_bytes") or sent),
                    inference_ms=int(payload.get("inference_ms") or 0),
                    elapsed=time.monotonic() - started,
                )
            if kind == "error":
                raise RuntimeError(f"{payload.get('code')}: {payload.get('message')}")


def _sftp_upload(path: Path) -> str:
    """上传到 AutoDL 的 remote_dir，返回远端绝对路径。失败抛异常。"""
    import paramiko

    conf = _state()
    if not conf["host"] or not conf["password"]:
        raise RuntimeError("缺少 AutoDL 连接信息（AUTODL_HOST / AUTODL_PASSWORD 或 .cache/autodl.json）")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(conf["host"], port=conf["port"], username=conf["user"],
                       password=conf["password"], timeout=20,
                       banner_timeout=20, auth_timeout=20)
        sftp = client.open_sftp()
        try:
            remote_dir = conf["remote_dir"].rstrip("/")
            # 目录可能不存在，逐级创建
            parts = remote_dir.strip("/").split("/")
            current = ""
            for part in parts:
                current += "/" + part
                try:
                    sftp.stat(current)
                except OSError:
                    sftp.mkdir(current)

            remote_path = f"{remote_dir}/{int(time.time())}_{path.name}"
            sftp.put(str(path), remote_path)
            return remote_path
        finally:
            sftp.close()
    finally:
        client.close()


# ---------------------------------------------------------------- HTTP 传输层
# 实例上还跑着一个 FastAPI 版转写服务（/root/transcribe_server.py，默认 8000 端口）：
#   GET  /health                        → 模型/设备/ffmpeg 状态
#   POST /api/transcribe         multipart file=... 或 url=...
#   POST /api/transcribe_link    {"url": "..."}     ← 服务端自己用 yt-dlp 下载
# 它比 WS 链路省一次"本机下载 + SFTP 上传"，所以默认优先用它。

_HTTP_SESSION = None


def _http_session():
    global _HTTP_SESSION
    if _HTTP_SESSION is None:
        import requests

        _HTTP_SESSION = requests.Session()
    return _HTTP_SESSION


def _http_base() -> str:
    """把 http_url 换成"本机可达"的基址：指向 127.0.0.1/localhost 时补 SSH 隧道。"""
    parsed = urlparse(http_url())
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8000
    if host in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}:
        return _tunneled_http_base(port)
    return f"{parsed.scheme or 'http'}://{host}:{port}"


def _http_headers() -> dict:
    token = api_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def http_health(timeout: float = 8.0) -> dict:
    """探测 HTTP 转写服务；连不上返回 {"ok": False, "error": ...}。"""
    try:
        base = _http_base()
        resp = _http_session().get(f"{base}/health", headers=_http_headers(), timeout=timeout)
        data = resp.json()
        data["base"] = base
        return data
    except Exception as exc:  # noqa: BLE001 - 探测不该抛
        return {"ok": False, "error": f"{exc}", "base": http_url()}


def _http_result(data: dict, started: float, transport: str) -> TranscribeResult:
    meta = data.get("meta") or {}
    segments = data.get("segments") or []
    try:
        inference = float(meta.get("elapsed_seconds") or 0)
    except (TypeError, ValueError):
        inference = 0.0
    return TranscribeResult(
        ok=True,
        text=str(data.get("text") or "").strip(),
        sentences=segments,
        transport=transport,
        inference_ms=int(inference * 1000),
        elapsed=time.monotonic() - started,
        media={
            "title": meta.get("title") or "",
            "author": meta.get("author") or "",
            "duration": meta.get("duration") or 0,
            "thumbnail": meta.get("thumbnail") or "",
            "platform": meta.get("platform") or "",
            "url": meta.get("url") or "",
            "chunks": meta.get("chunks") or 0,
            "model": meta.get("model") or "",
        },
    )


def transcribe_link(url: str, timeout: float = DEFAULT_TIMEOUT) -> TranscribeResult:
    """把链接直接丢给服务器：它自己 yt-dlp 下载 + FunASR 转写（推荐路径）。

    用户粘贴的常是整段分享文案，这里先抠出 URL，再交给服务端的 yt-dlp。
    """
    started = time.monotonic()
    try:
        from tools.media_tools import extract_url

        url = extract_url(url or "")
    except Exception:  # noqa: BLE001 - 抠不出来就原样发，服务端会报错
        pass
    try:
        base = _http_base()
        resp = _http_session().post(
            f"{base}/api/transcribe_link",
            json={"url": url},
            headers=_http_headers(),
            timeout=timeout,
        )
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - 交给上层降级
        return TranscribeResult(error=f"HTTP 转写失败：{exc}", elapsed=time.monotonic() - started)

    if not data.get("ok"):
        return TranscribeResult(
            error=str(data.get("error") or f"HTTP {resp.status_code}"),
            elapsed=time.monotonic() - started,
        )
    return _http_result(data, started, "http(link)")


def transcribe_file_http(path: str | Path, timeout: float = DEFAULT_TIMEOUT) -> TranscribeResult:
    """上传本地文件给 HTTP 服务转写（multipart）。"""
    started = time.monotonic()
    file = Path(path)
    if not file.exists():
        return TranscribeResult(error=f"文件不存在：{file}")
    try:
        base = _http_base()
        with file.open("rb") as handle:
            resp = _http_session().post(
                f"{base}/api/transcribe",
                files={"file": (file.name, handle, "application/octet-stream")},
                headers=_http_headers(),
                timeout=timeout,
            )
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return TranscribeResult(error=f"HTTP 转写失败：{exc}", elapsed=time.monotonic() - started)

    if not data.get("ok"):
        return TranscribeResult(
            error=str(data.get("error") or f"HTTP {resp.status_code}"),
            elapsed=time.monotonic() - started,
        )
    result = _http_result(data, started, "http(upload)")
    result.audio_bytes = file.stat().st_size
    return result


# ---------------------------------------------------------------- 对外接口

def probe() -> dict:
    """探测转写服务与实例信息，页面「环境状态」用。"""
    result = {"ws_url": ws_url(), "asr_mode": asr_mode(), "ws_ok": False,
              "device": "", "protocol": "", "error": "",
              "http_url": http_url(), "http_ok": False, "model": "", "media": ""}
    if asr_mode() == "http":
        health = http_health()
        result.update(http_ok=bool(health.get("ok")),
                      http_url=health.get("base") or http_url(),
                      model=str(health.get("model") or ""),
                      device=str(health.get("device") or ""),
                      protocol="http",
                      ws_ok=bool(health.get("ok")),
                      error="" if health.get("ok") else str(health.get("error") or "健康检查失败"))
        result["media"] = "yt-dlp" if health.get("yt_dlp") else ""
        return result

    parsed = urlparse(result["ws_url"])
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 6006
    sock = socket.socket()
    sock.settimeout(PROBE_TIMEOUT)
    try:
        sock.connect((host, port))
    except OSError as exc:
        result["error"] = f"连不上 {host}:{port}（{exc}）"
        return result
    finally:
        sock.close()

    try:
        from websockets.sync.client import connect as ws_connect
        with ws_connect(result["ws_url"], open_timeout=PROBE_TIMEOUT) as ws:
            info = json.loads(ws.recv(timeout=PROBE_TIMEOUT))
        result["ws_ok"] = info.get("type") == "ready"
        result["via"] = "直连"
        result["device"] = str(info.get("device") or "")
        result["protocol"] = str(info.get("protocol") or "")
    except Exception as exc:  # noqa: BLE001 - 探测不该抛
        result["error"] = f"直连握手失败：{exc}，尝试 SSH 隧道…"

    if not result["ws_ok"]:
        try:
            url = _tunneled_url()
            from websockets.sync.client import connect as ws_connect
            with ws_connect(url, open_timeout=PROBE_TIMEOUT) as ws:
                info = json.loads(ws.recv(timeout=PROBE_TIMEOUT))
            result.update(ws_ok=info.get("type") == "ready",
                          ws_url=url, via="SSH 隧道",
                          device=str(info.get("device") or ""),
                          protocol=str(info.get("protocol") or ""),
                          error="")
        except Exception as exc:  # noqa: BLE001
            result["error"] += f" 隧道也失败：{exc}"
    return result


def transcribe_file(path: str | Path, upload: bool = True,
                    timeout: float = DEFAULT_TIMEOUT) -> TranscribeResult:
    """转写本地音频/视频文件。

    Args:
        path: 本地文件路径
        upload: 是否先 SFTP 上传到 AutoDL（留档，便于事后排查）
        timeout: 等待推理结果的超时秒数
    """
    started = time.monotonic()
    file = Path(path)
    if not file.exists():
        return TranscribeResult(error=f"文件不存在：{file}")
    if file.suffix.lower() not in ALLOWED_SUFFIXES:
        allowed = " ".join(sorted(ALLOWED_SUFFIXES))
        return TranscribeResult(error=f"不支持的格式 {file.suffix}，服务端支持：{allowed}")

    # 优先 HTTP 上传转写（省一次 SFTP 往返，服务端直接吃文件）
    if asr_mode() == "http":
        http_result = transcribe_file_http(file, timeout=timeout)
        if http_result.ok:
            http_result.elapsed = time.monotonic() - started
            return http_result
        upload_error = f"HTTP 后端失败（{http_result.error}），改用 WS："
    else:
        upload_error = ""
    remote_path = ""
    if upload:
        try:
            remote_path = _sftp_upload(file)
        except Exception as exc:  # noqa: BLE001 - 上传失败不阻断转写
            upload_error = f"上传失败（仍尝试直传）：{exc}"

    try:
        url, via = resolve_ws_url()
        result = _ws_transcribe(file, url, timeout=timeout)
        result.transport = f"ws({via})"
    except Exception as exc:  # noqa: BLE001 - 交给上层降级
        return TranscribeResult(
            remote_path=remote_path,
            elapsed=time.monotonic() - started,
            error=f"{exc}" + (f"；{upload_error}" if upload_error else ""),
        )

    result.remote_path = remote_path
    result.audio_bytes = result.audio_bytes or file.stat().st_size
    result.elapsed = time.monotonic() - started
    return result


def transcribe_url(url: str, upload: bool = True, timeout: float = DEFAULT_TIMEOUT) -> TranscribeResult:
    """转写视频链接。

    优先把链接直接交给服务器（它自己 yt-dlp 下载，本机不落盘）；
    HTTP 后端不可用时才退回"本机下载 + 上传/推流"。
    """
    started = time.monotonic()
    if asr_mode() == "http":
        result = transcribe_link(url, timeout=timeout)
        if not result.ok:
            # 隧道可能刚好断了：清掉缓存重建一次再试，避免白白退化成"下载+上传"
            _TUNNELS.clear()
            result = transcribe_link(url, timeout=timeout)
        if result.ok:
            result.elapsed = time.monotonic() - started
            return result
        link_error = result.error
        print(f"[AutoDL/ASR] 链接直传失败，改用本机下载：{link_error}")
    else:
        link_error = ""

    from tools.media_tools import download_video

    path = download_video(url)
    if not path or not os.path.exists(path):
        return TranscribeResult(
            error=f"下载失败：{url}" + (f"（HTTP 后端：{link_error}）" if link_error else ""),
            elapsed=time.monotonic() - started,
        )

    result = transcribe_file(path, upload=upload, timeout=timeout)
    result.elapsed = time.monotonic() - started
    return result


def transcribe(source: str, upload: bool = True, timeout: float = DEFAULT_TIMEOUT) -> TranscribeResult:
    """统一入口：URL 走下载，本地路径直接转。"""
    if str(source).startswith(("http://", "https://")):
        return transcribe_url(str(source), upload=upload, timeout=timeout)
    return transcribe_file(source, upload=upload, timeout=timeout)


# ---------------------------------------------------------------- 命令行

def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AutoDL 远程 FunASR 转写")
    parser.add_argument("source", nargs="?", help="本地音视频路径或视频链接")
    parser.add_argument("--download", action="store_true", help="source 是链接时先下载（默认自动判断）")
    parser.add_argument("--no-upload", action="store_true", help="跳过 SFTP 上传，直接推流")
    parser.add_argument("--upload-only", action="store_true", help="只上传，不转写")
    parser.add_argument("--probe", action="store_true", help="只探测服务状态")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--save-config", nargs=4,
                        metavar=("HOST", "PORT", "PASSWORD", "WS_URL"),
                        help="写入 .cache/autodl.json 后退出")
    args = parser.parse_args(argv)

    if args.save_config:
        host, port, password, ws = args.save_config
        target = save_credentials(host=host, port=int(port), password=password, ws=ws)
        print(f"已写入 {target}")
        return 0

    if args.probe or not args.source:
        info = probe()
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0 if info["ws_ok"] else 2

    if args.upload_only:
        source = Path(args.source)
        if not source.exists():
            raise SystemExit(f"文件不存在：{source}")
        remote = _sftp_upload(source)
        size_mb = source.stat().st_size / 1024 / 1024
        print(f"已上传 {source.name}（{size_mb:.1f}MB） → {remote}")
        return 0

    result = transcribe(args.source, upload=not args.no_upload, timeout=args.timeout)
    print(result.brief)
    if result.remote_path:
        print(f"远端留档：{result.remote_path}")
    if result.text:
        print("\n" + "=" * 60 + "\n" + result.text[:2000])
    if not result.ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())
