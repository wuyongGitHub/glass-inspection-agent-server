# -*- coding: utf-8 -*-
"""临时冒烟脚本：验证新指标白名单 SQL 与图表生成。运行后删除。"""
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tools.chart_builder import build_chart_option
from app.tools.query_executor import execute_query

end = date.today().isoformat()
start = (date.today() - timedelta(days=30)).isoformat()


def check(metric, chart_type="auto"):
    r = execute_query({"metric": metric, "start_date": start, "end_date": end})
    c = build_chart_option(metric, r["rows"], {"chart_type": chart_type})
    kind = c.get("layout", "single-option") if isinstance(c, dict) and "layout" in c else "option"
    n_charts = len(c.get("charts", [])) if isinstance(c, dict) and "charts" in c else 1
    print(f"{metric:22s} ct={chart_type:9s} rows={len(r['rows']):3d} -> {kind} charts={n_charts}")
    return c


for m in ["overview", "defect_rate_trend", "defect_count_trend", "daily_volume",
          "rate_by_factory", "top_defects", "factory_composition", "multi"]:
    c = check(m)
    if m == "overview":
        # 看板容器应有 KPI + 至少一张图
        assert c.get("layout") == "dashboard" and c.get("kpis") and c.get("charts")
    if m == "factory_composition":
        # 图可渲染 json（保证无可序列化问题）
        json.dumps(c, ensure_ascii=False)

check("defect_rate_trend", "dual")
check("top_defects", "pie")
check("factory_composition", "heatmap")
check("factory_composition", "stack")
check("multi", "pie")
check("multi", "dashboard")
print("chart smoke ok")
