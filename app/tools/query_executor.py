"""安全查询执行器：只允许预定义指标 + SQL 参数绑定，杜绝注入与越权。

LLM 永远不直接生成 SQL —— 它只负责把自然语言解析成 QueryParams，
这里的白名单查询负责拼装。新增指标时在此处加一个分支并同步
app/domain.py 的 METRICS 与 app/graph/nodes/data.py 的 QueryParams。
"""
from decimal import Decimal

from app.db.session import adapt_sql, get_conn


def _to_jsonable(v):
    """把 MySQL Decimal 等非 JSON 原生类型转为 float/int，保证下游可序列化。"""
    if isinstance(v, Decimal):
        return float(v)
    return v


def _rows(cur) -> list[dict]:
    """把 cursor 结果转成 list[dict]，兼容 SQLite Row 与 MySQL DictCursor。

    - MySQL DictCursor：fetchall() 已返回 dict，直接使用。
    - SQLite sqlite3.Row：支持 dict(r) 转换。
    并对 MySQL 的 Decimal 值做 float 归一，保证 JSON 序列化 / 图表渲染正常。
    """
    fetched = cur.fetchall()
    if not fetched:
        return []
    first = fetched[0]
    if isinstance(first, dict):
        return [{k: _to_jsonable(v) for k, v in row.items()} for row in fetched]
    # sqlite3.Row / 元组：优先用 dict(r)（Row 支持），元组则按列名映射
    if cur.description and not hasattr(first, "keys"):
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in fetched]
    return [dict(r) for r in fetched]


def execute_query(p: dict) -> dict:
    metric = p.get("metric") or "overview"
    start = p.get("start_date") or "2000-01-01"
    end = p.get("end_date") or "2100-12-31"
    factory = p.get("factory")
    glass_type = p.get("glass_type")
    defect_type = p.get("defect_type")
    line_no = p.get("line_no")
    shift = p.get("shift")
    severity = p.get("severity")
    equipment = p.get("equipment")
    param_name = p.get("param_name")

    conn = get_conn()
    # SQLite 需要 row_factory=Row 才能 dict(r) 转 dict；MySQL DictCursor 已返回 dict
    if hasattr(conn, "row_factory"):
        import sqlite3

        conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    def run(sql: str, sql_args: list):
        """执行查询并适配占位符（SQLite ? / MySQL %s）。"""
        cur.execute(adapt_sql(sql), sql_args)

    cond = ["date(r.inspected_at) >= ?", "date(r.inspected_at) <= ?"]
    args: list = [start, end]
    if factory:
        cond.append("f.name LIKE ?")
        args.append(f"%{factory}%")
    if glass_type:
        cond.append("r.glass_type = ?")
        args.append(glass_type)
    if line_no:
        cond.append("r.line_no = ?")
        args.append(line_no)
    if shift:
        cond.append("r.shift = ?")
        args.append(shift)
    where = " AND ".join(cond)
    base = f"FROM inspection_records r JOIN factories f ON f.id = r.factory_id WHERE {where}"

    def _defect_cond(base_cond: list[str], base_args: list) -> tuple[str, list]:
        """基于 defects 表的查询条件：在基础条件上追加缺陷类型/严重度过滤。"""
        dcond = list(base_cond)
        dargs = list(base_args)
        if defect_type:
            dcond.append("d.defect_type = ?")
            dargs.append(defect_type)
        if severity:
            dcond.append("d.severity = ?")
            dargs.append(severity)
        return " AND ".join(dcond), dargs

    try:
        if metric == "overview":
            run(
                f"SELECT COUNT(*) AS records, COALESCE(SUM(r.total_count),0) AS total, "
                f"COALESCE(SUM(r.defect_count),0) AS defects, "
                f"ROUND(COALESCE(SUM(r.defect_count),0)*100.0/NULLIF(SUM(r.total_count),0),2) AS defect_rate_pct "
                f"{base}",
                args,
            )
            overview = _rows(cur)
            # 附加按天趋势段，让"概览"也能出图（KPI + 趋势折线）
            run(
                f"SELECT date(r.inspected_at) AS day, SUM(r.total_count) AS total, "
                f"SUM(r.defect_count) AS defects, "
                f"ROUND(SUM(r.defect_count)*100.0/NULLIF(SUM(r.total_count),0),2) AS defect_rate_pct "
                f"{base} GROUP BY day ORDER BY day",
                args,
            )
            trend = _rows(cur)
            rows = [
                {"section": "overview", "data": overview},
                {"section": "defect_rate_trend", "data": trend},
            ]
        elif metric in ("defect_rate_trend", "defect_count_trend", "daily_volume"):
            # 三者共用同一组按天字段，仅呈现侧重不同（检出率/缺陷量/检测量）
            run(
                f"SELECT date(r.inspected_at) AS day, SUM(r.total_count) AS total, "
                f"SUM(r.defect_count) AS defects, "
                f"ROUND(SUM(r.defect_count)*100.0/NULLIF(SUM(r.total_count),0),2) AS defect_rate_pct "
                f"{base} GROUP BY day ORDER BY day",
                args,
            )
            rows = _rows(cur)
        elif metric == "rate_by_factory":
            run(
                f"SELECT f.name AS factory, r.glass_type AS glass_type, SUM(r.total_count) AS total, "
                f"SUM(r.defect_count) AS defects, "
                f"ROUND(SUM(r.defect_count)*100.0/NULLIF(SUM(r.total_count),0),2) AS defect_rate_pct "
                f"{base} GROUP BY f.name, r.glass_type ORDER BY defect_rate_pct DESC",
                args,
            )
            rows = _rows(cur)
        elif metric == "top_defects":
            dwhere, dargs = _defect_cond(cond, args)
            run(
                f"SELECT d.defect_type AS defect_type, SUM(d.count) AS count "
                f"FROM defects d JOIN inspection_records r ON d.record_id = r.id "
                f"JOIN factories f ON f.id = r.factory_id "
                f"WHERE {dwhere} GROUP BY d.defect_type ORDER BY count DESC LIMIT 10",
                dargs,
            )
            rows = _rows(cur)
        elif metric == "factory_composition":
            # 厂家 x 缺陷类型构成矩阵：供堆叠柱 / 热力图渲染
            dwhere, dargs = _defect_cond(cond, args)
            run(
                f"SELECT f.name AS factory, d.defect_type AS defect_type, SUM(d.count) AS count "
                f"FROM defects d JOIN inspection_records r ON d.record_id = r.id "
                f"JOIN factories f ON f.id = r.factory_id "
                f"WHERE {dwhere} GROUP BY f.name, d.defect_type ORDER BY count DESC",
                dargs,
            )
            rows = _rows(cur)
        elif metric == "rate_by_line":
            # 按产线对比检出率（产线为 null 的脏数据用"未知"归并）
            run(
                f"SELECT COALESCE(r.line_no,'未知') AS line_no, SUM(r.total_count) AS total, "
                f"SUM(r.defect_count) AS defects, "
                f"ROUND(SUM(r.defect_count)*100.0/NULLIF(SUM(r.total_count),0),2) AS defect_rate_pct "
                f"{base} GROUP BY r.line_no ORDER BY defect_rate_pct DESC",
                args,
            )
            rows = _rows(cur)
        elif metric == "rate_by_shift":
            # 按班次对比检出率
            run(
                f"SELECT COALESCE(r.shift,'未知') AS shift, SUM(r.total_count) AS total, "
                f"SUM(r.defect_count) AS defects, "
                f"ROUND(SUM(r.defect_count)*100.0/NULLIF(SUM(r.total_count),0),2) AS defect_rate_pct "
                f"{base} GROUP BY r.shift ORDER BY defect_rate_pct DESC",
                args,
            )
            rows = _rows(cur)
        elif metric == "severity_distribution":
            # 缺陷严重度分布（轻微/一般/严重）
            dwhere, dargs = _defect_cond(cond, args)
            run(
                f"SELECT COALESCE(d.severity,'未知') AS severity, SUM(d.count) AS count "
                f"FROM defects d JOIN inspection_records r ON d.record_id = r.id "
                f"JOIN factories f ON f.id = r.factory_id "
                f"WHERE {dwhere} GROUP BY d.severity ORDER BY count DESC",
                dargs,
            )
            rows = _rows(cur)
        elif metric == "equipment_params":
            # 设备参数时间序列：温度/光源亮度等（设备与产线/厂家关联）
            eq_cond = ["date(p.recorded_at) >= ?", "date(p.recorded_at) <= ?"]
            eq_args: list = [start, end]
            if equipment:
                eq_cond.append("e.name LIKE ?")
                eq_args.append(f"%{equipment}%")
            if param_name:
                eq_cond.append("p.param_name LIKE ?")
                eq_args.append(f"%{param_name}%")
            eq_where = " AND ".join(eq_cond)
            run(
                f"SELECT e.name AS equipment, e.equipment_type AS equipment_type, "
                f"p.param_name AS param_name, AVG(p.param_value) AS value, "
                f"date(p.recorded_at) AS day "
                f"FROM equipment_params p JOIN equipment e ON e.id = p.equipment_id "
                f"WHERE {eq_where} GROUP BY e.name, p.param_name, day ORDER BY day",
                eq_args,
            )
            rows = _rows(cur)
        elif metric == "multi":
            # 综合分析：一次返回总体概览 + 厂家对比 + 缺陷构成，供分析节点综合洞察
            run(
                f"SELECT COUNT(*) AS records, COALESCE(SUM(r.total_count),0) AS total, "
                f"COALESCE(SUM(r.defect_count),0) AS defects, "
                f"ROUND(COALESCE(SUM(r.defect_count),0)*100.0/NULLIF(SUM(r.total_count),0),2) AS defect_rate_pct "
                f"{base}",
                args,
            )
            overview = _rows(cur)
            run(
                f"SELECT f.name AS factory, r.glass_type AS glass_type, SUM(r.total_count) AS total, "
                f"SUM(r.defect_count) AS defects, "
                f"ROUND(SUM(r.defect_count)*100.0/NULLIF(SUM(r.total_count),0),2) AS defect_rate_pct "
                f"{base} GROUP BY f.name, r.glass_type ORDER BY defect_rate_pct DESC",
                args,
            )
            factories = _rows(cur)
            dwhere, dargs = _defect_cond(cond, args)
            run(
                f"SELECT d.defect_type AS defect_type, SUM(d.count) AS count "
                f"FROM defects d JOIN inspection_records r ON d.record_id = r.id "
                f"JOIN factories f ON f.id = r.factory_id "
                f"WHERE {dwhere} GROUP BY d.defect_type ORDER BY count DESC LIMIT 10",
                dargs,
            )
            top = _rows(cur)
            # 附加按天趋势段，使综合分析看板可渲染趋势折线
            run(
                f"SELECT date(r.inspected_at) AS day, SUM(r.total_count) AS total, "
                f"SUM(r.defect_count) AS defects, "
                f"ROUND(SUM(r.defect_count)*100.0/NULLIF(SUM(r.total_count),0),2) AS defect_rate_pct "
                f"{base} GROUP BY day ORDER BY day",
                args,
            )
            trend = _rows(cur)
            rows = [
                {"section": "overview", "data": overview},
                {"section": "rate_by_factory", "data": factories},
                {"section": "top_defects", "data": top},
                {"section": "defect_rate_trend", "data": trend},
            ]
        else:
            raise ValueError(f"未知指标: {metric}")
    finally:
        conn.close()

    return {"metric": metric, "params": {k: v for k, v in p.items() if v}, "rows": rows}
