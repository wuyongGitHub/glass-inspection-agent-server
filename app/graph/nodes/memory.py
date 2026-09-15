"""用户偏好记忆节点：识别「记 / 问 / 改 / 删」记忆意图，读写 business_memory。

在 chat 闲聊流之前拦截：检测到记忆信号时用 LLM 结构化判断动作并落库，
否则返回 None 交回普通闲聊，避免把「我喜欢吃冰激凌」当纯闲聊而「假装记住」。
"""
from __future__ import annotations

from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.llm import get_llm
from app.memory.long_term import forget, recall, remember
from app.state import AgentState

# 记忆信号词（规则预筛，命中才交给 LLM 精判，避免每次闲聊都调 LLM）
MEMORY_HINT = [
    "记一下", "记住", "记得", "别忘了", "帮我记", "记下来", "备注一下",
    "喜欢", "不喜欢", "讨厌", "偏好", "口味", "爱吃", "不爱吃", "爱喝", "不爱喝",
    "我叫", "我是", "我来自", "我的名字", "你记住了吗", "忘掉", "忘了", "删除",
]


def _has_memory_signal(text: str) -> bool:
    return any(w in text for w in MEMORY_HINT)


MEMORY_PROMPT = """你是记忆识别器。判断用户这条消息是否与「长期记忆 / 个人偏好」相关，并给出操作。

操作定义：
- remember：用户陈述了一个关于自己的偏好或信息（含「记一下 / 我喜欢 / 我不喜欢 / 我叫 / 我是…」），
  需要写入或更新长期记忆。注意：用户否定旧偏好（如「我不喜欢X了」）也算 remember，
  用 fact 记录最新状态（如「不喜欢X」），subject 仍指向该事物本身。
- forget：用户明确要求删除 / 忘掉某条记忆。
- query：用户在询问自己之前告诉过你什么（如「我喜欢吃什么」「我叫什么」「我不喜欢什么」）。
- none：与记忆 / 偏好无关，纯闲聊或业务问题。

抽取要求：
- subject：记忆主题词，简短、规范（如「冰激凌」「篮球」「名字」），仅 remember / forget 需要。
  同一事物务必用同一个规范词，避免「冰激凌」与「冰淇淋」混用。
- fact：要记住的最新事实（如「喜欢吃冰激凌」「不喜欢吃冰激凌」「叫张三」），仅 remember 需要。

只输出一个 JSON 对象，例如 {"action":"remember","subject":"冰激凌","fact":"喜欢吃冰激凌"}。
不要输出任何其他文字。"""


class MemoryAction(BaseModel):
    action: Literal["remember", "forget", "query", "none"] = Field(description="记忆操作类型")
    subject: str = Field(default="", description="记忆主题词")
    fact: str = Field(default="", description="要记住的最新事实")


def _facts_summary(mems: list[dict]) -> str:
    """把已反序列化的记忆拼成可读文本，供 query 分支兜底。"""
    parts = []
    for m in mems:
        payload = m.get("payload") or {}
        fact = payload.get("fact") if isinstance(payload, dict) else ""
        if m.get("subject") and fact:
            parts.append(f"{m['subject']}：{fact}")
    return "；".join(parts)


def try_memory(state: AgentState) -> dict | None:
    """尝试把最后一条消息当作记忆操作处理；不是记忆则返回 None。

    返回 None 表示交回普通闲聊流程；返回 dict 表示已生成最终回答。
    """
    messages = state.get("messages") or []
    last_text = messages[-1].content if messages else ""
    if not last_text or not _has_memory_signal(last_text):
        return None

    try:
        decision = get_llm(temperature=0).with_structured_output(
            MemoryAction, method="json_mode"
        ).invoke([
            SystemMessage(content=MEMORY_PROMPT),
            HumanMessage(content=last_text),
        ])
    except Exception:
        # 判定失败退回普通闲聊，不让记忆识别影响主流程可用性
        return None

    if decision.action == "none":
        return None

    if decision.action == "remember":
        subject = (decision.subject or "").strip()
        fact = (decision.fact or "").strip()
        if not subject or not fact:
            return None
        try:
            remember("user_preference", subject, {"fact": fact})
        except Exception:
            return None
        return {"final_answer": f"好的，我记住了：{fact}。", "chart_config": None}

    if decision.action == "forget":
        subject = (decision.subject or "").strip()
        if not subject:
            return None
        try:
            n = forget(memory_type="user_preference", subject=subject)
        except Exception:
            return None
        if n:
            return {"final_answer": f"好的，关于「{subject}」的记忆已删除。", "chart_config": None}
        return {"final_answer": f"我没有找到关于「{subject}」的记忆哦。", "chart_config": None}

    if decision.action == "query":
        try:
            mems = recall(memory_type="user_preference")
        except Exception:
            mems = []
        if not mems:
            return {"final_answer": "我目前还没有记下你的相关偏好哦，你可以直接告诉我。",
                    "chart_config": None}
        facts = "\n".join(
            f"- {m.get('subject', '')}：{m.get('payload', {}).get('fact', '') if isinstance(m.get('payload'), dict) else ''}"
            for m in mems
        )
        try:
            reply = get_llm().invoke([
                SystemMessage(content=(
                    "你是玻璃检测部门的智能助手。下面是此前用户告诉过你、你已记住的偏好信息，"
                    "请仅依据这些信息，自然、准确地回答用户刚才的问题"
                    "（例如问喜欢 / 不喜欢什么就如实回答）。"
                    "若信息里没有相关内容，就如实说明还没记住，不要编造。\n\n"
                    "【已记住的信息】\n" + facts
                )),
                HumanMessage(content=last_text),
            ])
            return {"final_answer": reply.content, "chart_config": None}
        except Exception:
            return {"final_answer": f"根据我记下的信息：{_facts_summary(mems)}。",
                    "chart_config": None}

    return None
