"""评测样本基线（第一版）。

字段说明：
- intent: 期望意图（qa / data / chat / diagnosis），供路由准确率评测。
- defect / glass: 期望实体（缺省表示该样本不参与该字段评测）。
样本逐步扩充到 Router 50~100、RAG 100、Data 80~100、多轮 30~50、诊断 50、
拒答/边界 30~50（V2 第十阶段目标）。
"""

SAMPLES: list[dict] = [
    # ---- 闲聊 / 问候（含复读式问候）----
    {"id": "chat_001", "text": "你好", "intent": "chat"},
    {"id": "chat_002", "text": "你好你好你好你好你好你好你好你好", "intent": "chat"},
    {"id": "chat_003", "text": "在吗", "intent": "chat"},
    {"id": "chat_004", "text": "hello", "intent": "chat"},

    # ---- 数据查询 ----
    {"id": "data_001", "text": "最近一个月检出率多少", "intent": "data"},
    {"id": "data_002", "text": "各厂家对比出个报表", "intent": "data"},
    {"id": "data_003", "text": "帮我分析一下最近各厂家的缺陷趋势", "intent": "data"},
    {"id": "data_004", "text": "统计一下最近一周的不良率", "intent": "data"},
    {"id": "data_005", "text": "整体分析一下耀华建筑玻璃公司的数据", "intent": "data"},

    # ---- 知识问答 ----
    {"id": "qa_001", "text": "气泡和结石怎么区分", "intent": "qa",
     "defect": "气泡"},
    {"id": "qa_002", "text": "划伤的判定标准是什么", "intent": "qa",
     "defect": "划伤"},
    {"id": "qa_003", "text": "玻璃应力斑是如何形成的", "intent": "qa"},
    {"id": "qa_004", "text": "划伤的原因是什么", "intent": "qa", "defect": "划伤"},
    {"id": "qa_005", "text": "为什么玻璃会碎", "intent": "qa"},
    {"id": "qa_006", "text": "检测气泡应该用什么光源和相机", "intent": "qa",
     "defect": "气泡"},

    # ---- 诊断 ----
    {"id": "diag_001", "text": "为什么最近晶捷电子划伤率升高", "intent": "diagnosis",
     "defect": "划伤"},
    {"id": "diag_002", "text": "气泡异常增多是什么原因", "intent": "diagnosis",
     "defect": "气泡"},
    {"id": "diag_003", "text": "帮我定位一下最近崩边突然变多的原因", "intent": "diagnosis",
     "defect": "崩边"},
    {"id": "diag_004", "text": "最近划痕不良率偏高，怎么改善", "intent": "diagnosis",
     "defect": "划伤"},

    # ---- 实体抽取（同义词 -> canonical）----
    {"id": "ent_001", "text": "划痕怎么处理", "intent": "qa", "defect": "划伤"},
    {"id": "ent_002", "text": "擦伤多不多", "intent": "data", "defect": "划伤"},
    {"id": "ent_003", "text": "电子玻璃崩边检出率多少", "intent": "data",
     "defect": "崩边", "glass": "电子玻璃"},
    {"id": "ent_004", "text": "eg 玻璃气泡不良率", "intent": "data",
     "defect": "气泡", "glass": "电子玻璃"},
    {"id": "ent_005", "text": "麻点儿和针孔的区别", "intent": "qa", "defect": "麻点"},
    {"id": "data_006", "text": "划伤检出率多少", "intent": "data", "defect": "划伤"},

    # ---- 乱码 / 无意义输入（V2：一律判 chat）----
    {"id": "chat_005", "text": "asdadadasd", "intent": "chat"},
    {"id": "chat_006", "text": "aaaaaaaaaaaa", "intent": "chat"},
    {"id": "chat_007", "text": "###@@@", "intent": "chat"},
    {"id": "chat_008", "text": "1111111111", "intent": "chat"},
    {"id": "chat_009", "text": "哈哈哈哈哈", "intent": "chat"},
    {"id": "chat_010", "text": "qqqqqqqqq", "intent": "chat"},

    # ---- V2 维度扩展：产线 / 班次 / 严重度 / 设备参数 ----
    {"id": "data_007", "text": "各产线检出率对比", "intent": "data"},
    {"id": "data_008", "text": "L2 产线最近检出率多少", "intent": "data"},
    {"id": "data_009", "text": "白班和夜班哪个检出率高", "intent": "data"},
    {"id": "data_010", "text": "缺陷严重度分布怎么样", "intent": "data"},
    {"id": "data_011", "text": "最近设备参数温度变化趋势", "intent": "data"},
    {"id": "data_012", "text": "晶捷电子玻璃按产线对比下检出率", "intent": "data", "glass": "电子玻璃"},
    # 厂家名 + 时间/趋势词（无根因词）→ data；含根因词 → diagnosis
    {"id": "data_013", "text": "晶捷电子玻璃近两周划伤是不是变多了", "intent": "data", "defect": "划伤", "glass": "电子玻璃"},
    {"id": "data_014", "text": "晶捷电子玻璃近两周划伤变多了", "intent": "data", "defect": "划伤", "glass": "电子玻璃"},
    {"id": "diag_005", "text": "为什么晶捷电子玻璃划伤变多了", "intent": "diagnosis", "defect": "划伤"},
]
