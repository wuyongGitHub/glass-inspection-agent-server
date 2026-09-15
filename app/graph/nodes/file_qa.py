"""文件即时问答流：基于用户上传文件的解析文本回答问题，不落向量库。

文件文本来自 state["file_texts"]（[{"filename": str, "text": str}]）。
每个文件切块后，用 embedding 取与问题最相关的 top-k 块，交由 LLM 生成带引用的回答。
文件文本通常较短（单次上传），embedding 失败时退化为取前若干块。
"""
from __future__ import annotations

import math

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.formatting import FORMAT_GUIDE
from app.llm import get_embeddings, get_llm
from app.rag.ingest import chunk_text
from app.state import AgentState

FILE_QA_PROMPT = """你是玻璃检测部门的知识助手。请仅依据下方【上传文件内容】回答用户问题。
要求：
1. 只依据上传文件内容作答，文件未提及的细节不要编造；
2. 涉及数值/标准/结论时以文件原文为准，并标注出自哪个文件；
3. 使用简体中文，条理清晰，在回答末尾列出引用的文件名；
4. 只回答对话中【最后一条用户消息】的问题，历史问题仅作背景参考，不要复述或重答。

【上传文件内容】
{context}

""" + FORMAT_GUIDE

TOP_K = 8


def _cos(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(x * x for x in b))
    return num / (da * db) if da and db else 0.0


def file_qa(state: AgentState) -> dict:
    file_texts = state.get("file_texts") or []
    history = state.get("messages") or []
    question = history[-1].content if history else ""

    if not file_texts:
        return {
            "intent": "file_qa",
            "final_answer": "未收到可解析的文件内容，请先上传文件。",
            "chart_config": None,
        }

    # 只传了文件、没写具体问题时，默认总结文件，避免返回“请补充问题”
    if not str(question).strip():
        question = "请介绍这份文件的主要内容、用途与关键结论。"

    # 每个文件切块
    chunks: list[tuple[str, str]] = []
    for ft in file_texts:
        fn = ft.get("filename") or "文件"
        text = ft.get("text") or ""
        for c in chunk_text(text):
            chunks.append((fn, c))

    if not chunks:
        return {
            "intent": "file_qa",
            "final_answer": "上传的文件未提取到可用文字（可能是扫描件/图片型文件，或文件为空）。",
            "chart_config": None,
        }

    # 取与问题最相关的 top-k 块（embedding + 余弦），失败则取前 TOP_K
    top: list[tuple[float, str, str]] = []
    try:
        emb = get_embeddings()
        qv = emb.embed_query(question)
        cvs = emb.embed_documents([c for _, c in chunks])
        scored = [
            (_cos(qv, cv), fn, c) for (fn, c), cv in zip(chunks, cvs)
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:TOP_K]
    except Exception:
        top = [(0.0, fn, c) for fn, c in chunks[:TOP_K]]

    context = "\n\n---\n\n".join(f"【{fn}】\n{c}" for _, fn, c in top)
    msgs = [SystemMessage(content=FILE_QA_PROMPT.format(context=context))]
    if len(history) > 1:
        hist_lines = [
            f"{'用户' if isinstance(m, HumanMessage) else '助手'}: {m.content}"
            for m in history[:-1]
            if isinstance(m, (HumanMessage, AIMessage))
        ]
        if hist_lines:
            msgs.append(
                SystemMessage(
                    content="以下是此前的对话历史（仅作背景参考，不要回答其中的旧问题）：\n"
                    + "\n".join(hist_lines)
                )
            )
    msgs.append(HumanMessage(content=question))
    try:
        answer = get_llm().invoke(msgs)
    except Exception:
        return {
            "intent": "file_qa",
            "final_answer": "抱歉，模型服务暂时不可用，请稍后重试。",
            "chart_config": None,
        }
    return {"intent": "file_qa", "final_answer": answer.content, "chart_config": None}
