# 玻璃检测部门智能体 · 超级详细架构与落地手册

> 面向玻璃工业视觉检测部门的内部业务智能体（Agent）完整技术文档。
> 覆盖：业务背景与落地思想、技术栈、系统架构、五条业务流、RAG 知识库、数据层、工具层、记忆系统、案例库、评测体系、环境搭建、部署运行与扩展路线。
> 版本：V2（反映当前实际目录结构与实现，修正旧文档中 `domain.py` 单文件等过时描述）。

---

## 目录

1. [项目概述](#1-项目概述)
2. [落地思想与设计原则](#2-落地思想与设计原则)
3. [技术栈](#3-技术栈)
4. [系统架构总览](#4-系统架构总览)
5. [目录结构详解](#5-目录结构详解)
6. [全局状态模型 AgentState](#6-全局状态模型-agentstate)
7. [意图路由 Router](#7-意图路由-router)
8. [六条业务流详解](#8-六条业务流详解)
9. [RAG 知识库完整链路](#9-rag-知识库完整链路)
10. [领域层（本体 + 术语标准化）](#10-领域层本体--术语标准化)
11. [工具层](#11-工具层)
12. [记忆系统](#12-记忆系统)
13. [历史案例库 Case RAG](#13-历史案例库-case-rag)
14. [数据层（SQLite / MySQL 双后端）](#14-数据层sqlite--mysql-双后端)
15. [评测体系](#15-评测体系)
16. [配置项详解](#16-配置项详解)
17. [环境搭建（从零到跑通）](#17-环境搭建从零到跑通)
18. [运行与部署](#18-运行与部署)
19. [接入真实生产环境](#19-接入真实生产环境)
20. [扩展路线](#20-扩展路线)
21. [已知限制与注意事项](#21-已知限制与注意事项)

---

## 1. 项目概述

本项目是一个面向**玻璃工业视觉检测部门**的内部业务智能体，基于 **LangGraph** 构建。

### 1.1 业务背景

- **检测方式**：工业相机 + 光源采集玻璃图像，YOLO 目标检测模型识别缺陷。
- **检测对象**：建筑玻璃 / 家电玻璃 / 电子玻璃。
- **缺陷类型**（`app/domain/ontology.py` 的 `DEFECT_TYPES`）：气泡、崩边、杂质、结石、划伤、倒角不良、字符缺失、漏墨、缺印、油墨不良、麻点。
- **数据链路**：检测结果上云写入数据库 → 智能体自然语言查询 → 生成分析报告、ECharts 图表大屏与优化建议。

### 1.2 核心能力

| 能力 | 说明 |
|---|---|
| 知识问答（qa） | 基于部门知识库的 RAG 问答：缺陷判定标准、工艺机理、SOP、光源/相机选型、术语解释 |
| 数据查询（data） | 自然语言查询云端检测数据，自动产出分析报告 + ECharts 图表 + 优化建议 |
| 诊断分析（diagnosis） | 根因定位：数据取证 + 知识检索 + 证据链构建 + 四级可信度报告 |
| 闲聊兜底（chat） | 问候欢迎、业务外话题简短回应并引导回业务 |
| 视觉分析（vision） | 现场图片 → 视觉大模型识别缺陷 → 检索知识/案例 → 输出诊断意见；自动区分玻璃缺陷图 / 文档文字图 / 无关图 |
| 文件问答（file_qa） | 上传文档（pdf/docx/pptx/xlsx/md/txt），解析文字后即时问答（不落向量库） |
| 主动洞察 | 趋势突变检测、离群识别、环比/同比、帕累托分析、SPC 过程控制 |
| 长期记忆 | 厂家历史风险、产线风险、已确认案例、用户偏好（可追溯、可修正） |
| 历史案例库 | Case RAG：结构化匹配 + 症状向量相似度检索，沉淀「问题→原因→措施→效果」 |

---

## 2. 落地思想与设计原则

### 2.1 核心设计哲学：**"LLM 只做组织与措辞，数字与逻辑交给确定性代码"**

这是整个项目最重要的指导思想，贯穿所有模块：

- **数学/统计计算**（均值、标准差、环比、SPC 控制限、帕累托、离群检测）全部由 Python 确定性函数计算，**不让 LLM 算数**，避免数字幻觉。
- **SQL 拼装**由白名单代码完成，**LLM 永远不生成 SQL**，杜绝注入与越权。
- **图表配置**由代码确定性生成 ECharts option，LLM 只负责分析文字，保证可稳定上大屏。
- **证据（Evidence）** 统一携带 `metric / value / baseline / change / source / confidence / calculation_method`，保证每条结论可追溯、可复核。

### 2.2 意图路由："规则前置 + LLM 兜底"

网关 LLM 对玻璃行业术语的意图判断不稳定（同一问题多次结果不一），因此路由采用分层策略：

```
纯规则硬判定（问候/乱码） → 诊断规则 → 厂家+数据规则 → 关键词计数 → 多轮继承 → LLM 结构化输出兜底
```

规则层命中率高、零成本、可离线评测；规则无法判定时才交给 LLM（`json_mode` 结构化输出）。

### 2.3 领域本体 + 术语标准化（canonical 实体）

同义词/别名统一归一到 canonical 实体，例如：

- `划痕 / 擦伤 / 刮伤 / 表面划伤` → `划伤`
- `气孔` → `气泡`
- `漏印` → `缺印`
- `eg / 盖板 / 基板` → `电子玻璃`

Router、RAG、SQL 参数解析、Case 检索、报告生成统一使用 canonical 实体，减少同义表达导致的路由误判、检索漏召回与 SQL 参数错配。

### 2.4 分级可信度（事实/推断/假设/建议）

诊断报告严格区分四级结论：

- **事实（fact）**：数据库/知识库直接给出的。
- **推断（inference）**：由多个事实推导。
- **假设（hypothesis）**：待验证的可能原因，标注"待验证"与缺失数据。
- **建议（recommendation）**：可执行的检查/改善动作。

明确区分**相关性**与**因果性**：没有设备参数/现场确认时，只能说"疑似/待确认"。

### 2.5 稳健性与降级

所有 LLM 调用、数据库连接、OCR、embedding 均做了异常兜底，**任何单点故障不导致整条链路 500**：

- 路由失败 → 退化为闲聊。
- 参数解析失败 → 回退默认查询。
- SQL 执行失败 → 交给分析节点给出友好提示。
- LLM 网关抖动 → 图表与确定性统计洞察照常返回，仅缺失分析文字。
- 数据库不可用 → 路由退回关键词/LLM 判定。
- embedding 不可用 → 本地 n-gram 哈希向量兜底。
- OCR 引擎未安装 → 自动跳过，不影响文字型 PDF。

---

## 3. 技术栈

### 3.1 核心框架与库（`requirements.txt`）

| 依赖 | 版本 | 用途 |
|---|---|---|
| `langgraph` | >=0.2.50 | 图编排（StateGraph、checkpointer） |
| `langchain-core` | >=0.3.0 | 消息抽象（HumanMessage/AIMessage/SystemMessage） |
| `langchain-openai` | >=0.2.0 | OpenAI 兼容接口（LLM + Embedding） |
| `fastapi` | >=0.110 | Web 服务框架 |
| `uvicorn[standard]` | >=0.29 | ASGI 服务器（含 SSE 流式） |
| `pydantic` | >=2.6 | 数据校验 / 结构化输出（BaseModel） |
| `python-dotenv` | >=1.0 | `.env` 环境变量加载 |
| `pymysql` | >=1.1.0 | MySQL 驱动 |
| `pypdf` | >=4.0 | PDF 文字层提取 |
| `python-docx` | >=1.1 | Word 文档解析 |
| `python-pptx` | >=0.6.23 | PPT 文档解析 |
| `openpyxl` | >=3.1 | Excel 文档解析 |

### 3.2 可选 OCR 依赖（`requirements-ocr.txt`）

| 依赖 | 版本 | 用途 |
|---|---|---|
| `PyMuPDF` | >=1.24 | PDF 页面渲染为图片（纯 pip，无需 poppler） |
| `paddleocr` | >=2.7 | 中文 OCR 识别（推荐） |
| `paddlepaddle` | >=2.6 | PaddleOCR 推理后端 |

> 备选 OCR：`pytesseract`（需系统安装 tesseract-ocr 及中文包 chi_sim）+ `Pillow`。

### 3.3 技术要点清单

- **图编排**：LangGraph `StateGraph` + `MemorySaver` checkpointer（进程内多轮记忆）。
- **向量检索**：轻量 JSON 落盘向量库（`vector_store.json`）+ 余弦相似度 + 词法 IDF 加权混合排序。
- **Embedding 双模式**：远程 OpenAI 兼容 embeddings / 本地离线 n-gram 哈希向量（`EMBED_BACKEND=local`）。
- **图表**：代码生成 ECharts option（20+ 图型），JSON 可序列化。
- **数据库**：SQLite（演示）/ MySQL（生产）双后端，`?`/`%s` 占位符自动适配。
- **结构化输出**：`with_structured_output(..., method="json_mode")`。
- **统计分析**：SPC（均值±3σ + Western Electric 判异）、帕累托 80/20、Z-score 离群、趋势漂移、皮尔逊相关。

---

## 4. 系统架构总览

### 4.1 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│                        HTTP 接口层 (main.py)                 │
│   POST /api/chat  ·  GET /api/chat/stream  ·  GET /health    │
└──────────────────────────────┬──────────────────────────────┘
                               │ graph.invoke / graph.stream
┌──────────────────────────────▼──────────────────────────────┐
│                    图编排层 (graph/builder.py)               │
│  START → [vision/router] → qa / data / diagnosis / chat      │
│             LangGraph StateGraph + MemorySaver               │
└──────────────────────────────┬──────────────────────────────┘
                               │ 节点读写 AgentState
┌──────────────────────────────▼──────────────────────────────┐
│                     节点层 (graph/nodes/)                    │
│  router · qa · data · diagnosis · planner · vision · chat    │
└───────┬──────────────┬──────────────┬──────────────┬─────────┘
        │              │              │              │
┌───────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐ ┌─────▼─────────┐
│  RAG 检索层   │ │  工具层     │ │  领域层     │ │  记忆/案例层   │
│ rag/         │ │ tools/     │ │ domain/    │ │ memory/ cases/│
│ ingest/store │ │ query_exec │ │ ontology   │ │ long_term     │
│ rerank/ocr   │ │ chart/stat │ │ terms      │ │ cases.store   │
└───────┬──────┘ └─────┬──────┘ └────────────┘ └─────┬─────────┘
        │              │                            │
┌───────▼──────────────▼────────────────────────────▼─────────┐
│                   数据层 (db/session.py)                     │
│            SQLite (演示)  /  MySQL (生产) 双后端              │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 请求处理流程（宏观）

1. FastAPI 接收 `POST /api/chat`，构造 `HumanMessage`，调用 `graph.invoke`。
2. `route_start` 分流：带图片（`image`/`images`）→ 视觉分析流；带文件（`file_texts`）→ 文件问答流；否则 → 路由节点。
3. 路由节点识别意图，分流到 qa / data / diagnosis / chat 之一。
4. 对应业务流执行，最终写入 `final_answer`（+ 可选 `chart_config`）。
5. FastAPI 返回 `{intent, answer, chart}`。

### 4.3 图结构（`graph/builder.py`）

```python
START
  ├─ vision_analyze（有图片）→ END
  ├─ file_qa（有文件文本）→ END
  └─ router → qa_retrieve → qa_generate → END
            ├─ data_parse → data_execute → data_analyze → END
            ├─ diagnosis_plan → diagnosis_run → diagnosis_report → END
            └─ chat_reply → END
```

所有边均在 `build_graph()` 中用 `add_conditional_edges` / `add_edge` 静态声明，`compile(checkpointer=MemorySaver())` 编译。

---

## 5. 目录结构详解

```
glass-inspection-agent-server/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 入口（/health、/api/chat、/api/chat/upload、/api/chat/stream）
│   ├── config.py               # Settings 配置类（从 .env 读取）
│   ├── state.py                # AgentState 全局状态定义
│   ├── llm.py                  # LLM / Embedding 工厂（OpenAI 兼容 + 本地 n-gram 兜底）
│   ├── logging.py              # 结构化 JSON 日志（含 thread_id 上下文）
│   ├── domain/                 # 领域层
│   │   ├── __init__.py         # 重导出 canonical 常量与术语函数
│   │   ├── ontology.py         # 本体：玻璃类型/缺陷/严重度/指标/缺陷-工艺对照表
│   │   └── terms.py            # 术语标准化：同义词/别名 -> canonical 实体
│   ├── graph/                  # 图编排
│   │   ├── builder.py          # 主图组装（六条流）
│   │   └── nodes/
│   │       ├── router.py       # 意图路由（规则前置 + LLM 兜底）
│   │       ├── qa.py           # 问答流（检索 + 生成）
│   │       ├── data.py         # 数据流（参数解析 + 执行 + 分析）
│   │       ├── diagnosis.py    # 诊断流（数据取证 + 证据 + 报告）
│   │       ├── planner.py      # 诊断调查计划（实体抽取 + 步骤 + 参数）
│   │       ├── vision.py       # 视觉分析流（图片 -> 缺陷 -> 诊断意见 / 文档转文字）
│   │       ├── file_qa.py      # 文件问答流（上传文件即时问答）
│   │       └── chat.py         # 闲聊兜底
│   ├── rag/                    # RAG 知识库
│   │   ├── ingest.py           # 入库：扫描文档、切块、向量化、落盘
│   │   ├── loaders.py          # 多格式文档解析（pdf/md/docx/pptx/xlsx）
│   │   ├── store.py            # 轻量向量存储 + 混合检索
│   │   ├── query_rewrite.py    # 查询改写（同义词/canonical 扩展）
│   │   ├── reranker.py         # 重排序（semantic+lexical+domain+authority）
│   │   └── ocr.py              # OCR 兜底（扫描件/图片型 PDF）
│   ├── tools/                  # 工具层
│   │   ├── query_executor.py   # 白名单安全 SQL 执行器
│   │   ├── chart_builder.py    # ECharts 图表配置生成（20+ 图型）
│   │   ├── insights.py         # 主动洞察（趋势突变/离群/环比等）
│   │   ├── evidence.py         # 统一证据结构（fact/inference/hypothesis/recommendation）
│   │   ├── spc.py              # SPC 过程控制（I-MR + Western Electric）
│   │   ├── statistics.py       # 工业统计分析（帕累托/均值/相关性等）
│   │   └── vision.py           # 多模态视觉检测工具
│   ├── memory/                 # 记忆系统
│   │   ├── __init__.py
│   │   ├── long_term.py        # 长期业务记忆（business_memory 表）
│   │   └── short_term.py       # 短期会话上下文提取
│   ├── cases/                  # 案例库
│   │   └── store.py            # Case RAG（检索 + 沉淀）
│   ├── db/                     # 数据层
│   │   ├── session.py          # 双后端连接 + 建表 + 迁移
│   │   ├── schema.sql          # SQLite 建表脚本
│   │   └── schema_mysql.sql    # MySQL 建表脚本
│   └── evaluation/             # 评测
│       ├── benchmark.py        # 确定性路由/实体准确率评测
│       └── samples.py          # 评测样本基线
├── scripts/
│   ├── seed_db.py              # 生成演示检测数据（含埋异常）
│   └── eval_benchmark.py       # 运行离线评测
├── tests/
│   └── test_graph.py           # 图构建冒烟测试
├── docs/                       # 文档
├── data/
│   ├── docs/                   # 知识库文档目录（KB_DIR）
│   ├── inspection.db           # SQLite 数据库（运行时生成）
│   └── vector_store.json       # 向量索引（ingest 生成）
├── requirements.txt
├── requirements-ocr.txt
└── README.md
```

---

## 6. 全局状态模型 AgentState

定义于 `app/state.py`，所有节点读写同一份状态（`TypedDict`，`total=False` 表示字段均可选）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `messages` | `Annotated[list, add_messages]` | 对话历史（`add_messages` 自动追加合并） |
| `image` | `str` | 单图（URL 或 base64 Data URL），存在时走视觉流 |
| `images` | `list[str]` | 多图列表，存在时走视觉流 |
| `file_texts` | `list[dict]` | 上传文件解析结果 `[{"filename","text"}]`，存在时走文件问答流 |
| `intent` | `str` | 路由结果：qa / data / diagnosis / vision / file_qa / chat |
| `task_type` | `str` | 任务类型（诊断流细分） |
| `entities` | `dict` | 领域实体（缺陷/玻璃类型，已标准化） |
| `qa_context` | `list` | 问答流检索到的知识库片段 |
| `query_params` | `dict` | 数据流解析出的查询参数 |
| `query_rows` | `list` | 数据流执行结果 |
| `plan` | `list` | 诊断流调查计划 |
| `evidence` | `list` | 诊断流证据链 |
| `hypotheses` | `list` | 诊断假设 |
| `cases` | `list` | 匹配到的历史案例 |
| `risk_level` | `str` | 风险等级 |
| `action_items` | `list` | 行动项 |
| `citations` | `list` | 引用来源 |
| `confidence` | `float` | 置信度 |
| `final_answer` | `str` | 最终回答 |
| `chart_config` | `dict` | ECharts 图表配置 |
| `last_intent` | `str` | 上轮意图（多轮继承） |
| `last_query_params` | `dict` | 上轮查询参数（追问继承） |

> 所有字段均为可选，节点用 `.get()` 读取；`image`/`images`/`file_texts` 每轮请求入口显式覆盖（含为 `None`），避免残留上一轮媒体字段导致误判。

---

## 7. 意图路由 Router

定义于 `app/graph/nodes/router.py`，是系统的"大脑分流器"。

### 7.1 判定优先级（从高到低）

1. **纯问候硬规则**（`is_greeting`）：整句恰为问候语或复读式问候 → `chat`，绝不上数据流，也绝不继承上一轮意图。
2. **确认/收尾词硬规则**（`is_ack`）：整句恰为"好的/嗯/OK/谢谢/知道了/收到"等确认收尾语或复读 → `chat`，由闲聊节点极简回应，避免带上一轮上下文重复输出长内容。
3. **乱码/无意义输入**（`is_gibberish`）：纯符号、同字符反复、键盘乱打 → `chat`。
4. **诊断意图**（`_diagnosis_intent`）：根因/异常触发词 + 数据变化信号或厂家名 → `diagnosis`。
5. **厂家+数据强命中**（`_factory_data_hint`）：句中含库内真实厂家名 + 数据类意图词 → `data`。
6. **关键词计数**（`_keyword_intent`）：命中 QA_HINT / DATA_HINT 数多者胜；率值类强数据词（RATE_HINT）优先于缺陷名；qa 与 data 平局倾向 qa。
7. **多轮继承**：仅在「明确的纯指代追问」且上轮是 data/qa 时沿用上轮意图。
8. **LLM 兜底**：以上均未命中 → `json_mode` 结构化输出 `RouteDecision{intent}`，失败退化为 `chat`。

### 7.2 关键词表（分类）

- **QA_HINT**：机理成因（"如何形成/成因/为什么"）、判定标准（"判定/判级/阈值/公差"）、工艺（"退火/钢化/应力"）、检测方法（"光源/相机/明场/暗场"）、缺陷名（"气泡/崩边/划伤/结石"）、公司介绍（"介绍/概况/主营"）等。
- **TIME_HINT**：相对时间词（"近两周/上周/最近/本月"等）。
- **TREND_HINT**：趋势变化词（"变多/升高/下降/激增"等）。
- **DATA_HINT**：统计报表词（"检出率/不良率/统计/趋势/对比/排名"）+ TIME_HINT + TREND_HINT + 维度词（"产线/班次/严重度"等）。
- **RATE_HINT**：率值/数量强数据词（"检出率/不良率/数量/环比/同比"）+ TREND_HINT。
- **FOLLOWUP_HINT**：承接指代词（"那/再/还有/换成/这样"等）。
- **DIAGNOSIS_ROOT**：根因词（"为什么/原因/异常/定位/改善/复盘"等）。
- **DIAGNOSIS_DATA**：数据变化信号（"变多/升高/突然/最近/检出率"等）。

### 7.3 厂家名动态加载

`_load_factory_names()` 首次调用时从数据库 `factories` 表读取真实厂家名并缓存；DB 缺失/异常时返回 `[]`，退回关键词/LLM 判定，**不让路由因库不可用而崩**。

### 7.4 离线评测复用

`deterministic_route(text)` 是纯规则路由（不调 LLM、不继承上下文），供离线评测与单测复用，规则优先级与 `router_node` 一致。

---

## 8. 六条业务流详解

### 8.1 问答流（qa）—— `graph/nodes/qa.py`

**目标**：基于知识库的 RAG 问答，带引用回答。

**节点**：

1. **qa_retrieve（检索）**
   - 加载 `SimpleVectorStore`（`vector_store.json`）。
   - 若知识库为空 → 返回空 context 与 citations。
   - 否则：`rewrite_query(question)` 改写 → `get_embeddings().embed_query(query)` 向量化 → `rerank_top(store, query, query_vec)` 精排。
   - 返回 `qa_context`（片段 + 来源标注）与 `citations`（来源列表）。

2. **qa_generate（生成）**
   - 知识库为空 → 提示放入文档并运行 ingest。
   - 根据 `settings.qa_fallback` 选择 prompt：
     - **QA_FALLBACK=true（默认）**：片段不足/无关时，先声明"知识库未收录"，再用 LLM 通用知识回答，标注"非部门检验标准口径"。
     - **QA_FALLBACK=false（严格）**：仅依据片段，未收录主题一律拒答并建议补充文档。
   - 历史消息只作背景，模型只回答最后一条用户消息。
   - LLM 异常时返回友好提示，不 500。

### 8.2 数据查询流（data）—— `graph/nodes/data.py`

**目标**：自然语言 → 结构化查询参数 → 白名单 SQL → 分析报告 + 图表 + 建议。

**节点**：

1. **data_parse（参数解析）**
   - 用 `json_mode` 结构化输出 `QueryParams`（12 种 metric + 14 种 chart_type + 过滤条件）。
   - 解析失败 → 回退默认参数。
   - 显式 null 归一为默认值。
   - **多轮继承**：`_inherit_params` 把未提及的过滤条件继承上一轮。
   - 未指定时间默认近 30 天。

   **支持的 metric**（`app/domain/ontology.py` 的 METRICS）：
   | metric | 说明 |
   |---|---|
   | `overview` | 总体概览看板（KPI + 趋势） |
   | `defect_rate_trend` | 检出率按天趋势 |
   | `defect_count_trend` | 每日缺陷数量趋势 |
   | `daily_volume` | 每日检测量（产能） |
   | `rate_by_factory` | 按厂家（及玻璃类型）对比检出率 |
   | `top_defects` | 缺陷类型数量排行 Top 10 |
   | `factory_composition` | 各厂家缺陷类型构成分布 |
   | `multi` | 综合分析看板（概览+趋势+厂家+缺陷构成） |
   | `rate_by_line` | 按产线对比检出率 |
   | `rate_by_shift` | 按班次对比检出率 |
   | `severity_distribution` | 缺陷严重度分布 |
   | `equipment_params` | 设备参数趋势 |

2. **data_execute（执行）**
   - 调用 `execute_query(params)` 执行白名单 SQL。
   - 查询失败 → 返回空 rows + error 标记，交给分析节点兜底。

3. **data_analyze（分析）**
   - 有 error → 返回失败提示。
   - 无数据 → 提示调整条件。
   - 否则：`build_chart_option` 生成图表 + `build_insights` 生成确定性洞察 + `DEFECT_GUIDANCE` 对照表 → 组装 prompt → LLM 生成分析报告（数据摘要 + 异常风险 + 优化建议）。
   - LLM 异常时图表与洞察照常返回。

### 8.3 诊断流（diagnosis）—— `graph/nodes/planner.py` + `diagnosis.py`

**目标**：根因定位，先规划调查维度，再逐项用确定性工具验证，输出四级可信度报告。

**节点**：

1. **diagnosis_plan（计划）**
   - `resolve_entities(text)` 抽取缺陷/玻璃类型实体。
   - 读取厂家名，从用户文本中识别厂家。
   - 组装查询参数（metric=multi，默认近 30 天）。
   - 生成调查计划步骤（趋势定位 → 厂家对比 → 缺陷构成 → 机理/案例 → 假设排序）。

2. **diagnosis_run（执行 + 取证）**
   - 执行 SQL 查询，失败记录 error。
   - 整体概览 → `fact` 证据（含检出率/检测量/不良数）。
   - `build_evidence` 把确定性洞察转成结构化 Evidence（趋势漂移/SPC/离群/帕累托）。
   - 缺陷机理：先确定性对照表（`inference`），再用 RAG 补充（`fact`）。
   - 历史案例检索 `search_cases`（结构化 + 症状向量）。
   - 长期记忆 `recall_factory_risks` 参考厂家历史风险。
   - 返回 `evidence`（结构化）、`cases`、`citations`、`confidence`。

3. **diagnosis_report（报告）**
   - 无数据 → 提示补充条件。
   - 组装 prompt（计划 + 事实 + 统计信号 + 对照 + 知识片段 + 历史案例）。
   - LLM 生成四部分报告：**事实 / 推断 / 假设 / 建议**。
   - LLM 异常 → 降级输出确定性证据摘要。
   - 追加引用来源。

### 8.4 闲聊兜底（chat）—— `graph/nodes/chat.py`

- 乱码/无意义输入（`is_gibberish`）→ 返回 `GIBBERISH_REPLY`，一句话简短忽略（不调 LLM、不带历史）。
- 确认/收尾词（`is_ack`，如"好的/嗯/谢谢/知道了"）→ 返回 `ACK_REPLY` 极简回应，不调 LLM、不携带历史，避免带上一轮上下文重复输出长内容。
- 纯问候（`is_greeting`）→ 用 `WELCOME_PROMPT` 热情介绍能力范围（150 字内）。
- 其它 → 用 `CHAT_PROMPT` 简短回应并自然引导回业务（仅携带最近 8 条历史）。
- LLM 异常 → 友好提示。

### 8.5 视觉分析流（vision）—— `graph/nodes/vision.py`

- 入口：`state["image"]` 或 `state["images"]` 存在（`route_start` 分流，多图视为同一批现场照片）。
- 逐张调用 `analyze_defect_image`（视觉大模型），先判 `content_type` 三分类：
  - `glass_defect` → 提取缺陷类型/严重度/置信度/描述/可能成因；
  - `document` → 逐行转录文字（`text_content`）；
  - `other` → 仅描述。
- 分支处理：
  - 全部为文档图 → 提取文字（视觉转录不足时用本地 OCR 增强），LLM 归纳，明确"这是文档/文字图片"，不再套缺陷诊断；
  - 既非缺陷也非文档 → 友好提示上传玻璃照片或文档图片；
  - 存在玻璃缺陷图 → 构建 `fact`（视觉识别 + 置信度）+ `inference`（缺陷工艺对照），检索知识库 + 案例库，LLM 输出 200 字内图片诊断意见（客观转述 → 可能成因 → 复核/改善动作）。
- 返回 `intent="vision"` + evidence + cases + citations + final_answer。

### 8.6 文件问答流（file_qa）—— `graph/nodes/file_qa.py`

- 入口：`state["file_texts"]` 存在（`route_start` 分流）。
- 每个文件 `chunk_text` 切块，embedding 取与问题最相关的 Top 8 块（余弦相似度），失败则取前 8 块。
- LLM 生成「仅依据上传文件内容、标注文件出处」的回答。
- 不落向量库：文件文本为单次会话临时内容。
- 返回 `intent="file_qa"` + citations + final_answer。

---

## 9. RAG 知识库完整链路

### 9.1 入库（`rag/ingest.py`）

入口命令：`python -m app.rag.ingest`

流程：
1. 扫描 `KB_DIR`（默认 `data/docs/`）下所有 `.md/.txt/.pdf/.docx/.pptx/.xlsx`。
2. 逐个调用 `load_document` 解析为纯文本。
3. 空文本（扫描件/图片型 PDF 需 OCR）→ 跳过。
4. `chunk_text` 切块（`CHUNK_SIZE=500`、`CHUNK_OVERLAP=50`，按空行分段聚合，超长段滑窗切分）。
5. `get_embeddings().embed_documents(chunks)` 向量化。
6. `_meta_for` 生成元数据：`source / chunk / doc_type / glass_type / defect_type / authority`。
7. `store.add` 追加，`store.save()` 落盘 `vector_store.json`。

**关键点**：每次 `ingest()` 全量重建（`store.chunks = []`），避免陈旧索引。

**文档类型识别（`_doc_type`）**：根据文件名 + 前 400 字判断 `standard`（标准）、`sop`（作业指导）、`tutorial`（教程）、`report`（报告）、`faq`（问答）、`article`（文章）。

**authority 权威度（`_authority`）**：`standard`(1.0) > `sop`(0.9) > `report`(0.7) > `tutorial`(0.6) > `faq`(0.8) > `article`(0.5)，用于重排序加权。

### 9.2 多格式文档解析（`rag/loaders.py`）

| 格式 | 解析方式 |
|---|---|
| `.txt` | 直接 `utf-8` 读取，失败回退 `gbk` |
| `.md` | 直接读取（保留 Markdown 原始文本） |
| `.pdf` | `pypdf` 逐页提取文字层；无文字层（扫描件）时由 `ocr.py` 兜底 |
| `.docx` | `python-docx` 读取段落 + 表格 |
| `.pptx` | `python-pptx` 读取所有 slide 的 shape 文本 + 表格 |
| `.xlsx` | `openpyxl` 读取所有 sheet 单元格（含表头拼接） |

### 9.3 OCR 兜底（`rag/ocr.py`）

- `_page_has_text`：判断 PDF 页面文字层字符数。
- 文字层 < 40 字且含图片 → 触发 OCR。
- `_ocr_pdf`：`fitz`（PyMuPDF）渲染页面为 PNG → `PaddleOCR`（PP-OCRv6 中英文）识别。
- OCR 引擎不可用 → 自动跳过（返回空），不影响文字型 PDF。
- 结果与已有文字层合并，重复页面去重。

### 9.4 轻量向量存储（`rag/store.py`）

`SimpleVectorStore`：
- 数据字段：`id / text / embedding / metadata`。
- 持久化：JSON 落盘 `vector_store.json`（含 `meta` 与 `chunks`）。
- **检索 `search`**：余弦相似度 + 词法命中（倒排 n-gram IDF 加权）混合打分。
- **词法命中**：查询 token（中文 2-gram 分词）与 chunk 文本的 IDF 加权重叠度，叠加到向量相似度上，解决短查询/专业术语的召回问题。
- **`get_embeddings`**（在 `llm.py`）双模式：
  - `EMBED_BACKEND=openai`：调用远程 OpenAI 兼容 embeddings（需 `EMBED_BASE_URL/EMBED_API_KEY/EMBED_MODEL`）。
  - `EMBED_BACKEND=local`（默认）：本地 n-gram 哈希 512 维向量，纯离线、无成本、可复现，但对语义同义表达召回弱于远程 embedding。

### 9.5 查询改写（`rag/query_rewrite.py`）

- 同义词扩展：把 query 中出现的缺陷/玻璃类型别名映射为 canonical，并补入 canonical 词。
- 提取缺陷/玻璃类型关键词，追加到查询（提高召回）。
- 改写失败时返回原 query。

### 9.6 重排序（`rag/reranker.py`）

`rerank_top(store, query, query_vec)` 综合四路打分排序：

1. **语义分**：query 向量与 chunk 向量的余弦相似度。
2. **词法分**：query 与 chunk 的词法重叠（n-gram IDF）。
3. **领域分**：chunk 命中 query 中的缺陷/玻璃类型 canonical 词时加权。
4. **权威分**：chunk 元数据 `authority` 加权。

最终返回 Top-K（`QA_TOP_K`，默认 3）片段。

### 9.7 运行示例（实际验证）

- `data/docs/test.pdf`（1 页 143 字，文字型 PDF）→ 直接入库，无需 OCR。
- `data/docs/test2.pdf`（37 页，9 页图片型页面）→ 触发 PaddleOCR 识别。
- 入库成功后日志显示 "知识库构建完成"，生成 `vector_store.json`（示例中 344 chunks）。
- PaddleOCR 日志中的 "Creating model: PP-OCRv6_medium_det" 是正在加载模型（处理中），"No ccache found" / "用提供的模式无法找到文件" 均为无害警告。

---

## 10. 领域层本体 + 术语标准化

### 10.1 本体（`app/domain/ontology.py`）

| 常量 | 内容 |
|---|---|
| `GLASS_TYPES` | 建筑玻璃 / 家电玻璃 / 电子玻璃 |
| `DEFECT_TYPES` | 气泡、崩边、杂质、结石、划伤、倒角不良、字符缺失、漏墨、缺印、油墨不良、麻点 |
| `SEVERITIES` | 轻微 / 一般 / 严重 |
| `METRICS` | 12 种数据指标（见 8.2） |
| `CHART_TYPES` | 14 种图表类型（见 11.2） |
| `DEFECT_GUIDANCE` | 缺陷 → 成因 / 工艺机理 / 优化建议对照表（数据流与诊断流优化建议的知识来源） |

### 10.2 术语标准化（`app/domain/terms.py`）

提供函数与映射：

- `ALIAS_MAP`：别名 → canonical（如 `划痕/擦伤/刮伤 → 划伤`、`气孔 → 气泡`、`eg → 电子玻璃`）。
- `resolve_defect(text)`：文本 → canonical 缺陷类型（未命中返回 None）。
- `resolve_glass_type(text)`：文本 → canonical 玻璃类型。
- `resolve_entities(text)`：一次抽取缺陷 + 玻璃类型。
- `is_defect(word)` / `is_glass_type(word)`：判等辅助。

**作用**：Router 意图判定、RAG 查询改写、SQL 参数解析、Case 检索、报告生成统一使用 canonical 实体，避免同义词错配。

---

## 11. 工具层

### 11.1 查询执行器（`tools/query_executor.py`）

**核心安全原则：白名单 SQL，杜绝注入**。

- 每种 `metric` 对应一个**预编译 SQL 模板**（字符串常量，仅插入经过校验的参数）。
- **参数校验**：`factory` 必须命中库内真实厂家名；`defect_type` 必须命中 `DEFECT_TYPES`；`glass_type` 必须命中 `GLASS_TYPES`；`severity` 必须命中 `SEVERITIES`。
- **占位符适配**：通过 `adapt_sql(sql)` 自动把 `?` 转成 `%s`（MySQL），SQLite 保持 `?`。
- **方言差异**：日期函数、`LIMIT` 等由模板分支处理。
- **防注入**：参数一律走参数化绑定，LLM 不参与 SQL 拼接。
- 查询失败时返回空结果并记录 error，由上层节点兜底。

### 11.2 图表生成器（`tools/chart_builder.py`）

`build_chart_option(metric, rows)` 确定性生成 ECharts option（JSON 可序列化），LLM 不参与。

**支持的图表类型（CHART_TYPES）**：
| chart_type | 图型 |
|---|---|
| `line` | 折线图 |
| `bar` | 柱状图 |
| `horizontal_bar` | 横向排行 |
| `pie` / `ring` | 饼图 / 环形图 |
| `multi_line` | 多折线 |
| `combo` | 双轴组合图 |
| `stacked_bar` | 堆叠柱状 |
| `stacked_area` | 堆叠面积 |
| `radar` | 雷达图 |
| `heatmap` | 热力图 |
| `funnel` | 漏斗图 |
| `gauge` | 仪表盘 |
| `scatter` | 散点图 |
| `boxplot` | 箱线图 |

**看板容器**：`overview` 与 `multi` 返回 `{layout:"dashboard", kpis:[...], charts:[...]}` 多图看板。

### 11.3 主动洞察（`tools/insights.py`）

`build_insights(metric, rows)` 输出确定性洞察列表（LLM 不参与计算）：

- 趋势突变检测（`_detect_trend_shift`）：相邻周期变化幅度超阈值。
- 离群识别（`_detect_outlier`）：Z-score / IQR 判定厂家离群。
- 环比/同比（`_period_over_period`）。
- 帕累托分析（`_pareto`）：Top 缺陷累计占比（80/20）。
- 归因分析（`_attribute`）：把洞察映射到缺陷-工艺建议。

### 11.4 证据结构（`tools/evidence.py`）

`build_evidence(metric, rows)` 把确定性洞察转换为统一 `Evidence` 结构：

```
{metric, value, baseline, change, source, confidence, calculation_method}
```

- `source`：`data`（数据库）/ `rag`（知识库）/ `rule`（规则对照）。
- `confidence`：高/中/低。
- `calculation_method`：描述如何计算得出（可复核）。
- 分类：`fact` / `inference` / `hypothesis` / `recommendation`。

### 11.5 SPC 过程控制（`tools/spc.py`）

- 计算均值 ± 3σ 控制限（I-MR 单值-移动极差图）。
- **Western Electric 判异规则**：连续 7 点同侧、连续 6 点递增/递减、连续 8 点超出 1σ、单点超出 3σ 等。
- 输出：控制限、判异点、稳定性结论。

### 11.6 统计分析（`tools/statistics.py`）

- 均值/标准差/中位数/分位数。
- 帕累托 80/20 分析。
- Z-score 离群检测。
- 趋势漂移检测（线性回归斜率）。
- 皮尔逊相关系数（设备参数与缺陷率关联）。

### 11.7 视觉检测工具（`tools/vision.py`）

`analyze_defect_image(image, hint)` 调用视觉大模型（`get_vision_llm`，OpenAI 兼容），先判 `content_type` 三分类（`glass_defect`/`document`/`other`），再结构化输出：
- 缺陷图：`{defect_type, severity, confidence, description, possible_causes}`
- 文档图：`{text_content}`（逐行转录文字）
- 无关图：`{description}`

`extract_image_text(image)` 为本地 OCR 兜底。供视觉分析流使用。

---

## 12. 记忆系统

### 12.1 长期业务记忆（`memory/long_term.py`）

持久化到 `business_memory` 表，支持四类记忆（`memory_type`）：

| 类型 | 说明 |
|---|---|
| `factory_risk` | 厂家历史风险（长期不良趋势） |
| `line_risk` | 产线历史风险 |
| `confirmed_case` | 已确认案例（根因+措施+效果） |
| `user_preference` | 用户偏好（如关注某厂家） |

**核心接口**：
- `save_memory(memory_type, key, value, confidence)`：保存/更新（`upsert`，同 key 覆盖）。
- `recall_factory_risks(factory)`：召回厂家历史风险。
- `recall_line_risks(factory, line)`：召回产线风险。
- `recall_user_preferences(thread_id)`：召回用户偏好。
- 记忆带 `confidence` 与 `created_at/updated_at`，可追溯、可修正。

**特点**：所有记忆可追溯（含来源），且可用"修正/删除"语义更新，避免陈旧错误记忆污染后续诊断。

### 12.2 短期会话上下文（`memory/short_term.py`）

从 `AgentState.messages` 提取当前会话上下文，供多轮继承（指代消解）使用，配合 LangGraph `MemorySaver` checkpointer 实现进程内多轮记忆。

---

## 13. 历史案例库 Case RAG

定义于 `app/cases/store.py`。

### 13.1 案例数据来源

`cases` 表（`seed_db.py` 内置 4 条种子案例：划伤/气泡/崩边/结石，含根因+证据+措施+效果）。

### 13.2 检索逻辑 `search_cases`

综合两路打分排序：

1. **结构化条件匹配**：`defect_type`、`glass_type`、`factory`、`line_no`、`shift` 匹配加权。
2. **症状向量相似度**：`symptoms` 文本向量化（embedding）与查询症状余弦相似度。

返回 Top-K 案例（含根因、置信度、证据链、措施效果）。

### 13.3 案例沉淀（`save_case`）

新案例（人工确认后）写入 `cases` + `case_evidence` + `case_actions` 三表，形成「问题 → 证据 → 根因 → 措施 → 效果」闭环，供后续诊断复用。

---

## 14. 数据层 SQLite / MySQL 双后端

### 14.1 连接管理（`db/session.py`）

- `init_db()`：根据 `settings.db_url` 选择后端，建表（首次自动建表）。
- `init_mysql_database()`：MySQL 首次使用时自动建库。
- `is_mysql_backend()`：判断当前后端。
- `adapt_sql(sql)`：把 `?` 占位符转 `%s`（MySQL）。
- **行序列化**：`Decimal` / `datetime` / `date` 等类型自动转 JSON 可序列化。
- 连接异常兜底，返回友好错误。

### 14.2 表结构（`db/schema.sql` / `schema_mysql.sql`）

| 表 | 说明 |
|---|---|
| `factories` | 厂家（id / name / glass_type） |
| `inspection_records` | 检测记录（厂家、玻璃类型、时间、产线、班次、总数、缺陷数） |
| `defects` | 缺陷明细（记录 id、类型、数量、严重度、置信度） |
| `shifts` | 班次 |
| `lines` | 产线（厂家-产线命名） |
| `equipment` | 设备（检测机/相机/光源） |
| `equipment_params` | 设备参数（温度/光源亮度等，带时间） |
| `cases` | 历史案例（缺陷、玻璃、厂家、症状、根因、置信度） |
| `case_evidence` | 案例证据（kind / claim / source） |
| `case_actions` | 案例措施（action / result） |
| `business_memory` | 长期业务记忆 |

### 14.3 演示数据生成（`scripts/seed_db.py`）

- 5 家厂家（华南玻璃/耀华建筑玻璃/信义家电面板/康宁家电配件/晶捷电子玻璃）。
- 参数可调：`--days`（默认 90）、`--lines`（默认 2）、`--min-total`/`--max-total`、`--base-rate-min/max`、`--seed`、`--no-anomaly`。
- **埋异常**：晶捷电子玻璃近 30 天检出率抬升 + 划伤权重提高，供主动洞察/诊断流识别。
- 生成顺序：先清空子表再父表（满足外键），批量 `executemany` 提高速度。

---

## 15. 评测体系

### 15.1 离线评测（`app/evaluation/`）

- `samples.py`：样本基线 `SAMPLES`（chat/data/qa/diagnosis/实体抽取/乱码边界，逐步扩充）。
- `benchmark.py`：
  - `run_router_benchmark`：确定性路由准确率（不依赖 LLM/DB，秒级回归）。
  - `run_entity_benchmark`：缺陷/玻璃类型实体抽取准确率。
  - `evaluate(samples)`：聚合两类评测。
- 运行：`python scripts/eval_benchmark.py`，打印准确率与失败样本。

### 15.2 冒烟测试（`tests/test_graph.py`）

验证图构建成功（节点/边齐全），不依赖 LLM/DB。

### 15.3 样本扩充目标（V2 第十阶段）

Router 50~100、RAG 100、Data 80~100、多轮 30~50、诊断 50、拒答/边界 30~50。

---

## 16. 配置项详解

配置由 `app/config.py` 的 `Settings` 类（pydantic-settings）从 `.env` 读取。主要配置项：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容 API 地址 |
| `LLM_API_KEY` | `EMPTY` | API 密钥 |
| `LLM_MODEL` | `gpt-4o-mini` | 主模型名 |
| `LLM_VISION_MODEL` | （空） | 视觉（多模态图片输入）模型；为空回退 `LLM_MODEL` |
| `EMBEDDING_MODEL` | `text-embedding-ada-002` | embedding 模型名 |
| `EMBED_BACKEND` | `remote` | embedding 后端：`remote`（OpenAI 兼容）/ `local`（n-gram 哈希兜底） |
| `DB_URL` | `sqlite:///data/inspection.db` | 数据库：`sqlite:///` 或 `mysql://` |
| `MYSQL_HOST/PORT/USER/PASSWORD/DATABASE/CHARSET` | `127.0.0.1`/`3306`/`root`/（空）/`glass_inspection`/`utf8mb4` | MySQL 连接参数 |
| `KB_DIR` | `data/docs` | 知识库文档目录 |
| `VECTOR_STORE_PATH` | `data/vector_store.json` | 向量索引落盘路径 |
| `RETRIEVE_TOP_K` | `12` | （历史保留，当前检索由 rerank 参数控制） |
| `RERANK_CANDIDATES` | `30` | 重排粗召回候选数 |
| `RERANK_TOP_K` | `6` | 重排后最终返回片段数 |
| `QUERY_REWRITE` | `true` | 查询改写开关 |
| `LLM_TIMEOUT` | `60` | LLM 调用超时（秒） |
| `LLM_MAX_RETRIES` | `2` | LLM 调用重试次数 |
| `DIAGNOSIS_ENABLED` | `true` | 诊断流开关 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `QA_FALLBACK` | `true` | 通用知识回退开关 |

> 切块大小/重叠（`CHUNK_SIZE=500`/`CHUNK_OVERLAP=50`）为 `app/rag/ingest.py` 内常量；数据查询默认近 30 天为节点内常量，均不在 `config.py` 暴露。

---

## 17. 环境搭建从零到跑通

### 17.1 前置条件

- Python 3.10+（推荐 3.11/3.12）。
- （可选）MySQL 8.0（生产用）；无则用 SQLite。
- （可选）OCR 依赖（仅扫描件/图片型 PDF 需要）。

### 17.2 安装依赖

```bash
cd glass-inspection-agent-server

# 创建虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

# 安装核心依赖
pip install -r requirements.txt

# （可选）安装 OCR 依赖，识别扫描件/图片型 PDF
pip install -r requirements-ocr.txt
```

### 17.3 配置环境变量

```bash
copy .env.example .env        # Windows；Linux/Mac 用 cp .env.example .env
```

编辑 `.env`，填写：
- `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`（模型接入）
- `DB_URL` + `MYSQL_*`（数据库连接）
- 可选 `EMBED_BACKEND` / `EMBED_*`（embedding 后端）

### 17.4 初始化数据库

```bash
# SQLite（默认，无需额外步骤，自动建表）
python scripts/seed_db.py

# MySQL（需先建库，再建表灌数据）
python -c "from app.db.session import init_mysql_database; init_mysql_database()"
python scripts/seed_db.py
```

### 17.5 构建知识库

```bash
python -m app.rag.ingest
```

把 `data/docs/` 下的文档解析、切块、向量化，生成 `data/vector_store.json`。支持 `.md/.txt/.pdf/.docx/.pptx/.xlsx`，扫描件/图片型 PDF 自动走 OCR（需先装 OCR 依赖）。

### 17.6 启动服务

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

验证：
- 浏览器打开 `http://127.0.0.1:8000/docs` → Swagger 接口文档 = 服务活着。
- 浏览器打开 `http://127.0.0.1:8000/health` → `{"status":"ok"}` = 正常。

---

## 18. 运行与部署

### 18.1 HTTP 接口

#### POST /api/chat

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "最近一个月各厂家的检出率对比", "thread_id": "user-001"}'
```

返回：`{"intent": "data", "answer": "...", "chart": <ECharts option 或 {"layout":"dashboard","kpis":[...],"charts":[...]} 多图看板容器>}`

#### GET /api/chat/stream（SSE 流式）

```
http://127.0.0.1:8000/api/chat/stream?q=气泡的判定标准是什么&thread_id=user-001
```

#### GET /health

### 18.2 示例问题

| 流 | 问题示例 |
|---|---|
| 问答 | `气泡和结石怎么区分？判定标准是什么？` |
| 问答 | `检测电子玻璃表面划伤用什么光源？` |
| 数据 | `最近一个月各厂家的检出率对比` |
| 数据 | `晶捷电子玻璃近两周划伤是不是变多了` |
| 数据 | `最近30天家电玻璃的Top缺陷类型` |
| 数据 | `这周整体情况怎么样，出个看板` |
| 数据 | `各厂的缺陷构成分布，画热力图对比` |
| 数据 | `每天检测量多少？再来个检测量和检出率的组合图` |

### 18.3 生产部署

- **进程管理**：systemd / supervisor 托管 `uvicorn`。
- **容器化**：Docker 镜像（详见 `docs/DEPLOYMENT.md`）。
- **反向代理**：Nginx 转发到 8000 端口。
- **checkpointer 替换**：生产环境将 `MemorySaver` 替换为 `SqliteSaver` / `PostgresSaver`，实现跨进程/跨重启多轮记忆持久化。

---

## 19. 接入真实生产环境

1. **模型**：`.env` 配置 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`，任何 OpenAI 兼容接口均可（含私有化部署）。
2. **数据库**：`.env` 设 `DB_URL=mysql://` 并填写 `MYSQL_*`；MySQL 表结构见 `app/db/schema_mysql.sql`。把 YOLO 上云写入的表映射过来即可，代码已内置 SQLite/MySQL 双后端适配，无需改代码。
3. **知识库**：把部门真实文档放入 `data/docs/`，重新运行 `python -m app.rag.ingest`。支持 `.md/.txt/.pdf/.docx/.pptx/.xlsx`（老式 `.xls` 请另存为 `.xlsx`）。扫描件/图片型 PDF 已内置 OCR 兜底。
4. **缺陷-工艺建议**：`app/domain/ontology.py` 的 `DEFECT_GUIDANCE` 是优化建议的知识来源，按部门经验持续补充。

> 术语约定：需求中的"杂志"按"杂质"处理；"倒角"按缺陷项"倒角不良"处理，如与部门口径不同请直接改 `app/domain/ontology.py`。

---

## 20. 扩展路线

1. 接入企业微信 / 钉钉机器人（在 FastAPI 前加回调路由）。
2. 高风险写操作（如自动派单、推送通知）增加 `interrupt` 人工审批节点。
3. 报表定时推送（定时任务调 graph + 消息通道）。
4. 对接 YOLO 检测服务（新增 tool：提交图片 / 查询单批次明细）。
5. 评估集扩充：把部门高频问题整理成问答对，回归验证效果。
6. checkpointer 升级为持久化（SQLite/Postgres），支持跨进程多轮。
7. 向量库升级：从轻量 JSON 向量库迁移到 Chroma / FAISS / Milvus，支持更大规模知识库与更优召回。

---

## 21. 已知限制与注意事项

1. **多轮记忆非持久化**：默认 `MemorySaver` 是进程内内存，服务重启后历史对话丢失（生产需换持久化 checkpointer）。
2. **本地 embedding 语义弱**：默认 `EMBED_BACKEND=local` 是 n-gram 哈希向量，对同义/跨语言语义表达召回弱于远程 embedding；需要更强语义时切换 `openai` 后端。
3. **OCR 速度慢**：扫描件/图片型 PDF 的 PaddleOCR 识别较慢，大文件建议分批入库。
4. **SQL 白名单有限**：仅支持预定义的 12 种 metric，超出的自由查询需扩展 `query_executor.py` 模板。
5. **因果推断受限**：诊断流没有设备参数/现场确认时，只能给"疑似/待确认"假设，避免过度归因。
6. **单进程无状态扩展**：当前无消息队列/任务队列，高并发需配合 uvicorn workers + 持久化 checkpointer。
7. **老式 `.xls` 不支持**：需另存为 `.xlsx`。
8. **术语口径需对齐**：同义词映射（`ALIAS_MAP`）与缺陷-工艺建议（`DEFECT_GUIDANCE`）需随部门口径持续维护。

---

## 附录：核心文件速查

| 文件 | 作用 |
|---|---|
| `app/main.py` | FastAPI 入口 |
| `app/config.py` | 环境配置 |
| `app/state.py` | AgentState |
| `app/llm.py` | LLM/Embedding 工厂 |
| `app/graph/builder.py` | 五条流图组装 |
| `app/graph/nodes/router.py` | 意图路由 |
| `app/domain/ontology.py` | 领域本体 |
| `app/domain/terms.py` | 术语标准化 |
| `app/rag/ingest.py` | 知识库入库 |
| `app/rag/store.py` | 向量存储 + 混合检索 |
| `app/tools/query_executor.py` | 白名单 SQL |
| `app/tools/chart_builder.py` | ECharts 图表 |
| `app/tools/insights.py` | 主动洞察 |
| `app/tools/evidence.py` | 证据结构 |
| `app/memory/long_term.py` | 长期记忆 |
| `app/cases/store.py` | 案例库 |
| `app/db/session.py` | 双后端数据层 |
| `scripts/seed_db.py` | 演示数据生成 |
| `scripts/eval_benchmark.py` | 离线评测 |

---

*本文档由源码逐文件梳理生成，覆盖搭建过程、落地思想与技术栈。如需进一步细化某模块实现，可对照上表直接阅读对应源码。*
