"""RAG 重排序：在混合检索粗召回之后做精排。

信号融合（semantic + lexical + domain entity + authority）：
1. semantic  —— 向量余弦（相对归一）
2. lexical   —— 查询词法 IDF 加权覆盖率（术语精确命中）
3. domain    —— 文档元数据（glass_type / defect_type）与查询实体匹配加成分
4. authority —— 文档权威度（标准/判级/SOP 等来源加权）

默认粗召回 Top=settings.rerank_candidates，精排后取 Top=settings.rerank_top_k，
替代原先固定 Top-K 直接给 LLM，改善专业问答的命中质量。
"""
from __future__ import annotations

from app.config import settings
from app.domain.terms import resolve_entities
from app.rag.store import _gram_features


def _authority(meta: dict) -> float:
    """文档权威度：显式 meta.authority 优先，否则按来源文件名启发式。"""
    if meta.get("authority") is not None:
        try:
            return float(meta["authority"])
        except (TypeError, ValueError):
            pass
    doc_type = str(meta.get("doc_type", "")).lower()
    source = str(meta.get("source", "")).lower()
    if doc_type in ("standard", "sop") or any(
        k in source for k in ("标准", "判级", "判定", "规范", "规格", "sop", "作业")
    ):
        return 1.2
    return 1.0


def rerank(store, query_text: str, query_vec: list[float], candidates: list[dict]) -> list[dict]:
    """对候选 chunk 精排，返回按综合分降序的 chunk 列表（不截断，由调用方取 Top）。"""
    if not candidates:
        return []
    entities = resolve_entities(query_text)
    q_defect, q_glass = entities["defect_type"], entities["glass_type"]
    grams = _gram_features(query_text)
    weights = store._df_weighted_hits(grams)
    denom = sum(weights.values()) or 1.0

    cos_scores = [store._cos(query_vec, c["vec"]) for c in candidates]
    cos_max = max(cos_scores) if cos_scores else 0.0

    scored: list[tuple[float, dict]] = []
    for idx, c in enumerate(candidates):
        meta = c.get("meta") or {}
        cset = store._feat_cache.get(c["text"])
        if cset is None:
            cset = _gram_features(c["text"])
            store._feat_cache[c["text"]] = cset
        lexical = sum(weights[g] for g in grams & cset) / denom
        semantic = (cos_scores[idx] / cos_max) if cos_max > 0 else 0.0

        domain = 0.0
        if q_defect and (q_defect in c["text"] or str(meta.get("defect_type")) == q_defect):
            domain += 0.15
        if q_glass and (q_glass in c["text"] or str(meta.get("glass_type")) == q_glass):
            domain += 0.10

        authority = _authority(meta)
        # 语义 0.45 + 词法 0.30 + 领域 0.15 + 权威度（乘性加权 0.10 区间）
        score = 0.45 * semantic + 0.30 * lexical + domain + 0.10 * (authority - 1.0)
        scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored]


def rerank_top(store, query_text: str, query_vec: list[float]) -> list[dict]:
    """粗召回 + 精排，返回最终 Top-K 片段。"""
    candidates = store.search(query_vec, k=settings.rerank_candidates, query_text=query_text)
    ranked = rerank(store, query_text, query_vec, candidates)
    return ranked[: settings.rerank_top_k]
