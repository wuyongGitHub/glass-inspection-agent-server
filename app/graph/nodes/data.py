"""数据查询流：参数解析 -> 白名单安全 SQL -> 分析报告 + ECharts 图表 + 优化建议。"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Literal, Optional

from langchain_core.messages import SystemMessage
from pydantic import BaseModel, Field

from app.domain import DEFECT_TYPES, DEFECT_GUIDANCE
from app.formatting import FORMAT_GUIDE
from app.llm import get_llm
from app.state import AgentState
from app.tools.chart_builder import build_chart_option
from app.tools.insights import build_insights
from app.tools.query_executor import execute_query

PARSE_PROMPT = """你是玻璃检测数据查询助手，把用户的自然语言需求解析为结构化查询参数。
今天是 {today}。

可用枚举值：
- metric: overview(总体概览看板) / defect_rate_trend(检出率按天趋势) / defect_count_trend(每日缺陷量趋势) / daily_volume(每日检测量) / rate_by_factory(按厂家对比) / top_defects(缺陷类型排行) / factory_composition(各厂家缺陷构成分布) / multi(综合分析看板：概览+趋势+厂家+缺陷构成) / rate_by_line(按产线对比) / rate_by_shift(按班次对比) / severity_distribution(严重度分布) / equipment_params(设备参数趋势)
- glass_type: 建筑玻璃 / 家电玻璃 / 电子玻璃
- defect_type: """ + " / ".join(DEFECT_TYPES) + """
- severity: 轻微 / 一般 / 严重

解析规则：
- 用户没有提到的过滤条件一律留空(None)，不要猜测（系统会用上轮条件自动补全）
- "近一周/最近7天"等相对时间要换算成具体日期；未提及时间则留空（系统默认近30天）
- factory 填厂家名称关键词，例如"晶捷""华南"
- line_no 填产线号，例如"L1""L2""1号产线"；shift 填班次"白班/夜班"
- equipment 填设备名关键词，例如"检测机""相机""光源"；param_name 填参数名，例如"温度""光源亮度"
- 问"是不是变多了/趋势"用 defect_rate_trend；问缺陷数量变化"多少件/变多"用 defect_count_trend；问产能/检测量/每天检多少用 daily_volume
- 问"哪家好/对比"用 rate_by_factory；问"哪条产线好/产线对比/按产线"用 rate_by_line；问"哪个班次高/白班夜班对比/按班次"用 rate_by_shift
- 问缺陷"严重程度/等级分布/轻微一般严重各多少"用 severity_distribution
- 问"设备参数/温度/光源亮度变化趋势"用 equipment_params
- 问缺陷"统计/构成/占比/哪类多/饼状图分析"（如"分析最近瑕疵出现的统计"）用 top_defects
- 问"各厂的缺陷构成/分布/哪厂哪类多/热力"用 factory_composition
- 用户要求"全面/综合/整体分析/整体情况/出个看板/大屏"或同时问多个维度时用 multi
- 用户明确要求"饼状图/占比/构成"时 chart_type=pie；明确要柱状图 chart_type=bar；
  要"热力图/矩阵/分布深浅"→heatmap；要"堆叠/累计构成对比"→stack；
  要"双轴/检测量+检出率一起/组合图"→dual；要"多张图/看板/几张图一起/都看看"→dashboard；
  要"仪表盘/表盘/刻度盘"→gauge；要"雷达图/蛛网图/多维对比"→radar；
  要"漏斗图/转化漏斗"→funnel；要"玫瑰图/南丁格尔/花瓣图"→rose；
  要"旭日图/环形层级/太阳图"→sunburst；要"矩形树图/树状图/treemap"→treemap；其余留 auto
- 这是对上一轮的追问时，若用户没重复厂家/玻璃类型/时间/产线/班次，则留空由系统继承

只输出一个 JSON 对象（字段与类型按上述定义），不要输出任何其他文字。"""


class QueryParams(BaseModel):
    # 用 Optional 兜住 json_mode 下模型把不确定字段填 null 的情况
    # （字段缺失时 default 生效，字段为 null 时靠 data_parse 归一）
    metric: Optional[Literal[
        "overview", "defect_rate_trend", "defect_count_trend", "daily_volume",
        "rate_by_factory", "top_defects", "factory_composition", "multi",
        "rate_by_line", "rate_by_shift", "severity_distribution", "equipment_params",
    ]] = Field(default="overview", description="查询指标")
    chart_type: Optional[Literal[
        "auto", "pie", "bar", "line", "heatmap", "stack", "dual", "dashboard",
        "gauge", "radar", "funnel", "rose", "sunburst", "treemap",
    ]] = Field(default="auto", description="用户指定的图表类型，未提及则 auto")
    factory: Optional[str] = Field(default=None, description="厂家名称关键词")
    glass_type: Optional[Literal["建筑玻璃", "家电玻璃", "电子玻璃"]] = None
    defect_type: Optional[str] = Field(default=None, description="缺陷类型")
    line_no: Optional[str] = Field(default=None, description="产线号，如 L1/L2")
    shift: Optional[str] = Field(default=None, description="班次，如 白班/夜班")
    severity: Optional[Literal["轻微", "一般", "严重"]] = Field(default=None, description="严重度")
    equipment: Optional[str] = Field(default=None, description="设备名关键词")
    param_name: Optional[str] = Field(default=None, description="设备参数名，如 温度/光源亮度")
    start_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    end_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")


_INHERIT_KEYS = (
    "factory", "glass_type", "defect_type", "line_no", "shift",
    "severity", "equipment", "param_name", "start_date", "end_date",
)


def _inherit_params(parsed: dict, prev: dict | None) -> dict:
    """追问场景：未提及的过滤条件继承上一轮，使"那按厂家对比下"能延续厂家/玻璃类型/时间/产线/班次。"""
    if not prev:
        return parsed
    for key in _INHERIT_KEYS:
        if not parsed.get(key) and prev.get(key):
            parsed[key] = prev[key]
    return parsed


def data_parse(state: AgentState) -> dict:
    today = date.today()
    prompt = PARSE_PROMPT.replace("{today}", today.isoformat())
    try:
        params = (
            get_llm(temperature=0)
            .with_structured_output(QueryParams, method="json_mode")
            .invoke([SystemMessage(content=prompt)] + state["messages"][-6:])
        )
        p = params.model_dump()
    except Exception:
        # 模型输出 null/非 JSON 等解析失败时，回退默认查询，不让整条链路 500
        p = {
            "metric": None, "chart_type": None,
            "factory": None, "glass_type": None, "defect_type": None,
            "line_no": None, "shift": None, "severity": None,
            "equipment": None, "param_name": None,
            "start_date": None, "end_date": None,
        }
    # 显式 null 归一为默认值，避免下游收到空指标
    p["metric"] = p.get("metric") or "overview"
    p["chart_type"] = p.get("chart_type") or "auto"
    # 多轮追问：未提及的过滤条件继承上一轮查询条件
    p = _inherit_params(p, state.get("last_query_params"))
    if not p.get("start_date"):
        p["start_date"] = (today - timedelta(days=30)).isoformat()
    if not p.get("end_date"):
        p["end_date"] = today.isoformat()
    return {"query_params": p, "last_query_params": p}


def data_execute(state: AgentState) -> dict:
    try:
        result = execute_query(state["query_params"])
        return {"query_rows": result["rows"]}
    except Exception as e:  # 查询失败不让图崩溃，交给分析节点兜底提示
        return {"query_rows": [], "query_params": {**state["query_params"], "error": str(e)}}


ANALYZE_PROMPT = """你是玻璃检测数据分析师。根据查询条件与结果输出 markdown 分析报告，包含三部分：
1. <strong style="color:#1f6feb">📊 数据摘要</strong>：关键数字（检测量、不良数、检出率等），适度使用列表/表格
2. <strong style="color:#1f6feb">⚠️ 异常与风险</strong>：结合"计算洞察"中已给出的环比/突变/离群信号，指出明显偏高、突变或值得关注的点（可补充其它观察，但不得编造数字）
3. <strong style="color:#1f6feb">💡 优化建议</strong>：结合下方"缺陷-工艺对照表"与 Top 缺陷，只引用与本次数据相关的条目，给出可执行动作

要求：只依据数据说话，不要编造不存在的数字；简体中文；控制在 500 字以内。

查询条件：{params}
查询结果（JSON）：{rows}
计算洞察（确定性统计，可直接引用）：
{insights}
缺陷-工艺对照表：
{guidance}

""" + FORMAT_GUIDE


def _has_any_data(rows: list[dict]) -> bool:
    """判断查询结果是否真的包含数据。

    多指标(multi/overview)返回的是 section 结构 `[{"section": ..., "data": [...]}]`，
    即便所有 data 都是空列表，外层 rows 也非空，需逐段检查；
    单指标返回的是平铺行列表，只要有字典即视为有数据。
    """
    for r in rows:
        if not isinstance(r, dict):
            continue
        if "data" in r:
            if r.get("data"):
                return True
        else:
            return True
    return False


def data_analyze(state: AgentState) -> dict:
    rows = state.get("query_rows") or []
    p = state.get("query_params") or {}
    if p.get("error"):
        return {"final_answer": f"查询执行失败：{p['error']}", "chart_config": None}
    if not _has_any_data(rows):
        return {
            "final_answer": "该条件下没有查到检测数据。请调整厂家、玻璃类型或时间范围后重试。",
            "chart_config": None,
        }

    chart = build_chart_option(p.get("metric", "overview"), rows, p)
    insights = build_insights(p.get("metric", "overview"), rows)
    guidance = "\n".join(f"- {k}：{v}" for k, v in DEFECT_GUIDANCE.items())
    prompt = ANALYZE_PROMPT.format(
        params=json.dumps(p, ensure_ascii=False, default=str),
        rows=json.dumps(rows, ensure_ascii=False, default=str)[:8000],
        insights=insights or "（无明显异常信号）",
        guidance=guidance,
    )
    answer = None
    try:
        answer = get_llm(temperature=0.3).invoke(
            [SystemMessage(content=prompt), SystemMessage(content="请生成分析报告")]
        )
    except Exception:
        # 模型网关抖动时：图表与确定性统计洞察照常返回，不整条 500
        return {
            "final_answer": "**模型服务暂时不可用**，未能生成完整分析文字。以下为本次数据的确定性统计信号，图表已正常生成，可稍后重试获取完整报告：\n\n"
            + (insights or "（无明显异常信号）"),
            "chart_config": chart,
        }
    return {"final_answer": answer.content, "chart_config": chart}
