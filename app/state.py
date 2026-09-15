"""LangGraph 全局状态：所有节点读写同一份状态。

V2 扩展：在原 qa/data/chat 三流状态之上增加 task / entities / evidence /
hypotheses / cases / risk / actions / citations 等诊断字段。所有新增字段均为
可选（节点用 .get() 读取），保持与既有三流兼容。
"""
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    # 对话历史（add_messages 自动合并追加）
    messages: Annotated[list, add_messages]
    # 用户上传的图片（URL 或 base64 Data URL），存在时优先走视觉分析流（单图，向后兼容）
    image: str
    # 多张图片（URL 或 base64 Data URL 列表），存在时优先走视觉分析流
    images: list[str]
    # 上传文件的解析结果：[{"filename": str, "text": str}]，存在时走文件即时问答流
    file_texts: list[dict]
    # 路由结果: qa / data / chat / diagnosis / vision
    intent: str
    # 任务类型（诊断流细分），如 diagnosis
    task_type: str
    # 领域实体（缺陷/玻璃类型等，已标准化）
    entities: dict
    # 问答流：检索到的知识库片段
    qa_context: list
    # 数据流：解析出的查询参数 / 执行结果
    query_params: dict
    query_rows: list
    # 诊断流：调查计划 / 证据 / 假设 / 案例 / 风险 / 行动
    plan: list
    evidence: list
    hypotheses: list
    cases: list
    risk_level: str
    action_items: list
    citations: list
    confidence: float
    # 输出：最终回答与 ECharts 图表配置（大屏用）
    final_answer: str
    chart_config: dict
    # 会话级上下文：上轮意图与解析出的查询参数，用于多轮指代/追问继承
    last_intent: str
    last_query_params: dict
