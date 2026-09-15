"""诊断调查计划：从用户问题抽取实体 -> 生成调查步骤 -> 组装数据查询参数。

诊断流不一次性让 LLM 猜原因，而是先规划调查维度，再逐项用确定性工具验证。
本节点只做「实体抽取 + 计划 + 参数」，真正的数据/知识/证据在 diagnosis.py 里执行。
"""
from __future__ import annotations

from datetime import date, timedelta

from app.db.session import adapt_sql, get_conn
from app.domain.terms import resolve_entities
from app.state import AgentState


def _factory_names() -> list[str]:
    """读取已登记厂家名，用于从用户文本中识别厂家实体（与 router 口径一致）。"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(adapt_sql("SELECT name FROM factories"))
        names: list[str] = []
        for r in cur.fetchall():
            if isinstance(r, dict):
                names.append(r["name"])
            elif hasattr(r, "keys"):
                names.append(dict(r)["name"])
            else:
                names.append(r[0])
        return names
    except Exception:
        return []
    finally:
        conn.close()


def _build_plan(entities: dict, factory: str | None) -> list[str]:
    steps = ["近 30 天检出率趋势与突变点定位"]
    steps.append(f"厂家「{factory}」的多维度对比" if factory else "各厂家对比，定位高检出率厂家")
    steps.append("缺陷类型构成，定位主要缺陷")
    if entities.get("defect_type"):
        steps.append(f"针对「{entities['defect_type']}」的机理、工艺因素与历史案例")
    steps.append("形成原因假设并按风险排序，给出行动建议")
    return steps


def diagnosis_plan(state: AgentState) -> dict:
    text = state["messages"][-1].content
    entities = resolve_entities(text)
    names = _factory_names()
    factory = next((n for n in names if n in text), None)

    today = date.today()
    params = {
        "metric": "multi",
        "factory": factory,
        "glass_type": entities.get("glass_type"),
        "defect_type": entities.get("defect_type"),
        "start_date": (today - timedelta(days=30)).isoformat(),
        "end_date": today.isoformat(),
    }
    return {
        "task_type": "diagnosis",
        "entities": entities,
        "query_params": params,
        "last_query_params": params,
        "plan": _build_plan(entities, factory),
    }
