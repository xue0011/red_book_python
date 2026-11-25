# 代码文件地图（CODE MAP）

> 更新：2026-10-29 17:20 · 对应代码：v1（框架 + 账号定位 + 热点监控 + 内容复刻/AutoDL 转写）
> 扫描口径：从入口 `frontend/app.py` 与 7 个页面出发解析 `import`（含 `from pkg import name` 的文件级解析），
> 没有任何"活文件"引用到的即为孤儿。复检：`uv run python .cache/dev/code_map.py`

**结论**：Python 文件 **27 个**，在用 **22 个**，未被引用 **5 个**（4 个是空 `__init__.py`，真正可删的只剩 `tools/bocha_client.py`）。

---

## 1. 目录树与职责

标记：✅ 在用 · 🚪 入口 · ⚠️ 未被引用 · 📦 包标记（建议保留）

```
media_agent/
├── 🚪 frontend/app.py                     60 行  Streamlit 唯一入口：st.navigation（首页 default=True）+ 侧栏模型/数据库状态
├── config.py                             147 行  ✅ Pydantic Settings：LLM / 目录 / 博查 / AutoDL / HeyGem / PG
├── frontend/
│   ├── components/ui.py                   83 行  ✅ 侧栏页脚等小工具（page_header/hero_card/chips/bullets/card_grid 暂未调用）
│   └── views/
│       ├── 01_首页.py                     107 行  ✅ 启动页：六张可点击模块卡 + 三阶段表 + 快速开始 + 操作记录（原 home.py 已合并进来）
│       ├── 02_账号定位.py                   80 行  ✅ 输入 → run_positioning() → 三张卡 + 写库
│       ├── 03_热点监控.py                  138 行  ✅ 平台/赛道 → run_hot_topic() → 热榜表 + 筛选/选题卡
│       ├── 04_内容复刻.py                  173 行  ✅ 链接或本地文件 → run_replicate() / run_replicate_local()
│       ├── 05_口播视频.py                  373 行  ✅ 提词器 + 数字人（调 tools/heygem_client.py）
│       ├── 06_视频剪辑.py                  331 行  ✅ 口播（上传/链接/路径）+ 素材 + BGM + 文案 → deepagent（读 skill）或 render_mashup()
│       └── 07_数据复盘.py                  486 行  ✅ 抖音数据 → review_graph.invoke()
├── workflow/
│   ├── llm.py                            135 行  ✅ LLM 统一入口（get_model / llm_call / call_json / safe_llm_call）
│   ├── positioning.py                    120 行  ✅ 账号定位图：画像 → 对标 → 方案（3 节点，跑完写库）
│   ├── hot_topic.py                      217 行  ✅ 热点图：5 平台 Send 并行 → 筛选 → 选题
│   ├── replicate.py                      135 行  ✅ 内容复刻图：提取 → 拆解 → 仿写 → 标题（+ 本地文件入口）
│   ├── video.py                          191 行  ✅ 口播视频图：文案 → 优化 → TTS → 数字人
│   ├── mashup.py                         559 行  ✅ 视频剪辑 deepagent：把 .skills/ 同步到 .cache/skills 后按 skill 执行（页面默认）
│   ├── mashup_local.py                   436 行  ✅ 视频剪辑本地渲染：素材卡（上下交替/光晕）→ 5:20 手机屏 → 文案 → BGM
│   └── review.py                         279 行  ✅ 数据复盘图：采集 → 漏斗 → 质量 → 策略
├── tools/
│   ├── media_tools.py                    715 行  ✅ 下载 / 抽音 / 转写 / TTS / 配图 / 文章抓取（远程 ASR 优先）
│   ├── autodl_funasr.py                  530 行  ✅ AutoDL 转写：SFTP 上传 + WS 推流 + SSH 隧道兜底
│   ├── heygem_client.py                  634 行  ✅ HeyGem 数字人（克隆 / TTS / 合成）
│   ├── trend_radar_client.py             326 行  ✅ 热榜客户端（TrendRadar / NewsNow，含按位次估算热度）
│   └── bocha_client.py                   278 行  ⚠️ 博查联网检索（竞品分析改纯推演后闲置）
├── db/session.py                         203 行  ✅ PG 连接 + positioning_records / hot_topic_records 读写（连不上自动降级）
├── frontend/__init__.py · tools/__init__.py · workflow/__init__.py · frontend/components/__init__.py   📦
└── docs/  PRD.md · SPEC_v1.md · TECH_STACK.md · CODE_MAP.md · images/product_overview.png
```

---

## 2. 未被引用的文件

| 文件 | 行数 | 说明 | 建议 |
|---|---|---|---|
| `tools/bocha_client.py` | 278 | 博查 web-search 客户端：`search()` / `search_many()` / 证据去重 + 垃圾站过滤 / `build_queries()` | **可删**；若以后想给「竞品分析」接真实联网检索，这是现成实现，留着也不碍事 |
| `frontend/__init__.py` | 0 | 包标记 | **留** |
| `tools/__init__.py` | 0 | 包标记 | **留** |
| `workflow/__init__.py` | 0 | 包标记 | **留** |
| `frontend/components/__init__.py` | 1 | 包标记 | **留** |

> 之前扫出来的 9 个孤儿（`backend/services/*`、`workflow/hotspot.py`、`workflow/schemas.py`、`tools/hotlist_client.py`、`frontend/components/cards.py`、`db/schema.sql`）**已被删除**，本次扫描不再出现。

---

## 3. 文件在用、但某些函数没人调（别急着删）

| 定义 | 位置 | 说明 |
|---|---|---|
| `run_mashup()` | `workflow/mashup.py:541` | 页面直接 `mashup_graph.invoke()`，这只是便捷封装 |
| `run_review()` | `workflow/review.py:267` | 同上，页面用 `review_graph.invoke()` |
| `heygem_generate_video()` | `tools/heygem_client.py:473` | 05 页按步骤调 clone / tts / synthesize，没用一键封装 |
| `latest_hot_topic()` | `db/session.py:181` | 03 页只读最近一条定位，暂时没用它 |
| `generate_image()` | `tools/media_tools.py:63` | 配图生成，05/06 还没接 |
| `fetch_article()` | `tools/media_tools.py:673` | 文章抓取（trafilatura），复刻页目前只做视频 |
| `page_header` / `hero_card` / `chips` / `bullets` / `card_grid` | `frontend/components/ui.py` | 早期 UI 组件，页面自己写了 markdown；只剩 `sidebar_footer` 在用 |

---

## 4. 非 Python 资产

| 文件 | 大小 | 用途 |
|---|---|---|
| `README.md` | 6.8 KB | 快速开始 / 目录树 / 设计约定 / AutoDL 章节 |
| `docs/PRD.md` | 34 KB | 产品需求（六模块 + v1/v2/v3 验收） |
| `docs/SPEC_v1.md` | 40 KB | v1 施工图：数据契约、节点、时序图、验收 |
| `docs/TECH_STACK.md` | 13.5 KB | 技术栈与节点/内存/MCP 说明 |
| `docs/CODE_MAP.md` | 本文件 | 文件职责 + 引用关系 + 可删清单 |
| `docs/images/product_overview.png` | 178 KB | 产品全景图（PRD 内嵌） |
| `pyproject.toml` | 0.4 KB | uv 依赖 |
| `uv.lock` | 958 KB | 锁文件，**必须留** |
| `.env` / `.env.example` | 0.3 / 2.7 KB | 配置（`.env` 已 gitignore） |

---

## 5. 运行缺口（不是死代码，但功能会提示"缺失"）

| 缺口 | 影响 | 现状 |
|---|---|---|
| `backend/lib/douyin.py`（erma0/douyin 采集模块） | **数据复盘**拿不到真实作品数据，页面提示"采集模块缺失" | 文件与 `backend/` 目录都不存在；`workflow/review.py:59` 的 `from backend.lib.douyin import Douyin` 被 `except ImportError` 兜住，不崩 |
| `niquests` 包 | 同上（`/user/self` 取 sec_uid 用） | 未安装，不在 `pyproject.toml` |
| `DOUYIN_PROJECT_DIR` | 复盘读 Cookie 的 `config/settings.json` | 未配置，退回 `~/Documents/project/douyin` |
| `DEFAULT_BGM_PATH` | 剪辑页默认 BGM | 未配置，需手动上传 |
| 本机 ffmpeg | 剪辑合成 / 音频抽取 | 不在 PATH；转写已走 AutoDL（服务器有 ffmpeg），moviepy 自带 imageio-ffmpeg 可兜底 |
| 本机 funasr | 远程失败时的兜底 | 包已装，但没有模型权重与 GPU；`ASR_MODE=remote` 时不触发 |

---

## 6. 还能删的

1. `tools/bocha_client.py`（278 行）—— 注意：删了以后要恢复"竞品真实检索"得重写。
2. 其余不必动。

删完复检：

```powershell
uv run python .cache/dev/wiring_check.py      # 期望：编译/导入/渲染全 PASS
uv run python .cache/dev/render_check.py      # 期望：7 个页面全部渲染通过
uv run streamlit run frontend/app.py          # 打开默认落在「首页」
```
