"""短期会话记忆：封装本轮会话上下文与追问参数继承。

会话原始消息由 LangGraph checkpointer 持久化，本模块只提供
「从 state 提取可复用短期上下文」的辅助函数，供长期记忆沉淀时调用。
"""
from __future__ import annotations

from app.state import AgentState


def session_context(state: AgentState) -> dict:
    """提取本轮会话的可复用上下文（意图/实体/查询参数/结论）。"""
    return {
        "intent": state.get("intent"),
        "task_type": state.get("task_type"),
        "entities": state.get("entities") or {},
        "query_params": state.get("query_params") or {},
        "confidence": state.get("confidence"),
    }
