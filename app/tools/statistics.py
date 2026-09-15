"""工业统计分析工具：Pareto / 均值标准差 / 异常点 / 趋势漂移 / 环比同比 / 贡献度 / 严重度分布 / 相关性。

V2 目标：LLM 只负责组织推理，所有数学与统计结果由本模块的确定性代码产生，
并以 Evidence 对象返回，保证数字可追溯、可复核、不幻觉。
"""
from __future__ import annotations

import math
from statistics import mean, stdev
from typing import Optional

from app.tools.evidence import Evidence, fact


def _safe_float(v) -> Optional[float]:
    """把 DB Decimal / 字符串 / None 归一为 float，失败返回 None。"""
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def mean_std(values) -> dict:
    """均值 + 样本标准差（空/单元素时退化处理）。"""
    vals = [v for v in (_safe_float(x) for x in values) if v is not None]
    if not vals:
        return {"mean": None, "std": None, "n": 0}
    if len(vals) == 1:
        return {"mean": round(vals[0], 4), "std": 0.0, "n": 1}
    return {"mean": round(mean(vals), 4), "std": round(stdev(vals), 4), "n": len(vals)}


def pareto(items: list[dict], name_key: str = "name", value_key: str = "count") -> list[dict]:
    """帕累托分析：按 value 降序，计算单项占比与累计占比，并标记"关键少数"。

    返回带 cumulative_pct / is_vital_few 的结果，供"80/20"式缺陷排行与贡献度呈现。
    """
    total = sum(_safe_float(i.get(value_key)) or 0 for i in items)
    if total <= 0:
        return []
    sorted_items = sorted(items, key=lambda x: _safe_float(x.get(value_key)) or 0, reverse=True)
    cum = 0.0
    out = []
    for i in sorted_items:
        v = _safe_float(i.get(value_key)) or 0
        cum += v
        out.append({
            name_key: i.get(name_key),
            value_key: v,
            "pct": round(v / total * 100, 2),
            "cumulative_pct": round(cum / total * 100, 2),
        })
    # 关键少数：累计占比首次达到 80% 的项（含越过 80% 的那一项）
    vital = 1
    for idx, o in enumerate(out):
        vital = idx + 1
        if o["cumulative_pct"] >= 80.0:
            break
    for idx, o in enumerate(out):
        o["is_vital_few"] = idx < vital
    return out


def anomaly_points(values, threshold: float = 3.0) -> list[dict]:
    """Z-score 异常点检测：|z| > threshold 判为异常。"""
    vals = [_safe_float(x) for x in values]
    ms = mean_std([v for v in vals if v is not None])
    m, s = ms["mean"], ms["std"]
    if m is None or not s:
        return []
    out = []
    for idx, v in enumerate(vals):
        if v is None:
            continue
        z = (v - m) / s
        if abs(z) > threshold:
            out.append({"index": idx, "value": v, "z_score": round(z, 3)})
    return out


def trend_drift(series: list[dict], value_key: str = "defect_rate_pct",
                day_key: str = "day") -> Evidence:
    """趋势漂移：用首尾各 1/3 窗口均值对比，给出漂移方向与幅度。

    series 为按时间升序的 list[dict]，默认取 defect_rate_pct 字段。
    """
    vals = [v for v in (_safe_float(x.get(value_key)) for x in series) if v is not None]
    if len(vals) < 4:
        return fact("数据点不足，无法判断趋势漂移", metric=value_key,
                    source="统计规则", confidence=0.0)
    n = max(1, len(vals) // 3)
    head = mean(vals[:n])
    tail = mean(vals[-n:])
    change = round((tail - head) / head * 100, 2) if head else 0.0
    if abs(change) < 5:
        return fact("近期检出率与前期基本持平，无明显漂移",
                    metric=value_key, value=round(tail, 4), baseline=round(head, 4),
                    change=change, source="统计规则", confidence=0.8,
                    calculation_method="首尾各 1/3 窗口均值对比")
    direction = "上升" if change > 0 else "下降"
    return fact(f"检出率呈明显{direction}趋势",
                metric=value_key, value=round(tail, 4), baseline=round(head, 4),
                change=change, source="统计规则", confidence=0.85,
                calculation_method="首尾各 1/3 窗口均值对比")


def pct_change(series: list[dict], value_key: str = "defect_rate_pct",
               day_key: str = "day") -> list[dict]:
    """环比变化率：相邻周期 (当前-上一)/上一 * 100，供逐点变化定位。"""
    out = []
    for i in range(1, len(series)):
        prev = _safe_float(series[i - 1].get(value_key))
        cur = _safe_float(series[i].get(value_key))
        if prev in (None, 0) or cur is None:
            out.append({day_key: series[i].get(day_key), "pct_change": None})
        else:
            out.append({day_key: series[i].get(day_key),
                        "pct_change": round((cur - prev) / prev * 100, 2)})
    return out


def contribution(items: list[dict], name_key: str = "name",
                 value_key: str = "count") -> list[dict]:
    """贡献度：各维度（厂家/产线/缺陷）占总量的百分比，降序返回。"""
    total = sum(_safe_float(i.get(value_key)) or 0 for i in items)
    if total <= 0:
        return []
    out = []
    for i in items:
        v = _safe_float(i.get(value_key)) or 0
        out.append({name_key: i.get(name_key), value_key: v,
                    "share_pct": round(v / total * 100, 2)})
    out.sort(key=lambda x: x["share_pct"], reverse=True)
    return out


def severity_distribution(rows: list[dict]) -> list[dict]:
    """严重度分布：按 severity 聚合 count（输入 defects 行，含 severity/count 字段）。"""
    agg: dict[str, float] = {}
    for r in rows:
        sev = r.get("severity") or "未知"
        agg[sev] = agg.get(sev, 0.0) + (_safe_float(r.get("count")) or 0)
    total = sum(agg.values()) or 1.0
    return [{"severity": k, "count": v, "share_pct": round(v / total * 100, 2)}
            for k, v in sorted(agg.items(), key=lambda kv: -kv[1])]


def correlation(xs, ys) -> Optional[float]:
    """皮尔逊相关系数（只表达相关性，不表达因果）。"""
    x = [_safe_float(v) for v in xs]
    y = [_safe_float(v) for v in ys]
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    if len(pairs) < 2:
        return None
    mx = mean(a for a, _ in pairs)
    my = mean(b for _, b in pairs)
    cov = sum((a - mx) * (b - my) for a, b in pairs)
    sx = math.sqrt(sum((a - mx) ** 2 for a, _ in pairs))
    sy = math.sqrt(sum((b - my) ** 2 for _, b in pairs))
    if sx == 0 or sy == 0:
        return None
    return round(cov / (sx * sy), 4)
