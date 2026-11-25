# 技术栈说明（以「账号定位」为样板，逐模块对齐）

> 这份文档回答一个问题：**这些工作流到底是用什么跑起来的**。
> 写法是"先讲透第一个模块（账号定位）的每一个节点，再把其它五个模块映射到同一条流水线上"。
> 对应代码：`workflow/positioning.py`（图）、`workflow/llm.py`（模型调用）、`db/session.py`（落库）、`frontend/pages/02_账号定位.py`（页面）。
> **注**：文中提到的 `workflow/schemas.py`（Pydantic 契约）与 `backend/services/*` 属早期设计，已于 2026-10-29 删除；当前定位链路是纯文本进出，见 [CODE_MAP](CODE_MAP.md)。

---

## 0. 一句话

> **Streamlit 负责"看得见"，LangGraph 负责"跑得对"，DeepSeek 负责"想得出"，MCP/外部工具负责"查得到"，内存 State 负责"传得下去"。**

| 层 | 选型 | 在项目里的位置 |
|---|---|---|
| 表现层 | Streamlit ≥1.66（`st.navigation` 多页） | `frontend/app.py` + `frontend/pages/*` |
| 编排层 | **LangGraph** 1.2（`StateGraph`） | `workflow/positioning.py` |
| 模型层 | DeepSeek（OpenAI 兼容协议，`langchain-openai`） | `workflow/llm.py` |
| 工具层 | MCP 客户端 / 各平台 API / 本地 CLI | `tools/*`（v1 只有 `__init__.py`） |
| 数据层 | Pydantic v2 契约 + 内存 State + 可选落盘 | `workflow/schemas.py`、`backend/services/*` |
| 包管理 | **uv** | `pyproject.toml` / `uv.lock` / `.venv` |

---

## 1. 六个模块共用的同一套骨架

每个模块都是 `页面表单 → LangGraph 图 → 结构化结果 → 页面渲染 → 可选导出`，差别只在**图的形状**和**用什么工具**：

| 模块 | 图的形状 | 生成模型 | 外部工具 | 中间产物 |
|---|---|---|---|---|
| ② 账号定位 | 5 节点**串行** | DeepSeek ×3 次 | v1 无 / v2 MCP 联网 | 内存 |
| ③ 热点监控 | 1 → **并行 N** → 合并 → 筛选 | DeepSeek ×2 次 | NewsNow API（TrendRadar 协议） | 内存 + 可选 csv |
| ④ 内容复刻 | 串行 5 节点 | DeepSeek ×3 次 | yt-dlp、FunASR | `.cache/videos`、`.cache/audio` |
| ⑤ 口播视频 | 页面交互 + 长任务轮询 | DeepSeek（文案） + Edge-TTS 兜底 | HeyGem Docker 三容器（TTS/克隆/口型） | `.cache/videos` |
| ⑥ 视频剪辑 | **DeepAgents** 子代理 + 技能包 | DeepSeek | ffmpeg/ffprobe、moviepy、HyperFrames(npx) | `.cache/videos`、`.cache/subs` |
| ⑦ 数据复盘 | 串行 4 节点 | DeepSeek ×2 次 | douyin 本地爬虫 | 内存 + 报告 md |

**为什么热点监控是唯一"并行"的**：多平台抓取互不依赖，用 LangGraph 的 `send` 把 N 个平台分发出去，再用 `Annotated[list, operator.add]` 让返回值自动合并 —— 串行抓三个平台要 15 秒，并行只要 5 秒。其余模块步骤之间有强依赖（画像没出来就没法找对标），串行反而更稳、更好定位失败点。

---

## 2. 第一个模块：账号定位的五节点拆解

你提到"五个节点：开始输入口 → 分析用户画像 → 搜索对标账号 → 生成定位方案 → 结束输出"。
代码里就是这么落的，只是**把"开始输入口"和"结束输出"做成了真节点**，而不是只靠图的 `START`/`END` 边界：

```
START
  → ① prepare_input     校验输入 / 拼提示词快照            （不调模型）
  → ② analyze_profile   分析画像                          （DeepSeek 0.4 / 2000）
  → ③ find_benchmarks   搜索对标账号                       （DeepSeek 0.6 / 2500 · v2 加 MCP）
  → ④ build_plan        生成定位方案                       （DeepSeek 0.7 / 4000）
  → ⑤ format_output     归一化 + 组装给页面/导出的结构       （不调模型）
  → END
```

> `START` / `END` 是 `StateGraph` 的内置边界（只会"把入口状态塞进去 / 收尾返回"），
> **不是可执行节点**，所以"五个节点"要落在中间这五个上，图才刚好是 5 个可执行节点。

### 2.1 输入口：表单 → `PositioningInput`（Pydantic）

页面 3 项必填（职业、核心技能、目标平台）+ 选填（行业、目标人群、兴趣资源、风格、补充、目标、每周时长、是否出镜、粉丝数、变现诉求），点按钮时构造：

```python
PositioningInput(occupation=..., skills=[...], platforms=["抖音", "小红书"], ...)
```

Pydantic 做三件事：类型校验（平台只能是 `抖音/小红书/B站/视频号`）、默认值补全、`brief()` 生成给模型看的自然语言快照。**校验失败在进入图之前就报错**，不浪费模型调用。

### 2.2 ① `prepare_input`（不调模型，毫秒级）

- 入：`state["input"]`（dict）
- 做：`model_validate` 规整 → 生成 `brief` → 检查哪些选填项空着（没填目标人群 / 没填技能 / 没填兴趣）
- 出：`input`（规整后）、`brief`、`warnings`
- 为什么单独成节点：**模型节点只负责生成**。把"补默认值、拼提示词、记警告"从模型节点里剥出去，模型调用失败时这些逻辑不会白跑，也方便单测。

### 2.3 ② `analyze_profile`（DeepSeek，temperature 0.4，max_tokens 2000）

- 入：`brief`
- 提示词：`SYSTEM_ANALYZE` 要求输出**四个数组 + 一个钩子**，分别是 `strengths`（能力优势）、`transferable`（可迁移：`<能力> → 能帮 <谁> 解决 <什么>`）、`themes`（能连讲 3 个月的内容母题）、`risks`（赛道拥挤/合规红线/能力缺口）、`hook`（"凭什么听你讲"）
- 出：`UserProfile` → `state["profile"]`
- 温度低（0.4）：这一步要的是**准确归纳**，不是文案创意。

### 2.4 ③ `find_benchmarks`（DeepSeek，0.6，2500 · 这里接 MCP）

- 入：`brief` + `profile`
- v1 现状：模型直接推演 3–5 个对标账号，`verified=false`，页面标注"参考类型，未实时核验"
- v2 目标（你要的"外部 MCP 工具联网搜索"）：见第 4 节
- 出：`BenchmarkSet` → `state["benchmarks"]`

### 2.5 ④ `build_plan`（DeepSeek，0.7，4000）

- 入：`brief` + `profile` + `benchmarks`
- 提示词 `SYSTEM_PLAN` 有 8 条硬约束：一句话定位 ≤30 字、支柱占比合计 100、示例选题必须是"能直接开拍的句子"、人设 3 个候选名 ≤8 字、变现分三阶段、30 天恰好 4 周且指标带及格线、必须尊重"行业/目标人群/风格偏好"
- 出：`PositioningPlan`（原始）→ `state["plan"]`。**注意这里不做归一化**，归一化留给 ⑤，因为"模型跑偏"不该重跑一次贵的调用。

### 2.6 ⑤ `format_output`（不调模型）

- 做四件事：占比用**最大余数法**归一成合计 100；`plan_30d` 截断到 4 条并按 1–4 重编号；`PositioningPlan` 二次校验；算 `summary`（支柱数、对标数、周数、是否已核验）
- 出：`plan`（归一化）+ `summary`
- 这一步是"把模型的『差不多』修成『严格对』"，页面渲染永远拿不到非法数据。

### 2.7 结束输出：`run_positioning()` 一次性返回

```python
result = run_positioning(payload, on_step=callback)
# → {input, brief, warnings, profile, benchmarks, plan, summary, timings}
```

页面拿到后：`st.session_state["pos_result"] = result` → 渲染运行徽章（第几次、状态、耗时、模型名）+ 4 个 Tab（🧬四维分析 / 🔍对标账号 / 🗺完整方案 / 📅30天计划）→ 侧栏可导出 `.cache/reports/pos_<时间戳>_<slug>.md`。

`on_step` 回调是**图 → 页面的单向下水道**：节点开始时喊一声，页面把 `st.status` 的进度条往前推（①校验输入 → ②分析画像 → …），用户不会对着转圈干等。

---

## 3. 数据只在内存里怎么传？（这正是 `StateGraph` 的用处）

```python
class PositionState(TypedDict, total=False):
    input: dict; brief: str; warnings: list[str]
    profile: dict; benchmarks: dict; plan: dict; summary: dict
    timings: dict[str, float]
```

- **一个 dict 贯穿全程**：每个节点只 `return` 自己那几把 key，LangGraph 负责合并（同名 key 覆盖，其它保留）。所以 ② 拿得到 `brief`，③ 拿得到 `profile`，④ 拿得到前两步的产物。
- **`total=False`**：允许任何节点只写一部分，不用一开始就凑齐所有字段。
- **不落库、不落盘**：`invoke()` 结束整个 state 就还给函数；页面把结果放 `session_state` 里（刷新不丢、关页面就没）。只有用户点"导出"时才写 `.cache/reports/*.md`。
- **失败怎么定位**：每个节点包一层 `_guard`，任何异常都转成 `NodeError(node, cause)` → 页面显示"第 3 步（检索对标）失败：…"，已完成的 ①② 结果仍在 state 里，可以直接重跑整条链路或改输入再来。

---

## 4. 外部 MCP 联网搜索（③ 号节点的 v2 形态）

现状差距：③ 号节点现在是**纯模型推演**，账号名、粉丝量级都是模型编的（虽然提示词禁止它编精确数字）。你要的"MCP 联网搜索"正是补这个洞。

### 4.1 设计

```
brief + profile
   │
   ├─(a) 生成 3~4 条检索 query（f-string 拼，不花模型调用）
   │      "抖音 急诊科护士 科普 账号"
   │      "小红书 新手父母 育儿 医生 博主"
   │      "抖音 医疗健康 赛道 头部账号 涨粉"
   │
   ├─(b) MCP 客户端连一个"搜索类 MCP server"（stdio 或 http），逐个 query 调 search 工具
   │      → 每条 query 取前 3 条：title / url / snippet
   │
   ├─(c) 把 snippets 作为"证据"塞进 SYSTEM_BENCH 的 user 消息
   │      指令：只从证据里认账号，证据不足就说不足，不许补细节
   │
   └─(d) 输出 BenchmarkSet，verified=true，每个账号带 sources: [url, ...]
```

### 4.2 代码接口（`tools/mcp_search.py`）

```python
@dataclass
class SearchHit:
    title: str; url: str; snippet: str

def search(queries: list[str], top_k: int = 3, timeout: float = 20) -> list[SearchHit]:
    """同步封装 MCP 调用（内部 asyncio.run），失败返回 []，绝不抛给节点。"""

def build_queries(brief: str, profile: dict) -> list[str]:
    """从职业/行业/平台拼检索词，纯字符串，不调模型。"""
```

节点里只加 3 行：

```python
hits = search(build_queries(state["brief"], state["profile"]))
verified = bool(hits)
benchmarks = call_json(model, SYSTEM_BENCH, USER_BENCH.format(..., evidence=format_hits(hits)), BenchmarkSet)
```

### 4.3 配置（`.env`，全部可选，缺省即降级）

| 键 | 默认 | 说明 |
|---|---|---|
| `MCP_SEARCH_ENABLED` | `false` | 关掉就回到纯模型推演 |
| `MCP_SEARCH_COMMAND` | 空 | 启动 MCP server 的命令，如 `uvx duckduckgo-mcp-server` |
| `MCP_SEARCH_TOOL` | `search` | 工具名，不同 server 命名不一样 |
| `MCP_SEARCH_TIMEOUT` | `20` | 单次搜索超时秒数 |

### 4.4 三条硬规矩

1. **MCP 挂了不影响出方案**：`search()` 内部吞异常返回 `[]`，图照常往 ④ 走，只是 `verified=false`、页面提示"本次未联网核验"。
2. **搜索是"证据"，不是"答案"**：真正写账号名和爆款共性的是模型，MCP 只提供片段，避免把爬到的脏数据直接当结论。
3. **必须能溯源**：`verified=true` 时每个对标账号挂 `sources` URL，页面可点开；拿不出 URL 的一律 `verified=false`。

> 新增依赖：`langchain-mcp-adapters`（自带 `mcp` SDK）。安装：`uv add langchain-mcp-adapters`。
> 这一步要动 `pyproject.toml` 和 `BenchmarkAccount` 的 schema，属于 v1 之后的小版本，**建议先确认要接哪家搜索 MCP**（免 key 的 DuckDuckGo / 要 key 的 Tavily、博查、Exa）。

---

## 5. 其它五个模块的栈映射（一句话版）

| 模块 | 关键技术点 |
|---|---|
| ③ 热点监控 | `StateGraph` + `send` 并行分发到抖音/小红书/微博；`Annotated[list, operator.add]` 自动合并；LLM 做"赛道相关性"打分（相关度 / 切入角度 / 创作难度） |
| ④ 内容复刻 | yt-dlp 下载（含 `extract_url()` 从抖音分享口令抠链接）→ FunASR 本地转写（离线、免费）→ LLM 五维拆解 → LLM 仿写 → LLM 生成 5 类标题 |
| ⑤ 口播视频 | 提词器纯前端（HTML/CSS/JS 大字滚动，1–15 秒/行）；数字人走 HeyGem 三容器（`gen-video:8383` / `tts:18180` / `asr:10095`），TTS 失败降级 Edge-TTS |
| ⑥ 视频剪辑 | DeepAgents 子代理读 `skills/video-use/SKILL.md` 当操作手册，调 ffmpeg/moviepy/HyperFrames；字幕只能用 `SubtitlesClip` 读 SRT，合成尺寸 `(w, h + h//3)` 防底部裁切 |
| ⑦ 数据复盘 | 本地 douyin 爬虫（Cookie 手动粘贴）→ 最近 20 条真实互动 → 漏斗转化率 → LLM 四维评估 + 三档策略 |

---

## 6. 当前代码与本文的差距（诚实清单）

| 项 | 状态 |
|---|---|
| 五节点串行图 + 内存 State | ✅ 已实现（`workflow/positioning.py`） |
| 结构化 JSON 输出 + 校验失败重试 1 次 | ✅ 已实现（`workflow/llm.py: call_json`） |
| 归一化（占比 100 / 4 周重编号） | ✅ 已实现（`normalize_plan`，移到 ⑤ 号节点） |
| ③ 号节点 MCP 联网搜索 | ❌ 未实现，当前 `verified=false` 纯推演 |
| 其余五模块 | ⏳ 占位页（v2 起逐个落地） |
| 持久化（历史记录、SQLite） | ❌ v1 不做，只导出 md/json |

---

## 7. 依赖清单（v1 现状 / 各模块追加）

```toml
# 已装（v1）
langgraph>=1.2.18            # 编排
langchain-openai>=1.7.3      # 调 DeepSeek
langchain-core               # 随上面带
pydantic>=2 / pydantic-settings
streamlit>=1.66.1            # 页面

# ③ MCP 联网（待确认）
langchain-mcp-adapters       # MCP 客户端，自带 mcp SDK

# ④⑤⑥⑦ 各自安装（v2 起）
yt-dlp  ffmpeg(系统)  funasr  funasr-onnx  modelscope  edge-tts  moviepy
# ⑥ 还需要 node/npx（跑 hyperframes）
# ⑤ 需要 docker（跑 HeyGem 三容器）
```
