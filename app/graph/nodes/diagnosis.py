"""诊断流：数据取证 + 知识检索 + 证据构建 + 四级可信度报告。

流程（串行，先不并行）：
diagnosis_plan -> diagnosis_run（数据/知识/证据）-> diagnosis_report（LLM 综合）。

所有数学/统计信号由 insights.py 确定性产生，LLM 只负责组织推理与措辞，
并严格区分「事实 / 推断 / 假设 / 建议」。
"""
from __future__ import annotations

import json

from langchain_core.messages import SystemMessage

from app.cases.store import search_cases
from app.config import settings
from app.domain import DEFECT_GUIDANCE
from app.formatting import FORMAT_GUIDE
from app.llm import get_embeddings, get_llm
from app.memory.long_term import recall_factory_risks
from app.rag.query_rewrite import rewrite_query
from app.rag.reranker import rerank_top
from app.rag.store import SimpleVectorStore
from app.state import AgentState
from app.tools.chart_builder import build_chart_option
from app.tools.evidence import Evidence, fact, inference, hypothesis, recommendation
from app.tools.insights import build_evidence, build_insights
from app.tools.query_executor import execute_query


def _section(rows: list[dict], name: str) -> list[dict]:
    for sec in rows:
        if isinstance(sec, dict) and sec.get("section") == name:
            return sec.get("data") or []
    return []


def _overview(rows: list[dict]) -> dict:
    data = _section(rows, "overview")
    return data[0] if data else {}


def _retrieve_knowledge(defect: str | None) -> tuple[list[str], list[str]]:
    """检索与缺陷机理/工艺相关的知识片段，返回（片段文本，来源引用）。"""
    store = SimpleVectorStore(settings.vector_store_path)
    if not store.chunks or not defect:
        return [], []
    query = rewrite_query(f"{defect} 成因 工艺 原因 改善 检测标准")
    query_vec = get_embeddings().embed_query(query)
    hits = rerank_top(store, query, query_vec)
    texts = [f'{h["text"]}\n(来源: {h["meta"]["source"]})' for h in hits]
    citations = [h["meta"]["source"] for h in hits]
    return texts, citations


def diagnosis_run(state: AgentState) -> dict:
    params = state.get("query_params") or {}
    entities = state.get("entities") or {}
    defect = entities.get("defect_type")

    evidence: list[Evidence] = []
    rows: list[dict] = []
    error = None
    try:
        result = execute_query(params)
        rows = result["rows"]
    except Exception as e:
        error = str(e)

    citations: list[str] = []
    if error:
        evidence.append(fact(f"数据查询失败：{error}", source="SQL"))
    else:
        ov = _overview(rows)
        if ov:
            evidence.append(fact(
                f"整体检出率 {ov.get('defect_rate_pct')}%，检测量 {ov.get('total')}，不良数 {ov.get('defects')}",
                metric="defect_rate_pct", value=ov.get("defect_rate_pct"),
                source="SQL", calculation_method="SUM(defect_count)/SUM(total_count)",
            ))
        # V2 Evidence Engine：确定性洞察直接转结构化 Evidence（趋势/SPC/离群/帕累托）
        evidence.extend(build_evidence(params.get("metric", "multi"), rows))

    # 缺陷机理：先用确定性对照表，再用 RAG 补充
    if defect and defect in DEFECT_GUIDANCE:
        evidence.append(inference(
            f"「{defect}」典型工艺成因：{DEFECT_GUIDANCE[defect]}",
            source="领域对照表", confidence=0.6,
        ))
    knowledge, citations = _retrieve_knowledge(defect)
    if knowledge:
        evidence.append(fact(
            f"知识库检索到 {len(knowledge)} 条相关片段（含标准/工艺/机理）。",
            source="RAG", confidence=0.5,
        ))

    # 历史案例检索（Case RAG）：结构化匹配 + 症状向量相似度
    case_matches = search_cases(
        defect=defect,
        factory=params.get("factory"),
        glass_type=entities.get("glass_type") or params.get("glass_type"),
        symptoms=" ".join(state.get("plan") or []),
        top_k=5,
    )
    if case_matches:
        top_case = case_matches[0]
        evidence.append(fact(
            f"匹配到 {len(case_matches)} 条相似历史案例，最相似为"
            f"「{top_case.get('defect_type')}·{top_case.get('factory')}」，"
            f"根因：{top_case.get('root_cause')}",
            source="案例库", confidence=0.7,
        ))

    # 长期业务记忆：厂家历史风险（诊断时参考历史异常，可追溯、可人工修正）
    try:
        risks = recall_factory_risks()
        if risks and params.get("factory"):
            relevant = [r for r in risks if params["factory"] in (r.get("subject") or "")]
            for r in relevant[:2]:
                evidence.append(fact(
                    f"长期记忆：厂家「{r.get('subject')}」存在历史风险记录（更新于 {r.get('updated_at')}）",
                    source="长期记忆", confidence=0.6,
                ))
    except Exception:
        pass

    return {
        "query_rows": rows,
        "evidence": [e.model_dump() for e in evidence],
        "cases": case_matches,
        "citations": citations,
        "confidence": 0.8 if (rows and not error) else 0.2,
    }


REPORT_PROMPT = """你是玻璃质量检测诊断专家。基于给定的调查计划、数据事实、确定性统计信号、
缺陷工艺对照与知识库片段，输出一份诊断报告。

严格要求：
1. 分四部分：<strong style="color:#1f6feb">🔍 事实</strong>（数据库/知识库直接给出的）、<strong style="color:#1f6feb">🧭 推断</strong>（由多个事实推导）、
   <strong style="color:#1f6feb">❓ 假设</strong>（待验证的可能原因，按可能性排序）、<strong style="color:#1f6feb">💡 建议</strong>（可执行的检查/改善动作）。
2. 事实/推断必须来自给定材料，不得编造数字；假设可以提出，但要标注"待验证"与缺失数据。
3. 区分相关性与因果：没有设备参数/现场确认时，只能说"疑似/待确认"。
4. 简体中文，结构化 markdown，控制在 600 字以内，结尾列出引用来源。

调查计划：{plan}
数据事实（JSON）：{facts}
确定性统计信号：{insights}
缺陷-工艺对照：{guidance}
知识库片段：{knowledge}
历史案例（相似，仅供借鉴，需结合当前数据判断）：{cases}

""" + FORMAT_GUIDE


def diagnosis_report(state: AgentState) -> dict:
    rows = state.get("query_rows") or []
    params = state.get("query_params") or {}
    plan = state.get("plan") or []
    citations = state.get("citations") or []

    chart = build_chart_option(params.get("metric", "multi"), rows, params) if rows else None
    if not rows:
        return {
            "final_answer": "没有查到可用于诊断的检测数据。请补充厂家、玻璃类型或时间范围后重试。",
            "chart_config": None,
        }

    defect = (state.get("entities") or {}).get("defect_type")
    guidance = DEFECT_GUIDANCE.get(defect, "")
    knowledge, _ = _retrieve_knowledge(defect)
    insights = build_insights(params.get("metric", "multi"), rows)

    cases = state.get("cases") or []
    cases_text = "\n".join(
        f"- 「{c.get('defect_type')}·{c.get('factory')}」根因：{c.get('root_cause')}"
        f"（置信度 {c.get('confidence')}）"
        for c in cases[:3]
    )

    facts = json.dumps(
        {"plan": plan, "rows": rows}, ensure_ascii=False, default=str
    )[:6000]
    prompt = REPORT_PROMPT.format(
        plan="；".join(plan),
        facts=facts,
        insights=insights or "（无明显异常信号）",
        guidance=guidance or "（未命中具体缺陷）",
        knowledge="\n".join(knowledge[:3]) or "（无）",
        cases=cases_text or "（无相似历史案例）",
    )
    try:
        answer = get_llm(temperature=0.3).invoke(
            [SystemMessage(content=prompt), SystemMessage(content="请输出诊断报告")]
        )
        body = answer.content
    except Exception:
        body = (
            "**模型服务暂时不可用**，以下是确定性证据摘要：\n\n"
            + "\n".join(
                e.to_line() for e in [Evidence(**x) for x in state.get("evidence", [])]
            )
        )

    if citations:
        body += "\n\n**引用来源**：" + "、".join(sorted(set(citations)))
    return {"final_answer": body, "chart_config": chart}
