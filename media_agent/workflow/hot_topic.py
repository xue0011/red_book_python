"""热点监控工作流 — TrendRadar 真实抓取 + LLM筛选 + 选题建议


基于 sansan0/TrendRadar 架构，使用 NewsNow API 抓取 35+ 平台热点。
GitHub: https://github.com/sansan0/TrendRadar
"""
from typing import TypedDict, Annotated
import operator
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from workflow.llm import llm_call
from tools.trend_radar_client import fetch_hot_topics, fetch_platform_hot, PLATFORM_IDS
import json




class HotTopicState(TypedDict):
    platform: str
    account_field: str
    raw_topics: Annotated[list, operator.add]  # 并行结果合并
    filtered: str
    suggestions: str



#  节点：并行抓取各平台热点（真实 API）
def fetch_douyin(state: HotTopicState) -> dict:
    """抓取抖音热榜 — TrendRadar NewsNow API"""
    try:
        topics = fetch_platform_hot("douyin")
        return {"raw_topics": [{"source": "抖音", **t} for t in topics]}
    except Exception as e:
        print(f"[热点] 抖音抓取失败: {e}")
        return {"raw_topics": []}




def fetch_weibo(state: HotTopicState) -> dict:
    """抓取微博热搜 — TrendRadar NewsNow API"""
    try:
        topics = fetch_platform_hot("weibo")
        return {"raw_topics": [{"source": "微博", **t} for t in topics]}
    except Exception as e:
        print(f"[热点] 微博抓取失败: {e}")
        return {"raw_topics": []}




def fetch_zhihu(state: HotTopicState) -> dict:
    """抓取知乎热榜 — TrendRadar NewsNow API"""
    try:
        topics = fetch_platform_hot("zhihu")
        return {"raw_topics": [{"source": "知乎", **t} for t in topics]}
    except Exception as e:
        print(f"[热点] 知乎抓取失败: {e}")
        return {"raw_topics": []}




def fetch_xiaohongshu(state: HotTopicState) -> dict:
    """抓取小红书热榜 — TrendRadar NewsNow API"""
    try:
        topics = fetch_platform_hot("xiaohongshu")
        return {"raw_topics": [{"source": "小红书", **t} for t in topics]}
    except Exception as e:
        print(f"[热点] 小红书抓取失败: {e}")
        return {"raw_topics": []}




def fetch_bilibili(state: HotTopicState) -> dict:
    """抓取B站热搜 — TrendRadar NewsNow API"""
    try:
        topics = fetch_platform_hot("bilibili-hot-search")
        return {"raw_topics": [{"source": "B站", **t} for t in topics]}
    except Exception as e:
        print(f"[热点] B站抓取失败: {e}")
        return {"raw_topics": []}




#  路由：根据平台选择并行抓取源
PLATFORM_SENDS = {
    "抖音":   [Send("fetch_douyin", {})],
    "小红书": [Send("fetch_xiaohongshu", {})],
    "微博":   [Send("fetch_weibo", {})],
    "知乎":   [Send("fetch_zhihu", {})],
    "B站":    [Send("fetch_bilibili", {})],
}




def route_fetch(state: HotTopicState):
    """根据平台决定并行抓取哪些源"""
    platform = state.get("platform", "全部")
    if platform == "全部":
        return [
            Send("fetch_douyin", state),
            Send("fetch_weibo", state),
            Send("fetch_zhihu", state),
            Send("fetch_xiaohongshu", state),
            Send("fetch_bilibili", state),
        ]
    sends = PLATFORM_SENDS.get(platform, [Send("fetch_douyin", state)])
    return [Send(s.node, state) for s in sends]




#  节点：LLM 筛选 + 选题建议
def node_filter(state: HotTopicState) -> dict:
    """LLM 筛选与赛道相关的热点"""
    topics = state.get("raw_topics", [])
    if not topics:
        return {"filtered": "暂无热点数据，请检查网络连接或稍后重试。"}


    topics_json = json.dumps(topics, ensure_ascii=False)
    prompt = f"""你是「{state["account_field"]}」赛道的内容策划。


当前热点（{len(topics)}条）：
{topics_json}


筛选出与你赛道相关度高的热点，按相关度排序。对每个热点给出：
- 相关度评分(1-10)
- 推荐切入角度（50字以内）
- 创作难度（简单/中等/困难）
"""
    return {"filtered": llm_call(prompt, temperature=0.4)}




def node_suggest(state: HotTopicState) -> dict:
    """生成具体选题建议"""
    filtered = state.get("filtered", "")
    if "暂无热点数据" in filtered:
        return {"suggestions": "暂无选题建议，请先确保热点数据获取成功。"}


    prompt = f"""基于筛选出的热点：
{filtered}


为每个热点生成2个具体选题，每个选题包含：
1. 标题模板（可直接套用）
2. 内容框架（开头钩子-核心内容-结尾引导）
3. 预估播放量区间
"""
    return {"suggestions": llm_call(prompt, temperature=0.6)}




#  构建并行工作流图
builder = StateGraph(HotTopicState)
builder.add_node("fetch_douyin", fetch_douyin)
builder.add_node("fetch_weibo", fetch_weibo)
builder.add_node("fetch_zhihu", fetch_zhihu)
builder.add_node("fetch_xiaohongshu", fetch_xiaohongshu)
builder.add_node("fetch_bilibili", fetch_bilibili)
builder.add_node("filter", node_filter)
builder.add_node("suggest", node_suggest)


builder.add_conditional_edges(START, route_fetch,
    ["fetch_douyin", "fetch_weibo", "fetch_zhihu",
     "fetch_xiaohongshu", "fetch_bilibili"])
builder.add_edge("fetch_douyin", "filter")
builder.add_edge("fetch_weibo", "filter")
builder.add_edge("fetch_zhihu", "filter")
builder.add_edge("fetch_xiaohongshu", "filter")
builder.add_edge("fetch_bilibili", "filter")
builder.add_edge("filter", "suggest")
builder.add_edge("suggest", END)


hot_topic_graph = builder.compile()




#  对外接口
def run_hot_topic(platform: str, account_field: str) -> dict:
    """运行热点监控工作流


    Args:
        platform: 平台名（"抖音"/"小红书"/"微博"/"知乎"/"B站"/"全部"）
        account_field: 赛道描述（如 "科技测评 / AI工具分享"）


    Returns:
        {"raw_topics": [...], "filtered": "...", "suggestions": "..."}
    """
    return hot_topic_graph.invoke({
        "platform": platform,
        "account_field": account_field,
    })




graph = hot_topic_graph.get_graph()
try:  # 需要 pygraphviz；没装就跳过，别让 import 期崩掉整页
    graph.draw_png("positioning_workflow.png")
except Exception as _exc:  # noqa: BLE001
    print(f"[热点] 跳过流程图导出：{_exc}")