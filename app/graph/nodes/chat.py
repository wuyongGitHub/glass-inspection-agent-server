"""闲聊兜底节点。"""
from langchain_core.messages import SystemMessage

from app.formatting import FORMAT_GUIDE
from app.graph.nodes.memory import try_memory
from app.graph.nodes.router import is_ack, is_gibberish, is_greeting
from app.llm import get_llm
from app.state import AgentState

CHAT_PROMPT = (
    "你是玻璃检测部门的智能助手，友好、简洁地回应用户。"
    "与业务无关的问题简短回答，并自然引导回检测业务话题。"
)

# 乱码/无意义输入专用：一句话简短忽略，不调 LLM、不携带历史，
# 避免通用闲聊被引导出与问题无关的长篇业务内容
GIBBERISH_REPLY = (
    "这串看起来像是误触或乱码输入啦～"
    "如果有玻璃检测方面的问题（缺陷判定、检出率、设备选型等），直接告诉我就行。"
)

# 确认/收尾/致谢词（"好的/嗯/谢谢/知道了"）专用：极简回应，
# 不调 LLM、不携带历史，避免重复上一轮的长篇业务内容
ACK_REPLY = "好嘞，有问题随时问我～"

# 打招呼（您好/你好/嗨…）专用：热情介绍能力范围，替代通用闲聊的呆板引导
WELCOME_PROMPT = """你是玻璃工业视觉检测部门的智能助手，用户刚向你打招呼。
请热情但不浮夸地回应，并自然地介绍你能帮用户做的事，让用户知道可以从哪问起：
- 📊 检测数据分析：任意厂家 / 玻璃类型 / 时间范围的检出率、趋势、缺陷排行，
  可生成报表与图表，并主动指出异常和给出优化建议，例：“各厂最近30天检出率对比”
- 🔍 缺陷与质量问答：气泡、结石、划伤、应力斑等缺陷的成因、判定标准、
  检测方法与光源相机选型，例：“划伤和崩边怎么区分”
- 🔁 支持多轮连续追问与综合维度分析
结尾用一句自然的话引导用户直接提需求，不要使用“请问您具体想查询哪些工厂的检测数据呢”这类生硬套话。
全文简体中文，150 字以内。"""


def chat_reply(state: AgentState) -> dict[str, object]:
    messages = state.get("messages") or []
    last_text = messages[-1].content if messages else ""
    # 乱码/无意义输入：直接简短忽略，不调 LLM、不带历史，避免脑补长篇业务内容
    if is_gibberish(last_text):
        return {"final_answer": GIBBERISH_REPLY, "chart_config": None}
    # 确认/收尾词：极简回应，不带历史，避免重复上一轮的长篇业务内容
    if is_ack(last_text):
        return {"final_answer": ACK_REPLY, "chart_config": None}
    # 记忆拦截：命中「记/问/改/删」信号则落库读写，未命中返回 None 走普通闲聊
    mem_result = try_memory(state)
    if mem_result is not None:
        return mem_result
    # 纯问候走欢迎话术，其余走通用闲聊
    prompt = WELCOME_PROMPT if is_greeting(last_text) else CHAT_PROMPT
    try:
        reply = get_llm(temperature=0.7).invoke(
            [SystemMessage(content=prompt + "\n\n" + FORMAT_GUIDE)] + list(messages[-8:])
        )
    except Exception:
        # 模型网关不可用时给友好提示，不让整个会话 500
        return {"final_answer": "抱歉，模型服务暂时不可用，请稍后重试。", "chart_config": None}
    return {"final_answer": reply.content, "chart_config": None}
