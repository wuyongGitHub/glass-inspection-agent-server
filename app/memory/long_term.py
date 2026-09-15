"""长期业务记忆：厂家历史异常、产线风险、已确认案例、用户偏好。

记忆必须可追溯（带 updated_at）、可删除、可人工修正。
存储于 business_memory 表，memory_type + subject 唯一（upsert 覆盖）。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from app.db.session import adapt_sql, get_conn

# 记忆类型（受控词表）
MEMORY_TYPES = ("factory_risk", "line_risk", "confirmed_case", "user_preference")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rows(cur) -> list[dict]:
    fetched = cur.fetchall()
    if not fetched:
        return []
    first = fetched[0]
    if isinstance(first, dict):
        return [dict(r) for r in fetched]
    if cur.description and not hasattr(first, "keys"):
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in fetched]
    return [dict(r) for r in fetched]


def remember(memory_type: str, subject: str, payload: dict) -> None:
    """写入/更新一条长期记忆（同 type+subject 覆盖，updated_at 保留可追溯）。"""
    if memory_type not in MEMORY_TYPES:
        raise ValueError(f"未知记忆类型 {memory_type}，可选：{MEMORY_TYPES}")
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            adapt_sql("SELECT id FROM business_memory WHERE memory_type=? AND subject=?"),
            (memory_type, subject),
        )
        row = cur.fetchone()
        if row:
            mid = row["id"] if isinstance(row, dict) else row[0]
            cur.execute(
                adapt_sql("UPDATE business_memory SET payload=?, updated_at=? WHERE id=?"),
                (json.dumps(payload, ensure_ascii=False), _now(), mid),
            )
        else:
            cur.execute(
                adapt_sql(
                    "INSERT INTO business_memory (memory_type, subject, payload, updated_at) "
                    "VALUES (?,?,?,?)"
                ),
                (memory_type, subject, json.dumps(payload, ensure_ascii=False), _now()),
            )
        conn.commit()
    finally:
        conn.close()


def recall(memory_type: Optional[str] = None, subject: Optional[str] = None,
           limit: int = 50) -> list[dict]:
    """检索记忆：按类型/主体过滤，payload 已反序列化为 dict。"""
    conn = get_conn()
    cur = conn.cursor()
    try:
        sql = "SELECT * FROM business_memory WHERE 1=1"
        args: list = []
        if memory_type:
            sql += " AND memory_type=?"
            args.append(memory_type)
        if subject:
            sql += " AND subject LIKE ?"
            args.append(f"%{subject}%")
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(limit)
        cur.execute(adapt_sql(sql), args)
        rows = _rows(cur)
        for r in rows:
            try:
                r["payload"] = json.loads(r.get("payload") or "{}")
            except (TypeError, ValueError):
                r["payload"] = {}
        return rows
    finally:
        conn.close()


def forget(memory_id: Optional[int] = None, memory_type: Optional[str] = None,
           subject: Optional[str] = None) -> int:
    """删除记忆（可人工修正）。返回删除条数。"""
    conn = get_conn()
    cur = conn.cursor()
    try:
        if memory_id:
            cur.execute(adapt_sql("DELETE FROM business_memory WHERE id=?"), (memory_id,))
        elif memory_type and subject:
            cur.execute(
                adapt_sql("DELETE FROM business_memory WHERE memory_type=? AND subject=?"),
                (memory_type, subject),
            )
        elif memory_type:
            cur.execute(adapt_sql("DELETE FROM business_memory WHERE memory_type=?"),
                        (memory_type,))
        else:
            return 0
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def recall_factory_risks() -> list[dict]:
    """快捷方法：读取所有厂家风险记忆（供诊断时参考历史异常）。"""
    return recall(memory_type="factory_risk")


def recall_line_risks() -> list[dict]:
    """快捷方法：读取所有产线风险记忆。"""
    return recall(memory_type="line_risk")
