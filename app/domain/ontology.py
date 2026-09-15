"""领域本体：玻璃检测领域的 canonical 实体与稳定 ID。

V2 目标：让 Router、RAG、SQL 参数解析、Case 检索、报告生成尽量使用统一的
canonical 实体，而不是直接依赖用户原始文本。实体先落在 Python 常量 +
现有关系表（factories / inspection_records / defects）中，暂不引入图数据库；
后续关系复杂（如缺陷-工艺-设备多跳关联）再考虑 Neo4j 等方案。
"""
from __future__ import annotations

from enum import Enum

# 玻璃类型（与 DB factories.glass_type / inspection_records.glass_type 对齐）
GLASS_TYPES = ["建筑玻璃", "家电玻璃", "电子玻璃"]


class GlassType(str, Enum):
    BUILDING = "建筑玻璃"
    APPLIANCE = "家电玻璃"
    ELECTRONICS = "电子玻璃"


# 严重度（与 DB defects.severity 对齐）
SEVERITIES = ["轻微", "一般", "严重"]


class Severity(str, Enum):
    MINOR = "轻微"
    MODERATE = "一般"
    SEVERE = "严重"


# 数据流支持的缺陷类型（白名单，与 DB defects.defect_type 对齐）
DEFECT_TYPES = [
    "气泡", "崩边", "杂质", "结石", "划伤",
    "倒角不良", "字符缺失", "漏墨", "缺印", "油墨不良", "麻点",
]


class DefectType(str, Enum):
    BUBBLE = "气泡"
    EDGE_CHIPPING = "崩边"
    INCLUSION = "杂质"
    STONE = "结石"
    SCRATCH = "划伤"
    BAD_CHAMFER = "倒角不良"
    MISSING_CHAR = "字符缺失"
    INK_LEAK = "漏墨"
    MISSING_PRINT = "缺印"
    BAD_INK = "油墨不良"
    PITTING = "麻点"


# 数据查询流支持的指标（白名单，query_executor 只允许这些）
METRICS = {
    "overview": "总体检出率与检测量概览（含近段趋势，出看板图）",
    "defect_rate_trend": "检出率按天趋势",
    "defect_count_trend": "每日缺陷数量趋势",
    "daily_volume": "每日检测量（产能）",
    "rate_by_factory": "按厂家（及玻璃类型）对比检出率",
    "top_defects": "缺陷类型数量排行 Top 10",
    "factory_composition": "各厂家缺陷类型构成分布（堆叠/热力）",
    "multi": "综合分析看板（概览+趋势+厂家+缺陷构成）",
    # V2 维度扩展：产线 / 班次 / 严重度 / 设备参数
    "rate_by_line": "按产线对比检出率",
    "rate_by_shift": "按班次对比检出率",
    "severity_distribution": "缺陷严重度分布（轻微/一般/严重）",
    "equipment_params": "设备参数趋势（温度/光源亮度等随时间变化）",
}

# 缺陷 -> 典型成因与优化方向，供 data_analyze / diagnosis 生成针对性建议
DEFECT_GUIDANCE = {
    "气泡": "多为熔制澄清不良：检查熔窑温度曲线、澄清剂用量与澄清时间，关注配合料含气",
    "结石": "原料杂质或耐火材料侵蚀：检查原料筛分与入库检验，排查池壁砖/流道侵蚀状况",
    "划伤": "多来自传输与堆叠环节：检查辊道清洁度与磨损、玻璃间隔离垫、搬运吸盘",
    "崩边": "切割与磨边工序：检查切割刀轮压力与磨损、磨边轮状态、掰断力度",
    "倒角不良": "磨边倒角参数漂移：检查倒角轮进给量与对称性，核对砂轮磨损周期",
    "麻点": "成型与钢化环节：检查锡槽状况、辊道温度与钢化炉辊道清洁",
    "杂质": "原料纯度与熔制控制：加强原料批次检验，检查熔制温度均匀性",
    "字符缺失": "喷码/丝印工序：检查喷头堵塞、墨路压力与模版完整性",
    "漏墨": "丝印网版破损或刮刀压力异常：检查网版张力与破损点、刮刀硬度与压力",
    "缺印": "对位精度问题：检查 CCD 定位精度、模版安装位置与玻璃来料定位边",
    "油墨不良": "油墨粘度与固化异常：检查油墨搅拌与有效期、UV 固化灯功率与传送速度",
}

# 工序/设备/检验标准等本体目前以受控词表形式存在（数据源暂未接入设备/工艺参数）。
# 后续接入设备参数、产线/班次明细后，再在此登记 canonical ID 并持久化到 DB。
PROCESSES = ["熔制", "成型", "退火", "钢化", "切割", "磨边", "丝印", "镀膜", "包装"]
EQUIPMENT = ["熔窑", "锡槽", "退火窑", "钢化炉", "切割机", "磨边机", "丝印机", "喷码机"]
