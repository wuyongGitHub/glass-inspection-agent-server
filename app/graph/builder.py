"""主图组装：router 分流到问答流 / 数据查询流 / 诊断流 / 闲聊。

演示用 MemorySaver（进程内）；生产部署替换为 SqliteSaver / PostgresSaver
以支持多副本与重启恢复，见 README 扩展路线。
"""
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.graph.nodes import chat, data, qa
from app.graph.nodes.diagnosis import diagnosis_report, diagnosis_run
from app.graph.nodes.file_qa import file_qa
from app.graph.nodes.planner import diagnosis_plan
from app.graph.nodes.router import router_node
from app.graph.nodes.vision import vision_analyze
from app.state import AgentState


def route_after_router(state: AgentState) -> str:
    return state.get("intent", "chat")


def route_start(state: AgentState) -> str:
    """入口分流：带图片优先走视觉分析流，带文件走文件即时问答流，否则走文本路由。"""
    if state.get("image") or state.get("images"):
        return "vision_analyze"
    if state.get("file_texts"):
        return "file_qa"
    return "router"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("router", router_node)
    g.add_node("vision_analyze", vision_analyze)
    g.add_node("file_qa", file_qa)
    g.add_node("qa_retrieve", qa.qa_retrieve)
    g.add_node("qa_generate", qa.qa_generate)
    g.add_node("data_parse", data.data_parse)
    g.add_node("data_execute", data.data_execute)
    g.add_node("data_analyze", data.data_analyze)
    g.add_node("diagnosis_plan", diagnosis_plan)
    g.add_node("diagnosis_run", diagnosis_run)
    g.add_node("diagnosis_report", diagnosis_report)
    g.add_node("chat_reply", chat.chat_reply)

    g.add_conditional_edges(
        START,
        route_start,
        {"vision_analyze": "vision_analyze", "file_qa": "file_qa", "router": "router"},
    )
    g.add_conditional_edges(
        "router",
        route_after_router,
        {
            "qa": "qa_retrieve",
            "data": "data_parse",
            "diagnosis": "diagnosis_plan",
            "chat": "chat_reply",
        },
    )
    g.add_edge("vision_analyze", END)
    g.add_edge("file_qa", END)
    g.add_edge("qa_retrieve", "qa_generate")
    g.add_edge("qa_generate", END)
    g.add_edge("data_parse", "data_execute")
    g.add_edge("data_execute", "data_analyze")
    g.add_edge("data_analyze", END)
    g.add_edge("diagnosis_plan", "diagnosis_run")
    g.add_edge("diagnosis_run", "diagnosis_report")
    g.add_edge("diagnosis_report", END)
    g.add_edge("chat_reply", END)

    return g.compile(checkpointer=MemorySaver())
