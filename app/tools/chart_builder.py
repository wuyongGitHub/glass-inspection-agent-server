"""图表配置生成：由代码确定性生成 ECharts option，供报表大屏直接渲染。

返回结构约定（前端据此分支）：
- 单图指标：返回普通 ECharts option，可直接 `setOption(chart)`。
- 看板类（overview / multi 未指定单一图型）：返回容器
  `{"layout": "dashboard", "title": ..., "kpis": [...], "charts": [{"option": ..., "span": 12}, ...]}`，
  前端循环渲染每个 `item["option"]` 即可。
  `span` 为版面宽度提示：12 = 整行展示；6 = 半行展示（可与另一张 span=6
  的子图并排一行，版面更紧凑自然）。兼容旧格式：charts 元素若为纯 option
  则按整行（span=12）处理。

所有颜色均为 JSON 可序列化字面量（纯色 / {type:"linear",colorStops} 渐变对象），
不用 JS 函数，保证经 FastAPI default=str 序列化后 ECharts 仍可识别。
"""
from __future__ import annotations

_PALETTE = [
    "#3B82F6", "#F59E0B", "#10B981", "#EF4444", "#8B5CF6",
    "#06B6D4", "#F97316", "#EC4899", "#84CC16", "#64748B",
]
_GRID = {"left": 60, "right": 28, "bottom": 52, "top": 52}


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _grad(color: str, a0: float = 0.85, a1: float = 0.15) -> dict:
    """从上到下由主色渐变到透明的线性渐变对象（JSON 兼容）。"""
    return {
        "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
        "colorStops": [
            {"offset": 0, "color": _rgba(color, a0)},
            {"offset": 1, "color": _rgba(color, a1)},
        ],
    }


def _base(title: str) -> dict:
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "axis"},
        "grid": _GRID,
        "color": _PALETTE,
    }


def _mean_markline(color: str = "#F59E0B") -> dict:
    return {
        "markLine": {
            "silent": True, "symbol": "none",
            "lineStyle": {"type": "dashed", "color": color, "width": 1},
            "label": {"formatter": "均值 {c}", "color": "#64748B", "fontSize": 11},
            "data": [{"type": "average", "name": "均值"}],
        }
    }


def _max_markpoint(color: str = "#EF4444") -> dict:
    return {
        "markPoint": {
            "symbol": "pin", "symbolSize": 42,
            "itemStyle": {"color": color},
            "label": {"formatter": "{c}", "color": "#fff", "fontSize": 10},
            "data": [{"type": "max", "name": "峰值"}],
        }
    }


def _kpis(row: dict) -> list[dict]:
    """概览行 -> 大屏顶部 KPI 卡数据。"""
    if not row:
        return []
    return [
        {"key": "records", "label": "检测记录", "value": row.get("records", 0), "unit": "条"},
        {"key": "total", "label": "检测总量", "value": row.get("total", 0), "unit": "片"},
        {"key": "defects", "label": "不良总数", "value": row.get("defects", 0), "unit": "件"},
        {"key": "rate", "label": "综合检出率", "value": row.get("defect_rate_pct"), "unit": "%"},
    ]


def _section(rows: list[dict], name: str) -> list[dict] | None:
    for r in rows:
        if isinstance(r, dict) and r.get("section") == name:
            data = r.get("data")
            return data if data else None
    return None


def _dashboard(title: str, kpis: list[dict], charts: list[dict]) -> dict:
    if not charts:
        return {"layout": "dashboard", "title": title, "kpis": kpis, "charts": []}
    return {"layout": "dashboard", "title": title, "kpis": kpis, "charts": charts}


def _pack(chart: dict | None, span: int = 12) -> dict | None:
    """包装看板子图：span=12 整行；span=6 半行（可两两并排）。"""
    if not chart:
        return None
    return {"option": chart, "span": span}


# --------------------------------------------------------------------------
# 入口调度：根据 metric / chart_type 产出单个 option 或 dashboard 容器
# --------------------------------------------------------------------------
_TREND_METRICS = ("defect_rate_trend", "defect_count_trend", "daily_volume")


def build_chart_option(metric: str, rows: list[dict], params: dict | None = None) -> dict | None:
    if not rows:
        return None
    ct = (params or {}).get("chart_type") or "auto"

    if metric == "overview":
        return _overview_dashboard(rows, ct)
    if metric == "multi":
        return _multi_dashboard(rows, ct)
    if metric in _TREND_METRICS:
        return _day_chart(metric, rows, ct)
    if metric == "rate_by_factory":
        if ct == "radar":
            return _radar(rows)
        return _factory(rows, ct)
    if metric == "top_defects":
        if ct == "funnel":
            return _funnel(rows)
        if ct == "rose":
            return _rose(rows)
        return _defects(rows, ct)
    if metric == "factory_composition":
        if ct == "sunburst":
            return _sunburst(rows)
        if ct == "treemap":
            return _treemap(rows)
        return _composition(rows, ct)
    if metric == "rate_by_line":
        return _line_compare(rows, ct)
    if metric == "rate_by_shift":
        return _shift_compare(rows, ct)
    if metric == "severity_distribution":
        return _severity(rows, ct)
    if metric == "equipment_params":
        return _equipment_params(rows)
    return None


# --------------------------------------------------------------------------
# 看板类：overview（KPI + 趋势）与 multi（KPI + 趋势 + 厂家 + 缺陷构成）
# --------------------------------------------------------------------------
def _overview_dashboard(rows: list[dict], ct: str) -> dict:
    ov = _section(rows, "overview")
    trend = _section(rows, "defect_rate_trend") or _section(rows, "trend")
    if not ov and not any(isinstance(r, dict) and "section" in r for r in rows):
        ov, trend = rows[:1], None  # 兼容旧平铺结构（只有一行概览）
    kpis = _kpis(ov[0]) if ov else []
    if ct == "gauge":
        g = _gauge(ov[0]) if ov else None
        return g or {"layout": "dashboard", "title": "总体概览", "kpis": kpis, "charts": []}
    tr_chart = _rate_line(trend) if trend and len(trend) >= 2 else None
    if ct in ("pie", "bar", "heatmap", "stack"):  # 用户只想要某一单图时尽量给
        return tr_chart or {"layout": "dashboard", "title": "总体概览", "kpis": kpis, "charts": []}
    # 趋势线时间跨度大、信息密度高 -> 整行；其余小块交给前端自然排布
    return _dashboard("总体概览", kpis, [c for c in [_pack(tr_chart, 12)] if c])


def _multi_dashboard(rows: list[dict], ct: str) -> dict:
    ov = _section(rows, "overview")
    fac = _section(rows, "rate_by_factory")
    top = _section(rows, "top_defects")
    trend = _section(rows, "defect_rate_trend")
    kpis = _kpis(ov[0]) if ov else []

    def _first(*charts):  # 返回第一张非空图
        for c in charts:
            if c:
                return c
        return None

    if ct == "bar":
        return _first(_factory(fac, "bar"), _defects(top, "bar"), _rate_line(trend))
    if ct == "pie":
        return _first(_defects(top, "pie"), _defect_pie(top), _factory(fac, "bar"))
    if ct == "line":
        return _first(_rate_line(trend), _factory(fac, "line"), _defects(top, "bar"))
    if ct == "gauge":
        return _first(_gauge(ov[0]) if ov else None, _factory(fac, "bar"))
    if ct == "radar":
        return _first(_radar(fac), _factory(fac, "bar"))
    if ct == "rose":
        return _first(_rose(top), _defects(top, "pie"))
    if ct == "funnel":
        return _first(_funnel(top), _defects(top, "bar"))
    if ct in ("heatmap", "stack"):  # multi 无该维度数据，退回柱/排行
        return _first(_factory(fac, "bar"), _defects(top, "bar"))

    # 大屏排布：时间趋势整行打底；饼/雷达/玫瑰等圆形小图半行两两并排；
    # 类别少的柱图/排行也可半行，类别多则退回整行保证可读
    charts = [
        _pack(_rate_line(trend), 12) if trend and len(trend) >= 2 else None,
        _pack(_radar(fac), 6),
        _pack(_factory(fac, "bar"), 6 if fac and len(fac) <= 5 else 12),
        _pack(_defects(top, "auto"), 6 if top and len(top) <= 7 else 12),
        _pack(_rose(top), 6),
    ]
    return _dashboard("综合分析看板", kpis, [c for c in charts if c])


# --------------------------------------------------------------------------
# 按天趋势类：rate 折线 / count 柱线 / volume 柱，支持 dual 双轴组合
# --------------------------------------------------------------------------
def _day_chart(metric: str, rows: list[dict], ct: str) -> dict | None:
    if ct == "dual" and any(r.get("total") for r in rows):
        return _dual(rows)
    if metric == "defect_rate_trend":
        return _rate_bar(rows) if ct == "bar" else _rate_line(rows)
    if metric == "defect_count_trend":
        return _defect_bar(rows) if ct in ("bar", "auto") else _defect_line(rows)
    if metric == "daily_volume":
        return _volume_line(rows) if ct == "line" else _volume_bar(rows)
    return None


def _days(rows: list[dict]) -> list:
    return [r["day"] for r in rows]


def _rate_line(rows: list[dict]) -> dict:
    if not rows:
        return None
    opt = _base("检出率趋势（%）")
    opt["xAxis"] = {"type": "category", "data": _days(rows), "axisLabel": {"rotate": 45}}
    opt["yAxis"] = {"type": "value", "name": "%", "scale": True}
    series = {
        "name": "检出率", "type": "line", "smooth": True, "symbolSize": 5,
        "data": [r["defect_rate_pct"] for r in rows],
        "lineStyle": {"width": 3, "color": _PALETTE[0]},
        "itemStyle": {"color": _PALETTE[0]},
        "areaStyle": {"color": _grad(_PALETTE[0])},
    }
    series.update(_mean_markline())
    series.update(_max_markpoint())
    opt["series"] = [series]
    return opt


def _rate_bar(rows: list[dict]) -> dict:
    opt = _base("检出率趋势（%）")
    opt["xAxis"] = {"type": "category", "data": _days(rows), "axisLabel": {"rotate": 45}}
    opt["yAxis"] = {"type": "value", "name": "%", "scale": True}
    series = {
        "name": "检出率", "type": "bar", "barMaxWidth": 22,
        "data": [r["defect_rate_pct"] for r in rows],
        "itemStyle": {"borderRadius": [4, 4, 0, 0], "color": _grad(_PALETTE[0])},
    }
    series.update(_mean_markline())
    opt["series"] = [series]
    return opt


def _defect_line(rows: list[dict]) -> dict:
    opt = _base("每日缺陷数量趋势（件）")
    opt["xAxis"] = {"type": "category", "data": _days(rows), "axisLabel": {"rotate": 45}}
    opt["yAxis"] = {"type": "value", "name": "件"}
    s = {
        "name": "缺陷数", "type": "line", "smooth": True,
        "data": [r["defects"] for r in rows],
        "lineStyle": {"width": 3, "color": _PALETTE[3]},
        "itemStyle": {"color": _PALETTE[3]},
        "areaStyle": {"color": _grad(_PALETTE[3])},
    }
    s.update(_mean_markline("#F59E0B"))
    opt["series"] = [s]
    return opt


def _defect_bar(rows: list[dict]) -> dict:
    opt = _base("每日缺陷数量趋势（件）")
    opt["xAxis"] = {"type": "category", "data": _days(rows), "axisLabel": {"rotate": 45}}
    opt["yAxis"] = {"type": "value", "name": "件"}
    s = {
        "name": "缺陷数", "type": "bar", "barMaxWidth": 22,
        "data": [r["defects"] for r in rows],
        "itemStyle": {"borderRadius": [4, 4, 0, 0], "color": _grad(_PALETTE[3])},
    }
    s.update(_mean_markline())
    opt["series"] = [s]
    return opt


def _volume_bar(rows: list[dict]) -> dict:
    opt = _base("每日检测量（片）")
    opt["xAxis"] = {"type": "category", "data": _days(rows), "axisLabel": {"rotate": 45}}
    opt["yAxis"] = {"type": "value", "name": "片"}
    opt["series"] = [{
        "name": "检测量", "type": "bar", "barMaxWidth": 22,
        "data": [r["total"] for r in rows],
        "itemStyle": {"borderRadius": [4, 4, 0, 0], "color": _grad(_PALETTE[5])},
    }]
    return opt


def _volume_line(rows: list[dict]) -> dict:
    opt = _base("每日检测量（片）")
    opt["xAxis"] = {"type": "category", "data": _days(rows), "axisLabel": {"rotate": 45}}
    opt["yAxis"] = {"type": "value", "name": "片"}
    s = {
        "name": "检测量", "type": "line", "smooth": True,
        "data": [r["total"] for r in rows],
        "lineStyle": {"width": 3, "color": _PALETTE[5]},
        "itemStyle": {"color": _PALETTE[5]},
        "areaStyle": {"color": _grad(_PALETTE[5])},
    }
    s.update(_max_markpoint("#3B82F6"))
    opt["series"] = [s]
    return opt


def _dual(rows: list[dict]) -> dict:
    """双轴组合图：左轴检测量柱 + 右轴检出率折线，一次看产能与质量。"""
    opt = _base("检测量与检出率（组合）")
    opt["xAxis"] = {"type": "category", "data": _days(rows), "axisLabel": {"rotate": 45}}
    opt["yAxis"] = [
        {"type": "value", "name": "片", "axisLine": {"lineStyle": {"color": "#94A3B8"}}},
        {"type": "value", "name": "%", "scale": True, "splitLine": {"show": False},
         "axisLine": {"lineStyle": {"color": "#F59E0B"}}},
    ]
    rate = [r["defect_rate_pct"] for r in rows]
    opt["series"] = [
        {"name": "检测量", "type": "bar", "barMaxWidth": 22, "yAxisIndex": 0,
         "data": [r["total"] for r in rows],
         "itemStyle": {"borderRadius": [4, 4, 0, 0], "color": _grad(_PALETTE[5], 0.7, 0.2)}},
        {"name": "检出率", "type": "line", "yAxisIndex": 1, "smooth": True,
         "data": rate, "lineStyle": {"width": 3, "color": _PALETTE[1]},
         "itemStyle": {"color": _PALETTE[1]}, "symbol": "circle", "symbolSize": 6},
    ]
    return opt


def _factory(rows: list[dict], ct: str) -> dict:
    if not rows:
        return None
    labels = [f'{r["factory"]}·{r["glass_type"]}' for r in rows]
    vals = [r["defect_rate_pct"] for r in rows]
    if ct == "line":
        opt = _base("各厂家检出率对比（%）")
        opt["xAxis"] = {"type": "category", "data": labels, "axisLabel": {"rotate": 30}}
        opt["yAxis"] = {"type": "value", "name": "%"}
        s = {"name": "检出率", "type": "line", "smooth": True, "data": vals,
             "lineStyle": {"width": 3, "color": _PALETTE[2]}, "itemStyle": {"color": _PALETTE[2]}}
        s.update(_max_markpoint(_PALETTE[2]))
        opt["series"] = [s]
        return opt
    opt = _base("各厂家检出率对比（%）")
    opt["xAxis"] = {"type": "category", "data": labels, "axisLabel": {"rotate": 30}}
    opt["yAxis"] = {"type": "value", "name": "%"}
    s = {
        "name": "检出率", "type": "bar", "barMaxWidth": 26,
        "data": vals,
        "itemStyle": {"borderRadius": [4, 4, 0, 0], "color": _grad(_PALETTE[0])},
    }
    s.update(_mean_markline("#F59E0B"))
    opt["series"] = [s]
    return opt


def _defects(rows: list[dict], ct: str) -> dict:
    if not rows:
        return None
    if ct == "pie":
        return _defect_pie(rows)
    rows = list(reversed(rows))  # 横向条形图：数量大的在上
    opt = _base("缺陷类型排行")
    opt["xAxis"] = {"type": "value"}
    opt["yAxis"] = {"type": "category", "data": [r["defect_type"] for r in rows]}
    data = []
    for i, r in enumerate(rows):
        color = _PALETTE[i % len(_PALETTE)]
        data.append({"value": r["count"],
                     "itemStyle": {"borderRadius": [0, 4, 4, 0], "color": _grad(color, 0.9, 0.55)}})
    opt["series"] = [{"name": "数量", "type": "bar", "barMaxWidth": 16, "data": data}]
    return opt


def _defect_pie(rows: list[dict]) -> dict:
    if not rows:
        return None
    data = [{"name": r["defect_type"], "value": r["count"]} for r in rows]
    return {
        "title": {"text": "缺陷类型构成", "left": "center", "textStyle": {"fontSize": 14}},
        "color": _PALETTE,
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} 件 ({d}%)"},
        "legend": {"orient": "vertical", "right": 8, "top": "middle", "type": "scroll"},
        "series": [{
            "name": "缺陷数量", "type": "pie",
            "radius": ["38%", "68%"], "center": ["42%", "55%"],
            "avoidLabelOverlap": True,
            "itemStyle": {"borderRadius": 6, "borderColor": "#fff", "borderWidth": 2},
            "label": {"formatter": "{b}\n{d}%"},
            "data": data,
        }],
    }


# --------------------------------------------------------------------------
# 厂家 x 缺陷构成：堆叠柱（占比直观）/ 热力图（全矩阵）
# --------------------------------------------------------------------------
def _composition(rows: list[dict], ct: str) -> dict:
    if ct == "heatmap":
        return _heatmap(rows)
    return _stack(rows)  # auto / stack 默认堆叠构成


def _type_rank(rows: list[dict]) -> list[str]:
    """按缺陷总量从大到小排序的缺陷类型名（截 Top 8 防止图过密）。"""
    agg: dict[str, int] = {}
    for r in rows:
        agg[r["defect_type"]] = agg.get(r["defect_type"], 0) + int(r.get("count", 0))
    return [k for k, _ in sorted(agg.items(), key=lambda kv: -kv[1])][:8]


def _factory_order(rows: list[dict]) -> list[str]:
    agg: dict[str, int] = {}
    for r in rows:
        agg[r["factory"]] = agg.get(r["factory"], 0) + int(r.get("count", 0))
    return [k for k, _ in sorted(agg.items(), key=lambda kv: -kv[1])]


def _stack(rows: list[dict]) -> dict:
    types = _type_rank(rows)
    if not types:
        return None
    facs = _factory_order(rows)
    index = {t: i for i, t in enumerate(types)}
    by_key: dict[tuple, int] = {}
    for r in rows:
        if r["defect_type"] in index:
            by_key[(r["factory"], r["defect_type"])] = int(r.get("count", 0))
    opt = _base("各厂家缺陷构成（堆叠）")
    opt["tooltip"] = {"trigger": "axis", "axisPointer": {"type": "shadow"}}
    opt["xAxis"] = {"type": "category", "data": facs}
    opt["yAxis"] = {"type": "value", "name": "件"}
    opt["legend"] = {"type": "scroll", "top": 30}
    series = []
    for i, t in enumerate(types):
        color = _PALETTE[i % len(_PALETTE)]
        series.append({"name": t, "type": "bar", "stack": "total", "barMaxWidth": 30,
                       "itemStyle": {"color": color},
                       "data": [by_key.get((f, t), 0) for f in facs]})
    opt["series"] = series
    return opt


def _heatmap(rows: list[dict]) -> dict:
    types = _type_rank(rows)
    facs = _factory_order(rows)
    if not types or not facs:
        return None
    fi, ti = {f: i for i, f in enumerate(facs)}, {t: i for i, t in enumerate(types)}
    grid = {(r["factory"], r["defect_type"]): int(r.get("count", 0)) for r in rows}
    cells = [[fi[f], ti[t], grid.get((f, t), 0)] for f in facs for t in types if grid.get((f, t))]
    vmax = max((c[2] for c in cells), default=1)
    opt = _base("各厂家缺陷分布（热力）")
    opt["tooltip"] = {"position": "top", "formatter": "{c} 件"}
    opt["xAxis"] = {"type": "category", "data": facs, "splitArea": {"show": True}}
    opt["yAxis"] = {"type": "category", "data": types, "splitArea": {"show": True}}
    opt["visualMap"] = {
        "min": 0, "max": vmax, "calculable": True, "orient": "horizontal",
        "left": "center", "bottom": 4, "textStyle": {"fontSize": 10},
        "inRange": {"color": ["#E2E8F0", "#93C5FD", "#3B82F6", "#1E3A8A"]},
    }
    opt["series"] = [{
        "name": "缺陷数", "type": "heatmap",
        "data": cells, "label": {"show": True, "fontSize": 10},
        "emphasis": {"itemStyle": {"shadowBlur": 8, "shadowColor": "rgba(0,0,0,0.4)"}},
    }]
    return opt


# --------------------------------------------------------------------------
# 进阶图表：仪表盘 / 雷达 / 漏斗 / 玫瑰 / 旭日 / 矩形树图
# --------------------------------------------------------------------------
def _gauge(row: dict | None) -> dict | None:
    """检出率仪表盘：醒目展示综合检出率（%）。"""
    if not row:
        return None
    rate = row.get("defect_rate_pct")
    if rate is None:
        return None
    rate = float(rate)
    gmax = max(10.0, round(rate * 2, 1))
    return {
        "title": {"text": "综合检出率", "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"formatter": "{b}: {c}%"},
        "series": [{
            "name": "检出率", "type": "gauge",
            "min": 0, "max": gmax,
            "startAngle": 220, "endAngle": -40,
            "pointer": {"width": 4, "length": "70%"},
            "progress": {"show": True, "roundCap": True, "width": 12,
                         "itemStyle": {"color": _grad(_PALETTE[0], 0.95, 0.6)}},
            "axisLine": {"lineStyle": {"width": 12, "color": [[1, "#E2E8F0"]]}},
            "axisTick": {"show": False},
            "splitLine": {"length": 6, "lineStyle": {"color": "#CBD5E1", "width": 1}},
            "axisLabel": {"distance": 18, "color": "#64748B", "fontSize": 10},
            "anchor": {"show": True, "size": 10, "itemStyle": {"color": _PALETTE[0]}},
            "title": {"offsetCenter": [0, "75%"], "fontSize": 12, "color": "#64748B"},
            "detail": {"valueAnimation": True, "formatter": "{value}%",
                       "fontSize": 28, "fontWeight": "bold", "color": _PALETTE[0],
                       "offsetCenter": [0, "45%"]},
            "data": [{"value": round(rate, 2), "name": "检出率"}],
        }],
    }


def _radar(rows: list[dict]) -> dict | None:
    """厂家多维雷达：检测量 / 缺陷数 / 检出率三轴归一化对比（最多 6 家）。"""
    if not rows:
        return None
    rows = rows[:6]
    max_total = max((r.get("total") or 0) for r in rows) or 1
    max_defects = max((r.get("defects") or 0) for r in rows) or 1
    max_rate = max((r.get("defect_rate_pct") or 0) for r in rows) or 1

    def _norm(v, m):
        return round(float(v or 0) / float(m) * 100, 1)

    indicators = [
        {"name": "检测量", "max": 100},
        {"name": "缺陷数", "max": 100},
        {"name": "检出率", "max": 100},
    ]
    series = []
    for i, r in enumerate(rows):
        color = _PALETTE[i % len(_PALETTE)]
        series.append({
            "name": f'{r.get("factory", "")}·{r.get("glass_type", "")}',
            "value": [
                _norm(r.get("total"), max_total),
                _norm(r.get("defects"), max_defects),
                _norm(r.get("defect_rate_pct"), max_rate),
            ],
            "lineStyle": {"color": color, "width": 2},
            "itemStyle": {"color": color},
            "areaStyle": {"color": _rgba(color, 0.12)},
        })
    return {
        "title": {"text": "各厂家多维对比（归一化）", "left": "center", "textStyle": {"fontSize": 14}},
        "color": _PALETTE,
        "tooltip": {"trigger": "item"},
        "legend": {"type": "scroll", "top": 30, "data": [s["name"] for s in series]},
        "radar": {
            "indicator": indicators,
            "radius": "62%",
            "center": ["50%", "58%"],
            "axisName": {"color": "#475569", "fontSize": 11},
            "splitArea": {"areaStyle": {"color": ["#F8FAFC", "#F1F5F9"]}},
            "splitLine": {"lineStyle": {"color": "#E2E8F0"}},
        },
        "series": [{"type": "radar", "data": series}],
    }


def _funnel(rows: list[dict]) -> dict | None:
    """缺陷数量漏斗：从多到少呈现缺陷类型分布。"""
    if not rows:
        return None
    data = sorted(rows, key=lambda r: -(int(r.get("count") or 0)))
    return {
        "title": {"text": "缺陷类型漏斗", "left": "center", "textStyle": {"fontSize": 14}},
        "color": _PALETTE,
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} 件"},
        "series": [{
            "name": "缺陷数量", "type": "funnel",
            "left": "8%", "right": "8%", "top": 40, "bottom": 20,
            "width": "84%", "minSize": "12%", "maxSize": "100%",
            "sort": "descending", "gap": 3,
            "label": {"show": True, "position": "inside", "formatter": "{b} {c}", "fontSize": 11},
            "itemStyle": {"borderRadius": 4, "borderColor": "#fff", "borderWidth": 1},
            "data": [{"name": r["defect_type"], "value": int(r.get("count") or 0)} for r in data],
        }],
    }


def _rose(rows: list[dict]) -> dict | None:
    """南丁格尔玫瑰图：半径/面积编码缺陷占比，比普通饼图更醒目。"""
    if not rows:
        return None
    data = [{"name": r["defect_type"], "value": int(r.get("count") or 0)} for r in rows]
    return {
        "title": {"text": "缺陷类型构成（玫瑰图）", "left": "center", "textStyle": {"fontSize": 14}},
        "color": _PALETTE,
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} 件 ({d}%)"},
        "legend": {"orient": "vertical", "right": 8, "top": "middle", "type": "scroll"},
        "series": [{
            "name": "缺陷数量", "type": "pie",
            "roseType": "radius",
            "radius": ["18%", "70%"], "center": ["42%", "55%"],
            "itemStyle": {"borderRadius": 6, "borderColor": "#fff", "borderWidth": 2},
            "label": {"formatter": "{b}\n{d}%"},
            "data": data,
        }],
    }


def _sunburst(rows: list[dict]) -> dict | None:
    """旭日图：厂家 -> 缺陷类型 两层环形层级分布。"""
    if not rows:
        return None
    by_factory: dict[str, list[dict]] = {}
    for r in rows:
        by_factory.setdefault(r["factory"], []).append(
            {"name": r["defect_type"], "value": int(r.get("count") or 0)})
    data = [{"name": f, "children": children} for f, children in by_factory.items()]
    return {
        "title": {"text": "厂家-缺陷层级分布（旭日图）", "left": "center", "textStyle": {"fontSize": 14}},
        "color": _PALETTE,
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} 件"},
        "series": [{
            "type": "sunburst",
            "radius": ["12%", "78%"], "center": ["50%", "55%"],
            "data": data,
            "label": {"rotate": "radial", "fontSize": 10},
            "itemStyle": {"borderRadius": 4, "borderColor": "#fff", "borderWidth": 1},
        }],
    }


def _treemap(rows: list[dict]) -> dict | None:
    """矩形树图：面积编码缺陷数量，按厂家分组。"""
    if not rows:
        return None
    by_factory: dict[str, list[dict]] = {}
    for r in rows:
        by_factory.setdefault(r["factory"], []).append(
            {"name": r["defect_type"], "value": int(r.get("count") or 0)})
    data = [{"name": f, "children": children} for f, children in by_factory.items()]
    return {
        "title": {"text": "厂家-缺陷构成（矩形树图）", "left": "center", "textStyle": {"fontSize": 14}},
        "color": _PALETTE,
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} 件"},
        "series": [{
            "type": "treemap",
            "data": data,
            "top": 40, "bottom": 10, "left": 10, "right": 10,
            "leafDepth": 2,
            "itemStyle": {"borderRadius": 4, "borderColor": "#fff", "borderWidth": 2, "gapWidth": 2},
            "label": {"show": True, "fontSize": 10},
            "upperLabel": {"show": True, "height": 20, "fontSize": 12},
        }],
    }


# --------------------------------------------------------------------------
# V2 维度扩展：产线对比 / 班次对比 / 严重度分布 / 设备参数趋势
# --------------------------------------------------------------------------
def _rate_compare(rows: list[dict], key: str, title: str, ct: str) -> dict | None:
    """通用检出率对比柱图：key 为分组字段名（line_no/shift）。"""
    if not rows:
        return None
    labels = [str(r.get(key) or "未知") for r in rows]
    vals = [r.get("defect_rate_pct") for r in rows]
    if ct == "line":
        opt = _base(title)
        opt["xAxis"] = {"type": "category", "data": labels, "axisLabel": {"rotate": 30}}
        opt["yAxis"] = {"type": "value", "name": "%"}
        s = {"name": "检出率", "type": "line", "smooth": True, "data": vals,
             "lineStyle": {"width": 3, "color": _PALETTE[2]}, "itemStyle": {"color": _PALETTE[2]}}
        s.update(_max_markpoint(_PALETTE[2]))
        opt["series"] = [s]
        return opt
    opt = _base(title)
    opt["xAxis"] = {"type": "category", "data": labels, "axisLabel": {"rotate": 30}}
    opt["yAxis"] = {"type": "value", "name": "%"}
    s = {
        "name": "检出率", "type": "bar", "barMaxWidth": 32,
        "data": vals,
        "itemStyle": {"borderRadius": [4, 4, 0, 0], "color": _grad(_PALETTE[6])},
    }
    s.update(_mean_markline("#F59E0B"))
    opt["series"] = [s]
    return opt


def _line_compare(rows: list[dict], ct: str) -> dict | None:
    return _rate_compare(rows, "line_no", "各产线检出率对比（%）", ct)


def _shift_compare(rows: list[dict], ct: str) -> dict | None:
    return _rate_compare(rows, "shift", "各班次检出率对比（%）", ct)


def _severity(rows: list[dict], ct: str) -> dict | None:
    """严重度分布：默认饼图展示占比，bar 用柱图。"""
    if not rows:
        return None
    data = [{"name": str(r.get("severity") or "未知"), "value": int(r.get("count") or 0)} for r in rows]
    if ct == "bar":
        opt = _base("缺陷严重度分布")
        opt["xAxis"] = {"type": "category", "data": [d["name"] for d in data]}
        opt["yAxis"] = {"type": "value", "name": "件"}
        series = [{
            "name": "数量", "type": "bar", "barMaxWidth": 40,
            "data": [{"value": d["value"],
                      "itemStyle": {"borderRadius": [4, 4, 0, 0], "color": _grad(_PALETTE[i % len(_PALETTE)])}}
                     for i, d in enumerate(data)],
        }]
        opt["series"] = series
        return opt
    return {
        "title": {"text": "缺陷严重度分布", "left": "center", "textStyle": {"fontSize": 14}},
        "color": _PALETTE,
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} 件 ({d}%)"},
        "legend": {"orient": "vertical", "right": 8, "top": "middle"},
        "series": [{
            "name": "严重度", "type": "pie",
            "radius": ["40%", "68%"], "center": ["42%", "55%"],
            "itemStyle": {"borderRadius": 6, "borderColor": "#fff", "borderWidth": 2},
            "label": {"formatter": "{b}\n{d}%"},
            "data": data,
        }],
    }


def _equipment_params(rows: list[dict]) -> dict | None:
    """设备参数多序列折线：x=日期，每个 (设备·参数) 一条线。"""
    if not rows:
        return None
    days = sorted({str(r.get("day")) for r in rows if r.get("day")})
    series_keys: list[str] = []
    for r in rows:
        key = f'{r.get("equipment", "设备")}·{r.get("param_name", "参数")}'
        if key not in series_keys:
            series_keys.append(key)
    by_day: dict[tuple, float] = {}
    for r in rows:
        key = f'{r.get("equipment", "设备")}·{r.get("param_name", "参数")}'
        by_day[(key, str(r.get("day")))] = float(r.get("value") or 0)
    series = []
    for i, key in enumerate(series_keys):
        color = _PALETTE[i % len(_PALETTE)]
        series.append({
            "name": key, "type": "line", "smooth": True, "symbolSize": 5,
            "data": [by_day.get((key, d)) for d in days],
            "lineStyle": {"width": 2.5, "color": color},
            "itemStyle": {"color": color},
        })
    opt = _base("设备参数趋势")
    opt["xAxis"] = {"type": "category", "data": days, "axisLabel": {"rotate": 30}}
    opt["yAxis"] = {"type": "value", "name": "值", "scale": True}
    opt["legend"] = {"type": "scroll", "top": 30}
    opt["tooltip"] = {"trigger": "axis"}
    opt["series"] = series
    return opt

