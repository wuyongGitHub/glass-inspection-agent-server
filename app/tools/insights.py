"""主动洞察计算：从查询结果中提取可解释的信号，供 data_analyze 生成更有价值的分析。

所有计算均为确定性函数（不依赖 LLM），产出结构化洞察文本，再交由 LLM 润色整合，
保证数字准确、可复现。
"""
from __future__ import annotations

import statistics

from app.tools.evidence import Evidence, fact
from app.tools.spc import spc_evidence
from app.tools.statistics import pareto, trend_drift


def _pct_diff(cur: float, prev: float) -> float | None:
    """环比变化率（%），prev<=0 时无法计算返回 None。"""
    if prev <= 0:
        return None
    return round((cur - prev) / prev * 100, 1)


def detect_trend_spike(rows: list[dict]) -> list[str]:
    """趋势序列的突变检测：检出率显著高于均值 + 标准差阈值的天。

    阈值取「均值 + 2σ」与「均值 * 1.5」两者中的较小值，
    使小样本/平稳序列下也能捕捉到相对显著的上跳。
    """
    if len(rows) < 3:
        return []
    rates = [float(r["defect_rate_pct"]) for r in rows if r.get("defect_rate_pct") is not None]
    if len(rates) < 3:
        return []
    mean = statistics.mean(rates)
    stdev = statistics.pstdev(rates)
    if mean <= 0:
        return []
    threshold = mean + 2 * stdev if stdev > 0 else float("inf")
    threshold = min(threshold, mean * 1.5)
    spikes = []
    for r in rows:
        v = r.get("defect_rate_pct")
        if v is not None and float(v) >= threshold:
            spikes.append(f"{r.get('day', '')} 检出率达 {v}%，显著高于均值 {round(mean, 2)}%")
    return spikes[:5]


def detect_factory_outlier(rows: list[dict]) -> list[str]:
    """厂家对比中的离群点：检出率显著高于同批其它厂家。

    阈值取「均值 + 1.5σ」与「均值 * 1.3」较小值，兼容小样本（仅 2~3 家）场景。
    """
    if len(rows) < 2:
        return []
    rates = [float(r["defect_rate_pct"]) for r in rows if r.get("defect_rate_pct") is not None]
    if len(rates) < 2:
        return []
    mean = statistics.mean(rates)
    if mean <= 0:
        return []
    stdev = statistics.pstdev(rates)
    threshold = mean + 1.5 * stdev if stdev > 0 else float("inf")
    threshold = min(threshold, mean * 1.3)
    out = []
    for r in rows:
        v = r.get("defect_rate_pct")
        if v is not None and float(v) >= threshold:
            out.append(
                f"{r.get('factory', '')}（{r.get('glass_type', '')}）检出率 {v}%，"
                f"高于均值 {round(mean, 2)}%"
            )
    return out[:3]


def detect_group_outlier(rows: list[dict], key: str, label: str) -> list[str]:
    """通用分组离群检测（产线/班次）：检出率显著高于同批其它分组的项。"""
    if len(rows) < 2:
        return []
    rates = [float(r["defect_rate_pct"]) for r in rows if r.get("defect_rate_pct") is not None]
    if len(rates) < 2:
        return []
    mean = statistics.mean(rates)
    if mean <= 0:
        return []
    stdev = statistics.pstdev(rates)
    threshold = mean + 1.5 * stdev if stdev > 0 else float("inf")
    threshold = min(threshold, mean * 1.3)
    out = []
    for r in rows:
        v = r.get("defect_rate_pct")
        if v is not None and float(v) >= threshold:
            out.append(
                f"{label}{r.get(key, '')} 检出率 {v}%，高于均值 {round(mean, 2)}%"
            )
    return out[:3]


def severity_summary(rows: list[dict]) -> str:
    """严重度分布摘要：各类占比，重点提示严重缺陷比例。"""
    if not rows:
        return ""
    total = sum(int(r.get("count", 0)) for r in rows)
    if total <= 0:
        return ""
    by = {str(r.get("severity") or "未知"): int(r.get("count", 0)) for r in rows}
    parts = [f"{k} {v} 件（{round(v * 100.0 / total, 1)}%）" for k, v in by.items()]
    severe = by.get("严重", 0)
    flag = ""
    if severe > 0:
        flag = f"；其中「严重」缺陷 {severe} 件（占 {round(severe * 100.0 / total, 1)}%），需优先处置"
    return "；".join(parts) + flag


def equipment_param_signals(rows: list[dict]) -> list[str]:
    """设备参数趋势信号：每个 (设备·参数) 序列的首尾变化与波动幅度。"""
    from collections import defaultdict
    series: dict[tuple, list[tuple[str, float]]] = defaultdict(list)
    for r in rows:
        key = (r.get("equipment", "设备"), r.get("param_name", "参数"))
        try:
            series[key].append((str(r.get("day", "")), float(r.get("value") or 0)))
        except (TypeError, ValueError):
            continue
    out = []
    for (eq, pn), pts in series.items():
        pts.sort(key=lambda x: x[0])
        if len(pts) < 2:
            continue
        vals = [v for _, v in pts]
        first, last = vals[0], vals[-1]
        d = _pct_diff(last, first)
        if d is None or abs(d) < 5:
            continue
        out.append(f"{eq}·{pn} 从 {first} 变为 {last}（{'上升' if d >= 0 else '下降'} {abs(d)}%）")
    return out[:4]


def top_defect_signals(rows: list[dict]) -> str:
    """缺陷排行摘要：Top 缺陷及占比，用于根因归因。"""
    if not rows:
        return ""
    total = sum(int(r.get("count", 0)) for r in rows)
    if total <= 0:
        return ""
    lines = []
    for r in rows[:3]:
        c = int(r.get("count", 0))
        share = round(c * 100.0 / total, 1)
        lines.append(f"{r.get('defect_type', '')} {c} 件（占 {share}%）")
    return "；".join(lines)


def build_insights(metric: str, rows: list[dict]) -> str:
    """根据指标类型产出结构化洞察文本块，供分析提示词注入。"""
    parts: list[str] = []

    if metric in ("defect_rate_trend", "defect_count_trend", "daily_volume"):
        spikes = detect_trend_spike(rows)
        if spikes:
            parts.append("【趋势突变】" + "；".join(spikes))
        # 首尾环比
        if len(rows) >= 2:
            first = float(rows[0].get("defect_rate_pct") or 0)
            last = float(rows[-1].get("defect_rate_pct") or 0)
            d = _pct_diff(last, first)
            if d is not None:
                parts.append(f"【期初→期末环比】检出率从 {first}% 变为 {last}%（{'上升' if d >= 0 else '下降'} {abs(d)}%）")

    elif metric == "overview":
        # rows 为 sections：overview / defect_rate_trend
        for sec in rows:
            if sec.get("section") == "defect_rate_trend":
                trend = sec.get("data") or []
                spikes = detect_trend_spike(trend)
                if spikes:
                    parts.append("【趋势突变】" + "；".join(spikes))

    elif metric == "rate_by_factory":
        out = detect_factory_outlier(rows)
        if out:
            parts.append("【厂家离群】" + "；".join(out))

    elif metric == "top_defects":
        sig = top_defect_signals(rows)
        if sig:
            parts.append(f"【缺陷构成】Top 缺陷：{sig}")

    elif metric == "multi":
        for sec in rows:
            s = sec.get("section")
            data = sec.get("data") or []
            if s == "rate_by_factory":
                out = detect_factory_outlier(data)
                if out:
                    parts.append("【厂家离群】" + "；".join(out))
            elif s == "top_defects":
                sig = top_defect_signals(data)
                if sig:
                    parts.append(f"【缺陷构成】{sig}")
            elif s == "defect_rate_trend":
                spikes = detect_trend_spike(data)
                if spikes:
                    parts.append("【趋势突变】" + "；".join(spikes))

    elif metric == "rate_by_line":
        out = detect_group_outlier(rows, "line_no", "产线 ")
        if out:
            parts.append("【产线离群】" + "；".join(out))

    elif metric == "rate_by_shift":
        out = detect_group_outlier(rows, "shift", "班次 ")
        if out:
            parts.append("【班次差异】" + "；".join(out))

    elif metric == "severity_distribution":
        sig = severity_summary(rows)
        if sig:
            parts.append(f"【严重度分布】{sig}")

    elif metric == "equipment_params":
        sigs = equipment_param_signals(rows)
        if sigs:
            parts.append("【设备参数波动】" + "；".join(sigs))

    return "\n".join(parts)


def _to_sections(rows: list[dict]) -> dict[str, list[dict]]:
    """把 sections 结构或扁平结构统一为 {section_name: data}。"""
    if rows and all(isinstance(r, dict) and r.get("section") for r in rows):
        return {sec["section"]: sec.get("data") or [] for sec in rows}
    return {"_flat": rows}


def build_evidence(metric: str, rows: list[dict]) -> list[Evidence]:
    """把查询结果转成结构化 Evidence 列表（V2 Evidence Engine）。

    与 build_insights（纯文本，供报告注入）不同，本函数产出带
    metric/value/baseline/change/source/confidence/calculation_method 的证据对象，
    供诊断流 evidence 字段直接消费，保证数字可追溯。
    """
    evidence: list[Evidence] = []
    sections = _to_sections(rows)

    # 趋势漂移 + SPC 受控性 + 突变点
    trend = (sections.get("defect_rate_trend")
             or sections.get("defect_count_trend")
             or sections.get("daily_volume")
             or sections.get("_flat"))
    if trend:
        evidence.append(trend_drift(trend, "defect_rate_pct"))
        rates = [float(r.get("defect_rate_pct") or 0)
                 for r in trend if r.get("defect_rate_pct") is not None]
        if rates:
            evidence.append(spc_evidence(rates, "defect_rate_pct"))
        for s in detect_trend_spike(trend):
            evidence.append(fact(s, source="统计规则", confidence=0.85,
                                 calculation_method="均值+2σ 突变检测"))

    # 厂家离群
    rf = sections.get("rate_by_factory") or []
    for s in detect_factory_outlier(rf):
        evidence.append(fact(s, source="统计规则", confidence=0.8,
                             calculation_method="均值+1.5σ 离群检测"))

    # 缺陷构成：帕累托关键少数
    td = sections.get("top_defects") or []
    p = pareto([{"name": r.get("defect_type"), "count": r.get("count")} for r in td])
    if p:
        top = p[0]
        vital = [x for x in p if x["is_vital_few"]]
        evidence.append(fact(
            f"主要缺陷为「{top['name']}」占比 {top['pct']}%；"
            f"关键少数 {len(vital)} 类缺陷累计占 {vital[-1]['cumulative_pct']}%",
            metric="defect_count", value=top["count"],
            source="SQL+统计规则", confidence=0.9,
            calculation_method="帕累托 80/20 分析",
        ))

    # 产线 / 班次离群
    rl = sections.get("rate_by_line") or []
    for s in detect_group_outlier(rl, "line_no", "产线 "):
        evidence.append(fact(s, source="统计规则", confidence=0.8,
                             calculation_method="均值+1.5σ 离群检测"))
    rs = sections.get("rate_by_shift") or []
    for s in detect_group_outlier(rs, "shift", "班次 "):
        evidence.append(fact(s, source="统计规则", confidence=0.8,
                             calculation_method="均值+1.5σ 离群检测"))

    # 严重度分布
    sd = sections.get("severity_distribution") or []
    ss = severity_summary(sd)
    if ss:
        evidence.append(fact(ss, metric="severity", source="SQL+统计规则",
                             confidence=0.9, calculation_method="严重度占比分布"))

    # 设备参数波动
    ep = sections.get("equipment_params") or []
    for s in equipment_param_signals(ep):
        evidence.append(fact(s, metric="equipment_param", source="设备参数时序",
                             confidence=0.7, calculation_method="首尾环比 + 波动幅度"))

    return evidence
