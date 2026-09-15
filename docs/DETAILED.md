# 玻璃检测部门智能体 — 详细技术说明

> 本文档面向需要深入理解、二次开发、排障该智能体的开发/运维人员。
> 建议先读根目录 `README.md` 了解概览，再读本文。
> 配套文档：`docs/api.md`（HTTP 接口）、`docs/DEPLOYMENT.md`（服务器部署）。

---

## 目录

1. [整体架构](#1-整体架构)
2. [核心概念](#2-核心概念)
3. [状态模型 AgentState](#3-状态模型-agentstate)
4. [意图路由（router）](#4-意图路由router)
5. [问答流（qa）](#5-问答流qa)
6. [数据查询流（data）](#6-数据查询流data)
7. [诊断流（diagnosis）](#7-诊断流diagnosis)
8. [视觉分析流（vision）](#8-视觉分析流vision)
9. [文件问答流（file_qa）](#9-文件问答流file_qa)
10. [闲聊兜底（chat）](#10-闲聊兜底chat)
11. [检索与向量存储（rag）](#11-检索与向量存储rag)
12. [数据库层（db）](#12-数据库层db)
13. [配置体系（config）](#13-配置体系config)
14. [LLM 与 Embedding 工厂（llm）](#14-llm-与-embedding-工厂llm)
15. [多轮对话机制](#15-多轮对话机制)
16. [主动洞察机制（insights）](#16-主动洞察机制insights)
17. [安全设计](#17-安全设计)
18. [已知限制与坑](#18-已知限制与坑)

---

## 1. 整体架构

```
                     ┌─────────────────────────────────────┐
                     │            FastAPI (main.py)         │
                     │  POST /api/chat   GET /api/chat/stream │
                     │  POST /api/chat/upload（文件上传）     │
                     └───────────────┬─────────────────────┘
                                     │ invoke / stream
                                     ▼
                     ┌─────────────────────────────────────┐
                     │        LangGraph StateGraph          │
                     │          (graph/builder.py)           │
                     └───────────────┬─────────────────────┘
                                     │
        ┌────────────────────────────┼────────────────────────────┐
        ▼                            ▼                            ▼
   ┌─────────┐                 ┌───────────┐                ┌──────────┐
   │ router  │                 │   (分支)   │                │          │
   │ 意图识别 │                 └─────┬─────┘                │          │
   └─────────┘          ┌────────────┼────────────┐         │          │
                        ▼            ▼            ▼         │          │
                  ┌──────────┐ ┌──────────┐ ┌──────────┐    │          │
                  │ qa 问答流 │ │ data 数据│ │chat 闲聊 │    │          │
                  └──────────┘ └──────────┘ └──────────┘    │          │
```

入口分流（`builder.py` 的 `route_start`）：

| 入口条件 | 去向 |
|---|---|
| `image` / `images` 非空 | `vision_analyze`（视觉分析流） |
| `file_texts` 非空 | `file_qa`（文件即时问答流） |
| 其余（纯文本） | `router`（意图路由） |

六条业务流：

| 流 | 意图 | 节点链 | 产出 |
|---|---|---|---|
| 问答流 | `qa` | `qa_retrieve` → `qa_generate` | 带引用的文字回答 |
| 数据流 | `data` | `data_parse` → `data_execute` → `data_analyze` | 文字报告 + ECharts 图表 |
| 诊断流 | `diagnosis` | `diagnosis_plan` → `diagnosis_run` → `diagnosis_report` | 四级可信度诊断报告 |
| 视觉流 | `vision` | `vision_analyze` | 图片诊断意见 / 文档文字转述 |
| 文件问答流 | `file_qa` | `file_qa` | 基于上传文件的引用回答 |
| 闲聊流 | `chat` | `chat_reply` | 简短闲聊 / 欢迎介绍 |

**设计原则**：

1. **LLM 不碰 SQL**：数据查询走「预定义指标 + 参数绑定」，LLM 只把自然语言解析成结构化参数（`QueryParams`）。
2. **图表确定性生成**：ECharts option 由代码生成（`chart_builder.py`），LLM 只写文字，保证大屏稳定可渲染。
3. **洞察确定性计算**：趋势突变 / 离群 / 环比等统计信号由 `insights.py` 计算，LLM 只组织措辞，避免数字幻觉。
4. **状态单一载体**：所有节点读写同一份 `AgentState`，通过 LangGraph 的 reducer 机制合并。

---

## 2. 核心概念

### LangGraph 图（Graph）

- 节点（node）：接受 `state`，返回「对 state 的增量更新」的 dict。
- 边（edge）：控制节点间流转；条件边根据 state 决定去向。
- Checkpointer：持久化每个 `thread_id` 的图执行状态，实现多轮对话与断点续跑。

### `thread_id`（会话标识）

- 每个 `thread_id` 对应一份独立的对话历史（含多轮上下文）。
- 同一 `thread_id` 的消息会累积，供路由与参数继承使用。
- **重要**：当前使用 `MemorySaver`（进程内存），服务重启即清空（详见 §15）。

### 节点返回值的 reducer

`AgentState` 中 `messages` 字段用 `add_messages` reducer 自动追加；其它字段（如 `intent`、`query_params`）是「覆盖」语义，节点返回的 dict 会覆盖同名键。

---

## 3. 状态模型 AgentState

定义于 `app/state.py`（`TypedDict, total=False`，字段可选，节点用 `.get()` 读取）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `messages` | `Annotated[list, add_messages]` | 对话历史，自动追加 |
| `image` | `str` | 单图（URL 或 base64 Data URL），存在则走视觉流 |
| `images` | `list[str]` | 多图列表，存在则走视觉流 |
| `file_texts` | `list[dict]` | 上传文件解析结果 `[{"filename","text"}]`，存在则走文件问答流 |
| `intent` | `str` | 路由结果：`qa` / `data` / `diagnosis` / `vision` / `file_qa` / `chat` |
| `task_type` | `str` | 任务细分（诊断流用） |
| `entities` | `dict` | 领域实体（缺陷 / 玻璃类型，已标准化） |
| `qa_context` | `list` | 问答流检索到的知识库片段 |
| `query_params` | `dict` | 数据/诊断流解析出的查询参数 |
| `query_rows` | `list` | 数据/诊断流执行结果 |
| `plan` | `list` | 诊断流调查计划 |
| `evidence` | `list` | 诊断流结构化证据（`Evidence` 字典列表） |
| `hypotheses` | `list` | 诊断流假设 |
| `cases` | `list` | 检索到的相似历史案例 |
| `risk_level` | `str` | 诊断流风险等级 |
| `action_items` | `list` | 诊断流行动建议 |
| `citations` | `list` | 引用来源（文件名） |
| `confidence` | `float` | 诊断流可信度 |
| `final_answer` | `str` | 最终回答文字 |
| `chart_config` | `dict` | ECharts 图表配置 |
| `last_intent` | `str` | 上一轮意图（多轮继承用） |
| `last_query_params` | `dict` | 上一轮查询参数（多轮继承用） |

> `image` / `images` / `file_texts` 在每轮请求入口被**显式覆盖**（含为 `None` 时），防止 checkpointer 恢复上一轮残留的图片/文件字段，导致纯文本输入被误判为带图/带文件。

---

## 4. 意图路由（router）

文件：`app/graph/nodes/router.py`

### 判定优先级（`deterministic_route`，与 `router_node` 规则一致）

```
问候 > 确认/收尾词 > 乱码 > 诊断 > 厂家+数据 > 关键词（平局偏 qa）> LLM 兜底
```

1. **问候硬规则**（`is_greeting`）：整句（去空白/标点后）恰为问候语或问候词复读 → `chat`。
2. **确认/收尾词硬规则**（`is_ack`）：整句恰为"好的/嗯/OK/谢谢/知道了/收到"等确认收尾语或复读 → `chat`（闲聊节点极简回应，避免带上一轮上下文重复输出长内容）。
3. **乱码硬规则**（`is_gibberish`）：纯符号、同字符反复、键盘乱打式 ASCII 串 → `chat`。
4. **诊断规则**（`_diagnosis_intent`）：根因/异常触发词 + 数据变化信号，或「为什么/原因」+ 厂家名 → `diagnosis`（受 `DIAGNOSIS_ENABLED` 开关控制）。
5. **厂家+数据规则**（`_factory_data_hint`）：句中含库内真实厂家名 + 数据/时间/趋势意图词 → `data`。
6. **关键词规则**（`_keyword_intent`）：`qa_hits` 与 `data_hits` 多者胜，平局偏 `qa`，都为 0 返回 `None`。
7. **追问继承**（`_is_followup`）：`intent is None` 且句子短（≤12 字）+ 明确承接指代词 → 继承 `last_intent`。
8. **LLM 兜底**（`RouteDecision` 结构化输出）：规则未命中时，调用 LLM 用 `json_mode` 输出 `{"intent": "..."}`；失败则退化为 `chat`。

### 关键词表

- `QA_HINT`：缺陷机理/判定标准/工艺/设备选型/公司介绍等问答特征词。
- `TIME_HINT` / `TREND_HINT`：相对时间词与趋势变化词。
- `DATA_HINT`：统计/趋势/对比/排行/时间范围/维度（产线/班次/严重度/设备参数）等数据查询特征词。
- `RATE_HINT`：率值/数量类强数据词（检出率/不良率/数量…），只要出现即判 `data`。
- `DATA_STRONG_HINT`：厂家名 + 数据意图词强命中用。
- `FOLLOWUP_HINT`：明确承接指代词（**不含**"为什么/怎么"这类泛疑问词，避免误判）。
- `DIAGNOSIS_ROOT` / `DIAGNOSIS_DATA`：诊断根因触发词 + 数据变化信号。

### 关键修复：意图分类只传「当前消息」

LLM 兜底时**只把最后一条用户消息**传给模型，不塞历史消息。否则上一条是数据分析时，历史里的数据报告会带偏 LLM，导致"下一条无论发什么都判成 data"。多轮指代继承已由 `_is_followup` + `last_intent` 规则单独处理。

### 为什么「关键词优先」而非「纯 LLM」

网关 LLM 对玻璃术语（如"应力斑"）的意图判断不稳定（同一问题多次结果不一），关键词规则能锁定高频问法。LLM 仅作为规则覆盖不到时的兜底。

---

## 5. 问答流（qa）

文件：`app/graph/nodes/qa.py`

### `qa_retrieve`（检索）

1. 加载 `SimpleVectorStore`（从 `vector_store.json`），空则返回空上下文。
2. `rewrite_query(question)` 做查询改写（canonical 实体扩展，见 §11）。
3. 用 embedding 对改写后的 query 编码。
4. `rerank_top(store, query, query_vec)` 粗召回 + 精排（见 §11）。
5. 返回 Top 片段，格式化为 `"文本\n(来源: 文件名)"`，并收集 `citations`。

### `qa_generate`（生成）

- **Fallback 模式**（`QA_FALLBACK=true`，默认）：检索到相关片段则只依据片段回答；片段无关/不足时，先用一句说明「知识库未收录」，再用模型通用知识回答，并标注「非部门检验标准口径」。
- **Strict 模式**（`QA_FALLBACK=false`）：仅依据片段，未收录主题一律拒答并建议补充文档。
- 历史消息只作背景，模型仅回答最后一条用户消息；模型网关不可用时返回友好提示而非 500。

---

## 6. 数据查询流（data）

文件：`app/graph/nodes/data.py`

### `data_parse`（参数解析）

- 用 LLM `json_mode` 把自然语言解析成 `QueryParams`（字段缺失走 default，显式 null 归一为默认值）。
- 支持的 `metric`（白名单，12 种）：

| metric | 含义 |
|---|---|
| `overview` | 总体检出率与检测量概览（KPI + 趋势，看板容器） |
| `defect_rate_trend` / `defect_count_trend` / `daily_volume` | 按天检出率 / 缺陷量 / 检测量（产能） |
| `rate_by_factory` | 按厂家（及玻璃类型）对比检出率 |
| `top_defects` | 缺陷类型数量排行 Top 10 |
| `factory_composition` | 各厂家缺陷类型构成（堆叠 / 热力） |
| `multi` | 综合分析看板（概览 + 趋势 + 厂家 + 缺陷构成） |
| `rate_by_line` | 按产线对比检出率 |
| `rate_by_shift` | 按班次对比检出率 |
| `severity_distribution` | 缺陷严重度分布（轻微/一般/严重） |
| `equipment_params` | 设备参数趋势（温度/光源亮度等） |

- `chart_type`（14 种）：`auto` / `pie` / `bar` / `line` / `heatmap` / `stack` / `dual` / `dashboard` / `gauge` / `radar` / `funnel` / `rose` / `sunburst` / `treemap`。
- `_inherit_params`：追问时未提及的过滤条件（`factory/glass_type/defect_type/line_no/shift/severity/equipment/param_name/start_date/end_date`）继承上一轮（见 §15）。
- 未指定时间范围默认近 30 天。

### `data_execute`（执行）

- 调 `execute_query`（白名单 SQL，见 §17）。
- 查询失败不崩溃，写入 `query_params["error"]` 交给分析节点兜底。

### `data_analyze`（分析）

- 先 `build_insights` 生成确定性洞察（见 §16），再 `build_chart_option` 确定性生成图表。
- 调 LLM 生成三段式报告：数据摘要 / 异常与风险 / 优化建议，并结合「缺陷-工艺对照表」给建议。
- 无数据时友好提示；模型网关抖动时图表与洞察照常返回，不整条 500。

---

## 7. 诊断流（diagnosis）

文件：`app/graph/nodes/planner.py`（规划）、`app/graph/nodes/diagnosis.py`（取证 + 报告）

诊断流不一次性让 LLM 猜原因，而是「先规划调查维度 → 逐项用确定性工具验证 → 区分事实/推断/假设/建议」。

### `diagnosis_plan`（规划）

- `resolve_entities` 从问题抽取标准化实体（缺陷 / 玻璃类型）。
- 读库内厂家名识别厂家实体。
- 组装 `metric=multi` 的查询参数（默认近 30 天）。
- 生成调查计划 `plan`（趋势定位 → 厂家对比 → 缺陷构成 → 机理/工艺/案例 → 假设排序）。

### `diagnosis_run`（取证）

串行执行，构建 `Evidence` 证据链：

1. `execute_query` 取数（失败记 `fact` 证据，不崩溃）。
2. `build_evidence` 把确定性洞察（趋势/SPC/离群/帕累托）直接转结构化 `Evidence`。
3. 缺陷机理：先查 `DEFECT_GUIDANCE` 领域对照表（`inference`，置信度 0.6）。
4. 知识检索：`_retrieve_knowledge`（rewrite → embed → rerank），命中记 `fact`（置信度 0.5）。
5. 历史案例：`search_cases` 结构化匹配 + 症状向量相似度，取 Top 5，最相似记 `fact`（置信度 0.7）。
6. 长期记忆：`recall_factory_risks` 厂家历史风险（置信度 0.6）。

### `diagnosis_report`（报告）

- `build_chart_option` 生成图表（无数据则仅提示）。
- 调 LLM 输出四级报告：**事实 / 推断 / 假设 / 建议**，严格区分相关性与因果，标注「待验证」，结尾列引用来源。
- 模型不可用时回退输出确定性证据摘要。

---

## 8. 视觉分析流（vision）

文件：`app/graph/nodes/vision.py` + `app/tools/vision.py`

### 输入

- 合并 `image`（单图）与 `images`（多图），多图视为同一批现场照片（如同一块玻璃的不同角度）。
- 图片支持 URL 或 base64 Data URL。

### 逐张识别（`analyze_defect_image`）

视觉模型（`get_vision_llm`）先判 `content_type` 三分类，再按类型提取信息：

| `content_type` | 含义 | 提取字段 |
|---|---|---|
| `glass_defect` | 玻璃表面/内部缺陷照片 | `defect_type` / `severity` / `confidence` / `possible_causes` |
| `document` | 文档/报告/表格/文字图片 | `text_content`（逐行转录文字） |
| `other` | 无关图片（风景/人物/纯色等） | `description` |

### 分支处理

1. **全部为文档图、无缺陷图** → 提取文字（视觉转录不足时用本地 OCR 增强），交 LLM 归纳，明确说明"这是文档/文字图片"，不再套缺陷诊断。
2. **既非缺陷也非文档（无关图/空图）** → 友好提示"请上传玻璃表面/内部照片，或含文字的文档图片"。
3. **存在玻璃缺陷图** → 走缺陷诊断：为每个缺陷生成 `fact` 证据（视觉识别 + 置信度）与 `inference`（工艺成因对照），再知识检索 + 历史案例，最后 LLM 输出图片诊断意见（识别结果转述 → 可能成因 → 复核/改善动作）。

> 这一分类能力解决了「用户误传文字图片被当成玻璃缺陷分析」的问题：文档类图片会转述文字，而非输出"未知缺陷/置信度 0.1"这类误导性诊断。

---

## 9. 文件问答流（file_qa）

文件：`app/graph/nodes/file_qa.py`

- 输入来自 `state["file_texts"]`（`[{"filename","text"}]`，由上传接口解析后写入）。
- 每个文件用 `chunk_text` 切块，embedding 取与问题最相关的 Top 8 块（余弦相似度），失败则取前 8 块。
- 交 LLM 生成「仅依据上传文件内容、标注文件出处」的回答。
- **不落向量库**：文件文本为单次会话临时内容，不入部门知识库索引。
- 支持格式与入库解析一致（pdf/docx/pptx/xlsx/md/txt），见 §11。

---

## 10. 闲聊兜底（chat）

文件：`app/graph/nodes/chat.py`

- `is_gibberish` 命中 → 返回 `GIBBERISH_REPLY`，一句话简短忽略（不调 LLM、不携带历史）。
- `is_ack` 命中（"好的/嗯/谢谢/知道了/收到"等确认收尾词）→ 返回 `ACK_REPLY` 极简回应，**不调 LLM、不携带历史**，避免带上一轮上下文被 LLM 重复输出长篇内容。
- `is_greeting` 命中 → 用 `WELCOME_PROMPT` 热情介绍能力范围（数据分析 / 缺陷问答 / 多轮追问），150 字内。
- 其余 → `CHAT_PROMPT`（`temperature=0.7`），友好简短回应，自然引导回检测业务；仅携带最近 8 条历史消息。
- 与业务无关话题不做 RAG/查库，直接 LLM 生成；模型不可用时返回友好提示。

---

## 11. 检索与向量存储（rag）

### 文件结构

- `app/rag/ingest.py`：扫描 `KB_DIR` 下 `.md/.txt/.pdf/.docx/.pptx/.xlsx`，切块、向量化、落盘。
- `app/rag/loaders.py`：多格式解析（md/txt 直读；pdf 用 pypdf + PyMuPDF 图片页检测 + OCR；docx 用 python-docx；pptx 用 python-pptx；xlsx 用 openpyxl），并清理纯装饰符号行。
- `app/rag/ocr.py`：图片型 PDF 的 OCR 兜底（PaddleOCR / tesseract，可选依赖 `requirements-ocr.txt`）。
- `app/rag/store.py`：轻量向量存储 + 混合检索。
- `app/rag/query_rewrite.py`：查询改写。
- `app/rag/reranker.py`：RAG 重排序（精排）。

### 切块策略

- `CHUNK_SIZE = 500` 字符，`CHUNK_OVERLAP = 50`。
- 按空行分段聚合；超长段滑窗切分。
- 每个 chunk 附带元数据：`source`（文件名）、`doc_type`（standard/sop/equipment/case/knowledge）、`glass_type`、`defect_type`、`authority`（standard/sop 类 1.2，其余 1.0）。

### 混合检索排序（重点）

`store.search()` 采用 **语义余弦 + 词法 IDF 加权** 融合：

```
score = 0.55 * semantic + 0.45 * lexical
```

- **语义项**：query 与 chunk 的向量余弦相似度（归一化到 [0,1]）。
- **词法项**：查询特征（中文字符 bigram / 英数词）的 IDF 加权命中覆盖率。
- **为何混合**：本地 n-gram 向量的余弦对短查询噪声大，词法命中（尤其罕见术语如 ΔE/50mm/CS）能改善精确召回；远程语义 embedding 语义强，两者互补。

### 检索流程（rewrite → embed → rerank）

1. **查询改写** `rewrite_query`：确定性改写，识别到的缺陷/玻璃类型 canonical 词若原文未出现则追加，改善召回（`QUERY_REWRITE=false` 可关闭）。
2. **粗召回** `store.search(query_vec, k=rerank_candidates)`：上述混合检索。
3. **精排** `rerank`：四信号融合后取 Top `rerank_top_k`：
   ```
   score = 0.45 * semantic + 0.30 * lexical + domain + 0.10 * (authority - 1.0)
   ```
   - `domain`：查询缺陷匹配 +0.15，玻璃类型匹配 +0.10。
   - `authority`：文档权威度乘性加权（标准/判级/SOP 类 1.2）。

> `retrieve_top_k` 为早期配置（现仅保留在 `config.py`），当前检索实际由 `RERANK_CANDIDATES`（粗召回候选数）与 `RERANK_TOP_K`（精排后返回数）控制。

### Embedding 双后端

`app/llm.py` 支持：

| 后端 | 配置 | 说明 |
|---|---|---|
| 远程 | `EMBED_BACKEND=remote`（默认） | 走 OpenAI 兼容 embeddings 接口 |
| 本地 | `EMBED_BACKEND=local` | 内置 n-gram 哈希向量，无外部 API 依赖 |

---

## 12. 数据库层（db）

### 双后端支持（SQLite / MySQL）

`app/db/session.py` 通过 `DB_URL` 前缀自动选择后端，调用方无需感知差异：

| 后端 | `DB_URL` | 驱动 | 表结构文件 |
|---|---|---|---|
| SQLite | `sqlite:///data/inspection.db` | `sqlite3`（内置） | `schema.sql` |
| MySQL | `mysql://` | `pymysql` | `schema_mysql.sql` |

### 表结构（三张表，两端一致）

| 表 | 说明 |
|---|---|
| `factories` | 厂家（id, name, glass_type） |
| `inspection_records` | 检测记录（factory_id, glass_type, inspected_at, line_no, total_count, defect_count） |
| `defects` | 缺陷明细（record_id, defect_type, count, severity, confidence） |

### 关键函数（`app/db/session.py`）

| 函数 | 作用 |
|---|---|
| `get_conn()` | 按 `DB_URL` 返回 SQLite 或 MySQL 连接 |
| `is_mysql_backend()` | 判断当前是否为 MySQL 后端 |
| `adapt_sql(sql)` | 把 `?` 占位符适配为 MySQL 的 `%s` |
| `init_db()` | 按对应 schema 幂等建表（自动选择 schema 文件） |
| `init_mysql_database()` | 创建 MySQL 数据库本身（`CREATE DATABASE IF NOT EXISTS`） |
| `_exec_mysql_script()` | 按分号拆分执行 SQL（MySQL 无 `executescript`） |

### 已处理的双后端差异

| 差异点 | SQLite | MySQL | 处理 |
|---|---|---|---|
| 占位符 | `?` | `%s` | `adapt_sql()` 自动替换 |
| 自增主键 | `AUTOINCREMENT` | `AUTO_INCREMENT` | 独立 schema 文件 |
| 建表外键 | `PRAGMA foreign_keys` | `CONSTRAINT ... FOREIGN KEY` | 独立 schema 文件 |
| 除零保护 | `MAX(SUM(x),1)` | 不支持 | 统一 `NULLIF(SUM(x),0)` |
| 数值类型 | int/float | `Decimal` | `query_executor._rows()` 归一为 float |
| 结果行 | `sqlite3.Row` | `DictCursor` | `_rows()` 统一转 dict |

### 演示数据（`scripts/seed_db.py`）

生成 90 天、5 家厂家、L1/L2 两条产线的检测数据，并刻意埋了「晶捷电子玻璃近 30 天划伤上升」的可发现趋势，用于验证智能体的主动洞察能力。SQLite / MySQL 均可运行（内部用 `adapt_sql` 适配占位符）。

---

## 13. 配置体系（config）

文件：`app/config.py`，从 `.env` 读取（`python-dotenv`）。

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | `https://api.openai.com/v1` | LLM 接口地址（OpenAI 兼容） |
| `LLM_API_KEY` | `EMPTY` | API 密钥 |
| `LLM_MODEL` | `gpt-4o-mini` | 主模型名 |
| `LLM_VISION_MODEL` | （空） | 视觉（多模态图片输入）模型；为空回退 `LLM_MODEL` |
| `EMBEDDING_MODEL` | `text-embedding-ada-002` | embedding 模型名 |
| `EMBED_BACKEND` | `remote` | `local` 时用离线 n-gram 兜底（`llm.py` 直接读） |
| `DB_URL` | `sqlite:///data/inspection.db` | 数据库后端：`sqlite:///` 或 `mysql://` |
| `MYSQL_HOST` | `127.0.0.1` | MySQL 地址（`DB_URL=mysql://` 时生效） |
| `MYSQL_PORT` | `3306` | MySQL 端口 |
| `MYSQL_USER` | `root` | MySQL 用户名 |
| `MYSQL_PASSWORD` | ``（空） | MySQL 密码 |
| `MYSQL_DATABASE` | `glass_inspection` | MySQL 库名 |
| `MYSQL_CHARSET` | `utf8mb4` | MySQL 字符集 |
| `KB_DIR` | `data/docs` | 知识库文档目录 |
| `VECTOR_STORE_PATH` | `data/vector_store.json` | 向量索引路径 |
| `RETRIEVE_TOP_K` | `12` | （历史保留，当前检索由 rerank 参数控制） |
| `RERANK_CANDIDATES` | `30` | 重排粗召回候选数 |
| `RERANK_TOP_K` | `6` | 重排后最终返回片段数 |
| `QUERY_REWRITE` | `true` | 查询改写开关 |
| `LLM_TIMEOUT` | `60` | LLM 调用超时（秒） |
| `LLM_MAX_RETRIES` | `2` | LLM 调用重试次数 |
| `DIAGNOSIS_ENABLED` | `true` | 诊断流开关（关闭后 router 不路由到 diagnosis） |
| `LOG_LEVEL` | `INFO` | 结构化日志级别 |
| `QA_FALLBACK` | `true` | 知识库未收录时是否回退通用知识 |

---

## 14. LLM 与 Embedding 工厂（llm）

文件：`app/llm.py`

- `get_llm(temperature=0.2)`：返回 `ChatOpenAI` 实例（惰性创建），已内置 `timeout` / `max_retries`。
- `get_vision_llm(temperature=0)`：视觉模型工厂，`model = llm_vision_model or llm_model`，用于图片缺陷识别，与主文本模型可分离。
- `get_embeddings()`：按 `EMBED_BACKEND` 返回远程或本地 embedding。
- `LocalNGramEmbedding`：中文字符 n-gram 哈希 + 词频加权 + 归一化，离线兜底。

---

## 15. 多轮对话机制

### 记忆来源

- LangGraph 的 `MemorySaver` checkpointer 按 `thread_id` 持久化历史。
- 新增的 `last_intent` / `last_query_params` 字段在节点间流转，实现跨轮延续。

### 追问意图继承

`router_node` 中：当关键词未命中、且当前句子是「明确指代追问」时，`intent = last_intent`。意图分类的 LLM 兜底只看当前消息，避免历史数据报告带偏判断。

**典型场景**：

```
用户: 晶捷电子玻璃近两周划伤趋势     → data（关键词命中"趋势"）
用户: 那按厂家再对比下               → 关键词命中"厂家/对比"，仍 data
用户: 还有吗                        → 追问继承，data
```

### 查询参数继承

`data_parse` 中 `_inherit_params`：追问时未提及的 `factory/glass_type/defect_type/start_date/end_date` 继承上一轮。

**典型场景**：

```
用户: 晶捷电子玻璃近两周划伤趋势     → factory=晶捷, glass_type=电子玻璃, 近两周
用户: 那按厂家对比下                 → 继承 factory/glass_type/时间，只改 metric=rate_by_factory
```

---

## 16. 主动洞察机制（insights）

文件：`app/tools/insights.py`（本次优化新增）

**确定性统计**（不依赖 LLM，数字可复现），产出结构化洞察文本，供 `data_analyze` 注入报告：

| 函数 | 作用 | 阈值策略 |
|---|---|---|
| `detect_trend_spike` | 趋势突变检测 | `min(mean+2σ, mean*1.5)`，小样本友好 |
| `detect_factory_outlier` | 厂家离群检测 | `min(mean+1.5σ, mean*1.3)` |
| `_pct_diff` | 环比变化率 | `(cur-prev)/prev*100` |
| `top_defect_signals` | Top 缺陷占比摘要 | Top3 + 占比 |
| `build_insights` | 汇总各指标洞察 | 按 metric 分派 |
| `build_evidence` | 洞察转结构化证据 | 供诊断流用（事实/置信度） |

**设计价值**：LLM 只负责「润色整合」，不负责「算数字」，避免幻觉；洞察数字由代码保证准确。

---

## 17. 安全设计

| 威胁 | 防护 |
|---|---|
| SQL 注入 | 查询执行器只用预定义指标 + 参数绑定（`?`→`%s` 由 `adapt_sql` 适配），LLM 永不生成 SQL |
| 越权查询 | `metric` 白名单（`if/elif`），非法值抛异常 |
| 术语歧义 | `app/domain/` 统一术语口径（"杂志"→"杂质"） |
| 路由失败 | LLM 路由异常时退化为 `chat`，保证服务可用 |
| 查询失败 | `data_execute` 捕获异常，不崩溃，交给分析节点兜底提示 |

---

## 18. 已知限制与坑

| 限制 | 说明 | 应对 |
|---|---|---|
| **内存会话** | `MemorySaver` 服务重启丢历史；多 worker 不共享 | 生产换 `SqliteSaver`/`PostgresSaver` |
| **无鉴权** | 接口裸奔 | 网关层统一认证 |
| **Python 3.9 兼容** | `dict \| None` 注解需 `from __future__ import annotations` | 已在相关文件加 future import |
| **多图看板需前端配合** | 单图指标返回裸 option 可直接 `setOption`；`overview/multi` 返回 `layout=dashboard` 的 `kpis+charts` 容器，需前端按 layout 分支循环渲染 | 已在 `api.md` 给出分支渲染示例 |
| **无评估集** | 无 LLM 效果回归 | 建 20~50 条问答对评估 |
| **OCR 依赖 numpy 版本冲突** | PaddleOCR/paddlepaddle 依赖链在 NumPy 2.x 下会报 `_ARRAY_API not found` 崩溃 | 使用 OCR 时 `pip install "numpy<2"`（可选依赖；视觉模型本身可读文字图，OCR 仅扫描版 PDF 兜底） |
| **视觉识别依赖模型能力** | 缺陷/文档图片分类与转录由视觉模型决定，复杂场景置信度可能偏低 | 已用 `content_type` 三分类缓解误判；关键批次建议人工复核 |

---

## 附：文件清单速查

| 文件 | 职责 |
|---|---|
| `app/main.py` | FastAPI 入口 + 4 个接口（含文件上传） |
| `app/config.py` | 环境配置 |
| `app/logging.py` | 结构化日志 |
| `app/domain/ontology.py` | 缺陷/玻璃类型/指标/工艺建议（DEFECT_GUIDANCE） |
| `app/domain/terms.py` | 术语标准化（别名归一 + 实体解析） |
| `app/llm.py` | LLM/Embedding 工厂（含 get_vision_llm） |
| `app/state.py` | AgentState |
| `app/graph/builder.py` | 图组装（含 route_start 分流） |
| `app/graph/nodes/router.py` | 意图路由 |
| `app/graph/nodes/qa.py` | 问答流 |
| `app/graph/nodes/data.py` | 数据流 |
| `app/graph/nodes/diagnosis.py` | 诊断流（取证 + 报告） |
| `app/graph/nodes/planner.py` | 诊断规划 |
| `app/graph/nodes/vision.py` | 视觉分析流 |
| `app/graph/nodes/file_qa.py` | 文件问答流 |
| `app/graph/nodes/chat.py` | 闲聊 |
| `app/rag/ingest.py` | 知识库入库（多格式） |
| `app/rag/loaders.py` | 多格式解析 |
| `app/rag/ocr.py` | 图片型 PDF OCR |
| `app/rag/query_rewrite.py` | 查询改写 |
| `app/rag/reranker.py` | RAG 重排序 |
| `app/rag/store.py` | 向量存储 + 混合检索 |
| `app/cases/store.py` | 历史案例检索（search_cases） |
| `app/memory/long_term.py` | 厂家长期风险记忆（recall_factory_risks） |
| `app/tools/query_executor.py` | 白名单 SQL 执行 |
| `app/tools/chart_builder.py` | ECharts 生成 |
| `app/tools/insights.py` | 主动洞察计算（build_evidence） |
| `app/tools/vision.py` | 视觉识别工具（content_type 三分类） |
| `app/db/session.py` | DB 连接（SQLite / MySQL 双后端） |
| `app/db/schema.sql` | 表结构（SQLite） |
| `app/db/schema_mysql.sql` | 表结构（MySQL） |
| `scripts/seed_db.py` | 演示数据（双后端） |
| `tests/test_graph.py` | 冒烟测试 |
