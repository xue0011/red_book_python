# 自媒体运营 AI Agent

把「账号定位 → 热点监控 → 内容复刻 → 口播视频 → 视频剪辑 → 数据复盘」做成本地可运行的
Streamlit + LangGraph 应用。所有数据留在本机，不依赖任何 SaaS 后台。

## 快速开始

```bash
# 1. 安装依赖（Python 3.12，uv 管理）
uv sync

# 2. 配置密钥
cp .env.example .env        # Windows: copy .env.example .env
# 打开 .env，填入 DEEPSEEK_API_KEY=sk-...
# 可选：BOCHA_API_KEY（账号定位的竞品分析联网检索）、DATABASE_URL（PG 存储）

# 3. 启动（入口在前端层内，命令在项目根目录执行）
uv run streamlit run frontend/app.py
```

浏览器打开 http://localhost:8501，左侧先选「账号定位」，再选「热点监控」。

> 冒烟自检（不调用大模型）：`uv run python -c "import config, workflow.positioning, workflow.hot_topic, db.session; print(db.session.health())"`
> 更细的自检脚本放在本地 `.cache/dev/`（不入库）：`page_check2.py`（两页渲染/卡片/跨页上下文）、`render_check.py`（7 页）、`smoke_check.py`。

## 当前进度

| 版本 | 内容 | 状态 |
|---|---|---|
| **v1** | 工程骨架 + 账号定位 + 热点监控 | 🟡 待验收 |
| v2 | 内容复刻 / 口播视频 + 全模块串通 | ⚪ 未开始 |
| v3 | 视频剪辑 / 数据复盘 + 体验优化 | ⚪ 未开始 |

每个版本完成后由你验收，再 commit 并打 tag `v1` / `v2` / `v3`。

## 目录结构

```
media_agent/
├── config.py                  # Pydantic Settings，读 .env
├── frontend/                  # 前端层（Streamlit）
│   ├── app.py                 # 入口（st.navigation + 侧边栏），默认页=首页
│   ├── views/                 # 01 首页（启动页，含六模块入口）… 07 数据复盘
│   └── components/            # ui.py（侧栏页脚等小工具）
├── backend/                   # 后端层
│   └── lib/                   # 预留：抖音采集模块（放 douyin 项目，复盘用）
├── db/
│   └── session.py             # positioning_records / hot_topic_records / replicate_records 读写（连不上自动降级为不落库）
├── workflow/
│   ├── llm.py                 # get_model / safe_llm_call / call_json / llm_call（文本接口）
│   ├── positioning.py         # 账号定位：3 节点（画像分析 → 对标搜索 → 方案生成），文本进出，跑完入库
│   ├── hot_topic.py           # 热点监控：5 个抓取节点并行（Send）+ 赛道筛选 + 选题建议
│   └── replicate.py           # 内容复刻：下载 → 转写（AutoDL FunASR）→ 拆解 → 仿写 → 标题
│   └── mashup_local.py        # 视频剪辑：素材卡上下交替 + 5:20 手机屏 + 文案 + BGM（moviepy 确定性渲染）
│   └── mashup.py              # 视频剪辑（deepagent 智能体版：读 .skills/video-use 自己写脚本，页面默认走这条）
├── .skills/video-use/         # 视频剪辑 skill：SKILL.md（四条硬约束）+ templates/mashup_template.py（可用模板）
├── tools/
│   ├── trend_radar_client.py  # TrendRadar / NewsNow 热榜抓取（抖音/微博/知乎/小红书/B站）
│   ├── autodl_funasr.py       # AutoDL 远程转写：SFTP 上传 + WS 推流 + SSH 隧道兜底
│   └── media_tools.py         # 下载 / 抽音 / 转写 / TTS / 配图
├── docs/PRD.md                # 产品需求文档
├── docs/SPEC_v1.md            # v1 施工图（数据契约 / 时序图 / 页面线框 / 验收）
├── docs/TECH_STACK.md         # 技术栈说明（逐模块 + MCP 接入设计）
├── docs/CODE_MAP.md           # 代码文件地图：谁被引用、哪些是孤儿文件、可删清单
└── docs/images/               # PRD 里的产品全景图
```

> 早期结构化实现（`workflow/schemas.py`、`workflow/hotspot.py`、`tools/hotlist_client.py`、`backend/services/*`、`frontend/components/cards.py`、`db/schema.sql`）**已删除**；
> 唯一还留着未引用的是 `tools/bocha_client.py`（博查检索，想要"竞品真实联网"时可直接接）。详见 [代码地图](docs/CODE_MAP.md)。

## 设计约定

- **配置**：全部走 `config.Settings`，字段可被环境变量覆盖；缺 Key 不崩，页面提示。
- **LLM**：统一入口 `workflow/llm.py`；结构化输出用 `call_json`（JSON mode + Pydantic 校验 + 1 次自修复重试）。
- **状态**：页面状态放 `st.session_state`，前缀 `pos_` / `hot_` / `rep_` / `talk_` / `edit_` / `rev_`。
- **缓存**：`./.cache/` 下按 `{模块}_{时间戳}_{slug}.{ext}` 命名，已进 `.gitignore`。
- **采集规模**：`COLLECT_FULL_TEXT=false`（默认）只留标题/链接/摘要 160 字，省 token 省库；改 `true` 才抓正文与 `og:image`。
- **存储**：全部走 `db/session.py`（PG `positioning_records` / `hot_topic_records` / `replicate_records`）；连不上自动降级为"不落库"，不阻塞页面。
- **分层**：页面只做渲染与交互（`frontend/views/`），业务逻辑进 `workflow/`（LangGraph 图），副作用（下载/转写/数字人/热榜）进 `tools/`。
- **为什么是 `views/` 不是 `pages/`**：Streamlit 只要在入口同级看到 `pages/` 目录，就会走**旧版 MPA 分支**（`PagesManager.uses_pages_directory=True` → `_mpa_v1`），**直接跳过 `app.py`**，于是 `sys.path` 里没有项目根、页面 `import config` 全部 `ModuleNotFoundError`，`set_page_config` 与侧边栏也不生效。改名成 `views/` 后走 `st.navigation` 正常分支。

## AutoDL 远程转写（内容复刻的语音转文字）

本机没 GPU 时，转写交给 AutoDL 的 4090。现在有两条链路，**默认走 HTTP(link)**：把链接丢给服务器，服务器自己 yt-dlp 下载再识别，本机不落盘、不占带宽。

```
链接 ──POST /api/transcribe_link──▶ 服务器 yt-dlp 下载 ──▶ FunASR ──▶ {"text","segments","meta"}
本地文件 ──POST /api/transcribe(multipart)──▶ 同上
（HTTP 不可用时）──SFTP 上传 + WS 推流 (:6006)──▶ 旧链路
```

实例上的服务 `/root/transcribe_server.py`（FastAPI，默认 8000，Token 鉴权，只在实例内部监听，本机通过 SSH 隧道访问）：

| 接口 | 说明 |
|---|---|
| `GET /health` | 模型 / 设备 / ffmpeg / yt_dlp 状态 |
| `GET /api/info` | 服务信息与端点清单 |
| `POST /api/transcribe` | multipart：`file` 或 `url` 二选一 |
| `POST /api/transcribe_link` | JSON `{"url": "..."}`，服务端自己下载 |
| `GET /api/transcripts` | 服务端累计转写文本 |

配置（`.env`）与密钥（`.cache/autodl.json`）分开：

```bash
# .env：非敏感项
ASR_MODE=http                          # http=转写服务（推荐）｜ws=旧推流｜local=本机 funasr
ASR_HTTP_URL=http://127.0.0.1:8000     # 实例内部地址；本机会自动建 SSH 隧道
AUTODL_HOST=connect.nmb1.seetacloud.com
AUTODL_PORT=17653
AUTODL_USER=root
# 旧 WS 链路（仅 ASR_MODE=ws 时用）
FUNASR_WS_URL=ws://192.168.4.9:6006

# Token / 密码只写在这里（.cache/ 已 gitignore）
# .cache/autodl.json
{ "password": "...", "api_token": "...", "http_url": "http://127.0.0.1:8000" }
```

命令行用法：

```bash
uv run python -m tools.autodl_funasr --probe                              # 探测（http 模式看 http_ok / model）
uv run python -m tools.autodl_funasr "https://www.bilibili.com/video/BV..."  # 贴链接转写（服务端下载）
uv run python -m tools.autodl_funasr .cache/demo.mp3                      # 上传本地文件转写
uv run python -m tools.autodl_funasr .cache/demo.mp4 --upload-only        # 只传文件到 /root/autodl-tmp/asr_in
```

Python 侧：

```python
from tools.autodl_funasr import transcribe, probe

probe()                       # {"asr_mode": "http", "http_ok": True, "model": "paraformer-zh", ...}
r = transcribe("https://www.bilibili.com/video/BV1cxWPzxEDD/")
r.text, r.sentences, r.media  # 文本 / 时间轴 / 标题·作者·时长·平台
```

页面上也能直接传：内容复刻页除了「📎 视频链接」，还有「或直接上传本地视频/音频」，
文件先落 `.cache/videos/uploads/`，再由 `run_replicate_local()` 交给同一个服务转写。

> 服务端支持 mp3/wav/m4a/mp4/mov/mkv 等，单文件上限 500MB、时长上限 600 秒（超长自动切块），
> 实例盘 `/root/autodl-tmp` 约 50G。HTTP 转的是"临时文件"，转完即删；上传留档走旧 SFTP 路径。

转写失败不会中断流程：`transcribe_url()` → 本机下载 → `media_tools.audio_to_text()` → 最终退回本机 FunASR。

## 文档

- [PRD](docs/PRD.md) — 需求、范围、验收
- [SPEC v1](docs/SPEC_v1.md) — 数据契约、节点提示词、时序图、页面线框、验收清单
- [技术栈](docs/TECH_STACK.md) — 六模块技术栈总表、账号定位五节点拆解、MCP 联网搜索设计
- [代码地图](docs/CODE_MAP.md) — 每个文件的职责、引用关系、**未被引用的文件清单（可直接删）**、运行缺口
