"""历史案例库 Case RAG：结构化条件匹配 + 症状向量相似度检索，以及案例沉淀。

案例沉淀自诊断确认结果，形成「问题 → 原因 → 措施 → 效果」的企业经验资产。
检索同时采用向量相似度（symptoms）与结构化条件匹配（缺陷/厂家/玻璃类型/产线）。
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Optional

from app.db.session import adapt_sql, get_conn


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _jsonable(v):
    return float(v) if isinstance(v, Decimal) else v


def _rows(cur) -> list[dict]:
    """把 cursor 结果转 list[dict]，兼容 SQLite Row 与 MySQL DictCursor。"""
    fetched = cur.fetchall()
    if not fetched:
        return []
    first = fetched[0]
    if isinstance(first, dict):
        return [{k: _jsonable(v) for k, v in row.items()} for row in fetched]
    if cur.description and not hasattr(first, "keys"):
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in fetched]
    return [dict(r) for r in fetched]


def _cos(a, b) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _embed(text: str):
    """向量化文本，embedding 不可用时返回 None（降级为纯结构化匹配）。"""
    try:
        from app.llm import get_embeddings

        return get_embeddings().embed_query(text)
    except Exception:
        return None


def search_cases(defect: Optional[str] = None, factory: Optional[str] = None,
                 glass_type: Optional[str] = None, line_no: Optional[str] = None,
                 symptoms: Optional[str] = None, top_k: int = 5,
                 min_score: float = 0.25) -> list[dict]:
    """检索历史案例：结构化字段匹配 + 症状向量相似度综合排序。"""
    conn = get_conn()
    if hasattr(conn, "row_factory"):
        import sqlite3

        conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(adapt_sql("SELECT * FROM cases WHERE status='confirmed'"))
        rows = _rows(cur)
    finally:
        conn.close()
    if not rows:
        return []

    sym_vec = _embed(symptoms) if symptoms else None
    scored: list[tuple[float, dict]] = []
    for r in rows:
        score = 0.0
        if defect and r.get("defect_type") == defect:
            score += 0.6
        if factory and factory in (r.get("factory") or ""):
            score += 0.2
        if glass_type and glass_type in (r.get("glass_type") or ""):
            score += 0.1
        if line_no and line_no == r.get("line_no"):
            score += 0.1
        if sym_vec:
            rv = _embed(r.get("symptoms") or "")
            if rv:
                score += _cos(sym_vec, rv)
        scored.append((score, r))

    scored.sort(key=lambda x: -x[0])
    out = []
    for score, r in scored:
        if score < min_score:
            continue
        r["score"] = round(score, 4)
        out.append(r)
    return out[:top_k]


def get_case(case_id: int) -> Optional[dict]:
    """读取案例详情（含证据与措施）。"""
    conn = get_conn()
    if hasattr(conn, "row_factory"):
        import sqlite3

        conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(adapt_sql("SELECT * FROM cases WHERE id=?"), (case_id,))
        case_rows = _rows(cur)
        if not case_rows:
            return None
        c = case_rows[0]
        cur.execute(adapt_sql("SELECT kind, claim, source FROM case_evidence WHERE case_id=?"),
                    (case_id,))
        c["evidence"] = _rows(cur)
        cur.execute(adapt_sql("SELECT action, result FROM case_actions WHERE case_id=?"),
                    (case_id,))
        c["actions"] = _rows(cur)
        return c
    finally:
        conn.close()


def save_case(defect_type: str, symptoms: str, root_cause: str,
              factory: Optional[str] = None, glass_type: Optional[str] = None,
              line_no: Optional[str] = None, shift: Optional[str] = None,
              metrics: Optional[dict] = None, confidence: float = 0.5,
              evidence: Optional[list] = None, actions: Optional[list] = None,
              status: str = "pending") -> int:
    """沉淀一条案例（默认 pending，人工确认后可置 confirmed）。

    evidence: list[(kind, claim, source)]；actions: list[(action, result)]。
    返回新案例 id。
    """
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            adapt_sql(
                "INSERT INTO cases (defect_type, glass_type, factory, line_no, shift, "
                "symptoms, metrics, root_cause, confidence, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            ),
            (defect_type, glass_type, factory, line_no, shift, symptoms,
             json.dumps(metrics, ensure_ascii=False) if metrics else None,
             root_cause, confidence, status, _now()),
        )
        case_id = cur.lastrowid
        for kind, claim, src in (evidence or []):
            cur.execute(
                adapt_sql("INSERT INTO case_evidence (case_id, kind, claim, source) VALUES (?,?,?,?)"),
                (case_id, kind, claim, src),
            )
        for action, result in (actions or []):
            cur.execute(
                adapt_sql("INSERT INTO case_actions (case_id, action, result) VALUES (?,?,?)"),
                (case_id, action, result),
            )
        conn.commit()
        return int(case_id)
    finally:
        conn.close()


def list_cases(defect: Optional[str] = None, status: str = "confirmed",
               limit: int = 50) -> list[dict]:
    """列出案例（可按缺陷过滤）。"""
    conn = get_conn()
    if hasattr(conn, "row_factory"):
        import sqlite3

        conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        if defect:
            cur.execute(
                adapt_sql("SELECT * FROM cases WHERE status=? AND defect_type=? ORDER BY id DESC LIMIT ?"),
                (status, defect, limit),
            )
        else:
            cur.execute(
                adapt_sql("SELECT * FROM cases WHERE status=? ORDER BY id DESC LIMIT ?"),
                (status, limit),
            )
        return _rows(cur)
    finally:
        conn.close()
