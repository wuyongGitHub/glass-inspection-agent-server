"""查询改写：检索前对用户问题做轻量改写，提升召回。

默认采用确定性改写（同义词/canonical 实体扩展），不额外消耗 LLM；
需要时可用 rewrite_query_llm 做语义改写（由调用方显式触发）。
"""
from __future__ import annotations

from app.config import settings
from app.domain.terms import resolve_entities


def rewrite_query(text: str) -> str:
    """确定性改写：识别到的缺陷/玻璃类型 canonical 词若原文未出现则追加，改善召回。"""
    if not settings.query_rewrite or not text:
        return text
    ents = resolve_entities(text)
    added = []
    for term in (ents["defect_type"], ents["glass_type"]):
        if term and term not in text:
            added.append(term)
    return f"{text} {' '.join(added)}".strip() if added else text
