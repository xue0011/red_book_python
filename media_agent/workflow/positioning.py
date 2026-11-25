"""账号定位工作流 — 3步：画像分析 → 对标搜索 → 方案生成"""
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from workflow.llm import llm_call
from db.session import PositioningRecord, get_session




class PositioningState(TypedDict):
    user_info: str
    profile: str
    competitors: str
    plan: str




#   节点
def node_analyze(state: PositioningState) -> dict:
    prompt = f"""你是自媒体定位分析师。分析以下用户的画像：


【用户信息】
{state["user_info"]}


从4个维度分析：
1. 专业优势（核心竞争力）
2. 内容风格匹配（性格适合什么风格）
3. 3个推荐赛道（按优先级，说明理由）
4. 差异化切入点（与同类账号的区别）
"""
    return {"profile": llm_call(prompt, temperature=0.7)}




def node_competitors(state: PositioningState) -> dict:
    prompt = f"""基于以下用户画像，推荐5个对标账号：


{state["profile"]}


要求：
- 2个头部大号（天花板参考），3个腰部账号（可追赶目标）
- 每个账号说明：账号名、粉丝量级、内容特色、可借鉴点
"""
    return {"competitors": llm_call(prompt, temperature=0.7)}




def node_plan(state: PositioningState) -> dict:
    prompt = f"""基于以下信息，生成完整的账号定位策划方案：


【用户画像】
{state["profile"]}


【对标账号】
{state["competitors"]}


输出结构化方案：
1. **一句话定位**（你是谁，提供什么价值）
2. **目标受众**（3类核心粉丝画像）
3. **内容版图**（5个内容系列，每个说明主题+形式+频率）
4. **人设打造**（语言风格、视觉风格、记忆点）
5. **变现路径**（3条商业模式，从近到远排列）
6. **30天起号计划**（每周运营重点和内容数量）
"""
    return {"plan": llm_call(prompt, temperature=0.7)}




#   构建图
builder = StateGraph(PositioningState)
builder.add_node("analyze", node_analyze)
builder.add_node("competitors", node_competitors)
builder.add_node("plan", node_plan)


builder.add_edge(START, "analyze")
builder.add_edge("analyze", "competitors")
builder.add_edge("competitors", "plan")
builder.add_edge("plan", END)


positioning_graph = builder.compile()




#   对外接口
def run_positioning(user_info: str) -> dict:
    """运行定位工作流并保存结果"""
    result = positioning_graph.invoke({"user_info": user_info})


    # 保存到数据库
    try:
        with get_session() as session:
            record = PositioningRecord(
                user_name=user_info[:50],
                user_input=user_info,
                profile_analysis=result.get("profile", ""),
                competitors=result.get("competitors", ""),
                positioning_plan=result.get("plan", ""),
            )
            session.add(record)
            session.commit()
    except Exception as e:
        print(f"[DB] 保存失败: {e}")


    return result