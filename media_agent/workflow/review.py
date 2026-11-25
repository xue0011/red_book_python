"""
数据复盘工作流 — 接入真实抖音数据采集


使用本地 douyin 项目 (erma0/douyin) 抓取个人主页作品数据，
LLM 进行流量漏斗诊断、内容质量评估和优化策略建议。


douyin 项目: C:/Users/13261/Documents/project/douyin
"""
from typing import TypedDict
import os, sys, json
from langgraph.graph import StateGraph, START, END
from workflow.llm import llm_call
from config import settings


# 添加 douyin 项目到 Python 路径（目录在 config.py / .env 里配：DOUYIN_PROJECT_DIR）
DOUYIN_PROJECT = (
    settings.douyin_project_dir
    or os.path.expanduser("~/Documents/project/douyin")
)
if os.path.isdir(DOUYIN_PROJECT) and DOUYIN_PROJECT not in sys.path:
    sys.path.insert(0, DOUYIN_PROJECT)




class ReviewState(TypedDict):
    profile_url: str        # 抖音主页 URL
    video_data: str         # JSON: 作品列表 + 统计数据
    funnel_diagnosis: str
    content_assessment: str
    suggestions: str
    error_msg: str




#  Node 1: 从抖音抓取真实数据
def node_fetch_data(state: ReviewState) -> dict:
    """
    使用本地 douyin 项目采集个人主页作品数据。


    输入: 抖音用户主页 URL（如 https://www.douyin.com/user/xxx）
    输出: JSON 格式的作品列表，包含播放/点赞/评论/分享/收藏数据
    """
    url = state.get("profile_url", "")


    if not url:
        return {"error_msg": "请输入抖音用户主页链接",
                "video_data": "{}"}


    # 尝试使用本地 douyin 项目采集
    try:
        from backend.lib.douyin import Douyin


        # 直接从配置文件读取 Cookie（绕过缓存，确保读到最新值）
        settings_file = os.path.join(DOUYIN_PROJECT, "config", "settings.json")
        cookie = ""
        if os.path.exists(settings_file):
            try:
                with open(settings_file, "r", encoding="utf-8") as f:
                    cookie = json.load(f).get("cookie", "")
            except Exception:
                pass


        if not cookie:
            return {
                "error_msg": (
                    "douyin 项目尚未配置 Cookie，请在页面上方 🍪 抖音 Cookie 配置 中粘贴并保存"
                ),
                "video_data": "[]",
            }


        # 处理 /user/self（通过页面HTML提取自己的sec_uid）
        if url.endswith("/user/self"):
            import niquests as requests_mod
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Cookie": cookie,
                }
                resp = requests_mod.get(url, headers=headers, timeout=15)
                import re
                match = re.search(r'sec_uid[\"\s:=]+(MS4w[^\"\s&\\\\]+)', resp.text)
                if match:
                    url = f"https://www.douyin.com/user/{match.group(1)}"
                    print(f"[复盘] 解析到自己的主页: {url}")
            except Exception as e:
                print(f"[复盘] 解析 /user/self 失败: {e}，将使用原URL")


        print(f"[复盘] 正在采集: {url}")
        # 仅采集数据不下载视频，限制最多 20 条
        crawler = Douyin(url, limit=20, type="post", cookie=cookie)
        crawler.run()
        results = crawler.results


        if results and len(results) > 0:
            # 检测是否有真实播放量（自己的主页有，别人的主页全为0）
            has_play_count = any(
                int(r.get("play_count", r.get("playCount", 0))) > 0
                for r in results[:5]
            )


            items = []
            for r in results[:20]:
                # douyin 爬虫 parser 把 statistics 字段展开到了顶层，直接读 r
                item = {
                    "标题": str(r.get("desc", r.get("title", "")))[:80],
                    "发布时间": str(r.get("time", "")),
                    "时长": f"{int(r.get('duration', 0) / 1000)}秒" if r.get("duration") else "未知",
                    "点赞": int(r.get("digg_count", r.get("diggCount", 0))),
                    "评论": int(r.get("comment_count", r.get("commentCount", 0))),
                    "分享": int(r.get("share_count", r.get("shareCount", 0))),
                    "收藏": int(r.get("collect_count", r.get("collectCount", 0))),
                }
                # 只有自己的主页才显示播放量
                if has_play_count:
                    item["播放"] = int(r.get("play_count", r.get("playCount", 0)))
                items.append(item)


            data_json = json.dumps(items, ensure_ascii=False, indent=2)
            print(f"[复盘] 采集完成: {len(items)} 条作品")
            return {"video_data": data_json, "error_msg": ""}
        else:
            return {"error_msg": "未获取到作品数据，请检查链接是否正确或 Cookie 是否有效",
                    "video_data": "[]"}


    except ImportError as e:
        # douyin 项目依赖未安装
        return {
            "error_msg": f"douyin 项目依赖缺失: {e}\n请先: cd {DOUYIN_PROJECT} && uv pip install -r requirements.txt",
            "video_data": "[]",
        }
    except Exception as e:
        return {
            "error_msg": f"数据采集失败: {e}\n请确保 douyin 项目已配置 Cookie",
            "video_data": "[]",
        }




#  Node 2: LLM 漏斗诊断
def node_funnel(state: ReviewState) -> dict:
    data = state.get("video_data", "[]")
    if data == "[]" or data == "{}":
        return {"funnel_diagnosis": "暂无数据"}


    prompt = f"""你是短视频运营诊断专家。以下是抖音个人主页的真实数据：


{data}


请诊断：
1. **播放/互动量分析** — 哪些作品播放/点赞/评论/分享/收藏高？高表现作品的共同特征是什么？
   （注：如果播放量为0或不含播放字段，说明是非本人主页，只基于互动数据做分析即可）
2. **转化分析** — 计算点赞→评论转化率、点赞→收藏转化率（如有播放量，加上播放→点赞转化率），找出各指标最优/最差作品
3. **流量漏斗** — 分析从曝光到互动的转化情况
4. **发布时间** — 哪个时间段发布效果最好？
每个结论用数据支撑。
"""
    return {"funnel_diagnosis": llm_call(prompt, temperature=0.3)}




#
#  Node 3: LLM 内容评估
#


def node_content(state: ReviewState) -> dict:
    data = state.get("video_data", "[]")
    if data == "[]" or data == "{}":
        return {"content_assessment": "暂无数据"}


    prompt = f"""基于这些抖音作品真实数据，评估内容质量：


{data}


从以下维度评估：
1. **内容方向** — 哪些主题/选题表现最好？有没有明显的内容偏好？
2. **标题技巧** — 高播放作品的标题有什么共同特征？
3. **节奏建议** — 根据播放/互动数据，建议最佳视频时长
4. **竞争分析** — 对比同赛道平均水平，这组数据的优劣势在哪里
"""
    return {"content_assessment": llm_call(prompt, temperature=0.4)}




#  Node 4: LLM 优化策略
def node_suggest(state: ReviewState) -> dict:
    data = state.get("video_data", "[]")
    if data == "[]" or data == "{}":
        return {"suggestions": "暂无数据"}


    funnel = state.get("funnel_diagnosis", "")
    content = state.get("content_assessment", "")


    prompt = f"""基于抖音账号真实数据分析，生成可执行优化方案：


真实数据:
{data}


诊断结果:
{funnel}


内容评估:
{content}


请给出：
1. **立即执行**（3条 — 下一条视频就能用的改进）
2. **短期策略**（3条 — 1周内迭代方向）
3. **长期规划**（3条 — 1个月优化路径）
4. **A/B 测试方案** — 设计一个对比实验：改什么、怎么测、看什么指标
"""
    return {"suggestions": llm_call(prompt, temperature=0.4)}




#  Graph
builder = StateGraph(ReviewState)
builder.add_node("fetch", node_fetch_data)
builder.add_node("funnel", node_funnel)
builder.add_node("content", node_content)
builder.add_node("suggest", node_suggest)


builder.add_edge(START, "fetch")
builder.add_edge("fetch", "funnel")
builder.add_edge("funnel", "content")
builder.add_edge("content", "suggest")
builder.add_edge("suggest", END)


review_graph = builder.compile()




def run_review(profile_url: str) -> dict:
    """运行数据复盘工作流


    Args:
        profile_url: 抖音用户主页链接


    Returns:
        {"video_data": str, "funnel_diagnosis": str,
         "content_assessment": str, "suggestions": str}
    """
    return review_graph.invoke({"profile_url": profile_url})