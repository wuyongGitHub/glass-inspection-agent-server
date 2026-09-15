# 玻璃检测部门智能体（LangGraph）

面向玻璃工业视觉检测部门的内部业务智能体，基于 LangGraph 构建。业务背景：通过相机 + 光源采集图像，YOLO 模型检测建筑 / 家电 / 电子玻璃缺陷（气泡、崩边、杂质、结石、划伤、倒角不良、字符缺失、漏墨、缺印、油墨不良、麻点等），检测结果上云分析并生成报表大屏与优化建议。

包含四条核心业务流（外加闲聊兜底）：

- **问答流（qa）**：基于部门知识库的 RAG 问答 —— 缺陷判定标准、工艺知识、SOP、光源 / 相机选型
- **数据查询流（data）**：自然语言查询云端检测数据（概览看板、检出率/缺陷量/检测量趋势、厂家对比、Top 缺陷、厂家缺陷构成分布），自动生成分析报告、优化建议与 ECharts 大屏图表配置
- **诊断流（diagnosis）**：面向"为什么 XX 变多 / 偏高"的根因调查 —— 实体抽取 → 调查计划 → 数据取证 + 知识/案例检索 + 证据构建 → 输出「事实 / 推断 / 假设 / 建议」四级可信度报告
- **视觉分析流（vision）**：上传现场图片，视觉大模型识别缺陷 → 检索知识库/历史案例 → 输出图片诊断意见；自动区分「玻璃缺陷图 / 文档文字图 / 无关图」，文档类图片提取文字而非套缺陷诊断
- **文件问答流（file_qa）**：上传文档（pdf/docx/pptx/xlsx/md/txt），解析文字后即时问答（不落向量库）

## 架构

```
START ──┬─ 带图片 ── vision_analyze（视觉识别缺陷 → 知识/案例检索 → 诊断意见）── END
        ├─ 带文件 ── file_qa（上传文件文本即时问答）── END
        └─ 文本 ─── router（意图识别：业务关键词规则前置 + LLM 结构化输出兜底）
                     ├─ qa        → qa_retrieve（向量检索）→ qa_generate（带引用回答）→ END
                     ├─ data      → data_parse（参数解析）→ data_execute（白名单安全 SQL）→ data_analyze（报告+图表+建议）→ END
                     ├─ diagnosis → diagnosis_plan（实体+调查计划+参数）→ diagnosis_run（数据/知识/案例/证据取证）→ diagnosis_report（四级可信度报告）→ END
                     └─ chat      → chat_reply → END
```

- 全部状态由 `AgentState` 携带，`MemorySaver` checkpointer 支持多轮对话（生产环境替换为 `SqliteSaver` / `PostgresSaver`）
- 数据查询不生成自由 SQL，而是**预定义指标 + 参数绑定**，杜绝注入与越权
- 图表由代码确定性生成 ECharts option，LLM 只负责分析文字，稳定可上大屏
- 图表形态丰富：折线 / 柱状 / 横向排行 / 环形饼 / 双轴组合 / 堆叠 / 热力图；`overview` 与综合分析默认返回 `{layout:"dashboard", kpis, charts[]}` 多图看板
- 支持多轮追问与指代继承（"那按厂家再对比下"能延续上一轮上下文）
- **会话收尾识别**：对"好的/嗯/谢谢/知道了"等确认收尾词做极简回应，不重复上一轮长篇内容
- 数据分析内置主动洞察：趋势突变检测、厂家离群识别、环比计算、Top 缺陷根因归因
- **证据引擎（Evidence）**：所有数学 / 统计结论由确定性代码产生，每条证据携带 `metric / value / baseline / source / confidence / calculation_method`，严格区分「事实 / 推断 / 假设 / 建议」，杜绝 LLM 数字幻觉
- **SPC 过程控制**：单值控制图（均值 ± kσ）+ Western Electric 判异规则，输出受控/失控结论作为诊断证据
- **历史案例库（Case RAG）**：结构化字段匹配 + 症状向量相似度检索，沉淀「问题 → 原因 → 措施 → 效果」经验资产
- **长期业务记忆**：厂家历史异常、产线风险、已确认案例、用户偏好，可追溯（`updated_at`）、可删除、可人工修正

## 详细文档

| 文档 | 说明 |
|---|---|
| [`docs/PROJECT_HANDBOOK.md`](docs/PROJECT_HANDBOOK.md) | **超级详细架构与落地手册（推荐先读）**：业务背景、技术栈、五条业务流、RAG 链路、领域层、工具层、记忆系统、案例库、评测体系、环境搭建、部署运行、已知限制 |
| [`docs/DETAILED.md`](docs/DETAILED.md) | 详细技术说明：架构、状态模型、各节点实现、检索排序、多轮机制、主动洞察、安全设计、已知限制 |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | 服务器部署指南：systemd / Docker / Nginx / 真实数据库接入 / 生产化改造清单 |
| [`docs/api.md`](docs/api.md) | HTTP 接口文档 |

## 目录结构

```
glass-inspection-agent-server/
├── app/
│   ├── main.py               # FastAPI 入口（/health、/api/chat、/api/chat/upload、/api/chat/stream）
│   ├── config.py             # 环境配置（模型 / 数据库 / 检索 / 诊断开关等）
│   ├── llm.py                # LLM / Embedding 工厂（OpenAI 兼容接口）
│   ├── logging.py            # 结构化 JSON 日志（intent / thread_id / 耗时）
│   ├── state.py              # AgentState 全局状态定义
│   ├── domain/
│   │   ├── ontology.py       # 领域本体：玻璃类型 / 缺陷 / 指标 / 工艺 白名单常量
│   │   └── terms.py          # 术语标准化：同义词/别名 → canonical 实体
│   ├── graph/
│   │   ├── builder.py        # 主图组装（router 分流 + vision 图片入口）
│   │   └── nodes/            # router / qa / data / diagnosis / vision / file_qa / chat / planner 节点
│   ├── rag/                  # RAG 知识库：ingest / loaders / ocr / query_rewrite / reranker / store
│   ├── tools/
│   │   ├── query_executor.py # 查询执行器（白名单安全 SQL，双后端适配）
│   │   ├── chart_builder.py  # ECharts 图表确定性生成
│   │   ├── insights.py       # 主动洞察（趋势/离群/环比/归因）
│   │   ├── statistics.py     # 工业统计：Pareto / 均值标准差 / 异常点 / 趋势漂移 / 相关性
│   │   ├── spc.py            # SPC 控制图 + Western Electric 判异规则
│   │   ├── evidence.py       # 统一证据结构（事实/推断/假设/建议）
│   │   └── vision.py         # 多模态视觉缺陷识别工具
│   ├── memory/
│   │   ├── short_term.py     # 短期会话上下文提取
│   │   └── long_term.py      # 长期业务记忆（厂家/产线风险、案例、偏好）
│   ├── cases/store.py        # 历史案例库 Case RAG（检索 + 沉淀）
│   ├── evaluation/           # 离线评测（路由 + 实体抽取准确率）
│   └── db/                   # 双后端连接（SQLite / MySQL）+ schema.sql / schema_mysql.sql
├── scripts/
│   ├── seed_db.py            # 生成 90 天演示检测数据（SQLite / MySQL 均可用）
│   └── eval_benchmark.py     # 运行离线评测（路由 + 实体抽取准确率）
├── tests/                    # 图构建冒烟测试等
├── docs/                     # 详细技术说明 + 部署指南 + API 文档
└── data/docs/                # 知识库文档（示例 + 替换为部门真实文档）
```

## 快速开始

```bash
cd glass-inspection-agent-server
python -m venv .venv
# Windows: .venv\Scripts\activate   Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt

copy .env.example .env        # 编辑：填入模型 API 地址与密钥，以及数据库连接

# 首次初始化数据库（SQLite 或 MySQL，二选一）
python scripts/seed_db.py     # 自动建表 + 生成 90 天演示数据

python -m app.rag.ingest      # 构建知识库向量索引（data/vector_store.json）

uvicorn app.main:app --reload --port 8000
```

### 数据库：SQLite 或 MySQL 二选一

通过 `.env` 的 `DB_URL` 切换，代码自动适配两种后端的 SQL 方言与占位符：

```ini
# 方式一：MySQL（生产推荐，本地已有 MySQL 8.0 时用这个）
DB_URL=mysql://
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的密码
MYSQL_DATABASE=glass_inspection
MYSQL_CHARSET=utf8mb4

# 方式二：SQLite（演示/无数据库时）
# DB_URL=sqlite:///data/inspection.db
```

> **MySQL 首次使用**：需先创建数据库。可运行下面命令自动建库（再用 `seed_db.py` 建表灌数据）：
> ```bash
> python -c "from app.db.session import init_mysql_database; init_mysql_database()"
> python scripts/seed_db.py
> ```

## 启动命令：
> cd e:\gitLab\glass-inspection-agent
> .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

## 是否打开成功
1. 浏览器直接看（最直观）
打开 http://127.0.0.1:8000/docs，能看到 Swagger 接口文档页 = 服务活着。
打开 http://127.0.0.1:8000/health，浏览器里显示 {"status":"ok"} = 正常。

## API

### POST /api/chat

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "最近一个月各厂家的检出率对比", "thread_id": "user-001"}'
```

请求体字段：`message`（问题）、`thread_id`（会话 ID，同一 ID 携带多轮上下文）、`image`（可选单图，图片 URL 或 base64 Data URL）、`images`（可选多图列表）、`file_texts`（可选，上传文件解析结果 `[{"filename","text"}]`）。存在图片走视觉分析流，存在文件文本走文件问答流。

返回：`{"intent": "data", "answer": "...", "chart": <ECharts option 或 {"layout":"dashboard","kpis":[...],"charts":[...]} 多图看板容器>}`，`intent` 可能为 `qa / data / diagnosis / vision / file_qa / chat`。

视觉流示例（传图片走视觉缺陷识别）：

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "帮我看看这块玻璃有什么缺陷", "image": "https://.../defect.jpg", "thread_id": "user-001"}'
```

### GET /api/chat/stream（SSE 流式）

`http://127.0.0.1:8000/api/chat/stream?q=气泡的判定标准是什么&thread_id=user-001`

### GET /health

## 示例问题

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
| 诊断 | `为什么晶捷电子玻璃近两周划伤变多了` |
| 诊断 | `气泡异常增多是什么原因，帮我定位一下` |
| 视觉 | `上传现场图片，问"这块玻璃有什么缺陷"` |

### 知识库未收录的主题

问答流默认开启"通用知识回退"：当检索不到与问题相关的知识库片段时，模型会用自身通用知识
（即 `.env` 中配置的 LLM，DeepSeek 等 OpenAI 兼容模型均可）回答，并明确标注"非部门检验标准口径"。
若要求严格只答知识库内容，可在 `.env` 中设置 `QA_FALLBACK=false`（此时将提示补充文档）。

## 接入真实环境

1. **模型**：`.env` 中配置 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`，任何 OpenAI 兼容接口均可（含私有化部署模型）；视觉（多模态图片输入）模型可用 `LLM_VISION_MODEL` 单独指定（为空则回退 `LLM_MODEL`）
2. **数据库**：`.env` 设 `DB_URL=mysql://` 并填写 `MYSQL_*` 连接参数；MySQL 表结构见 `app/db/schema_mysql.sql`（SQLite 为 `schema.sql`），字段与上云检测结果对齐（把 YOLO 上云写入的表映射过来即可）。代码已内置 SQLite / MySQL 双后端适配（占位符、方言、`Decimal` 序列化均已处理），无需改代码
3. **知识库**：把部门真实文档放入 `data/docs/`，重新运行 `python -m app.rag.ingest`。支持 `.md / .txt / .pdf / .docx / .pptx / .xlsx`（老式 `.xls` 请另存为 `.xlsx`）。扫描件/图片型 PDF 已内置 OCR 兜底：可选安装 `pip install -r requirements-ocr.txt`（未安装则自动跳过 OCR，不影响文字型 PDF 入库；OCR 识别较慢，建议大文件分批）
4. **缺陷-工艺建议**：`app/domain/ontology.py` 的 `DEFECT_GUIDANCE` 是优化建议的知识来源，按部门经验持续补充

> 术语约定：需求中的"杂志"按"杂质"处理；"倒角"按缺陷项"倒角不良"处理，如与部门口径不同请直接改 `app/domain/ontology.py`。

## 离线评测

内置确定性离线评测（不依赖 LLM / 数据库，可秒级回归）：

```bash
python scripts/eval_benchmark.py
```

输出路由意图准确率、实体（缺陷 / 玻璃类型）抽取准确率，并列出失败样本。评测样本基线见 `app/evaluation/samples.py`（覆盖闲聊 / 数据 / 问答 / 诊断 / 实体抽取 / 乱码边界）。

## 扩展路线

1. 接入企业微信 / 钉钉机器人（在 FastAPI 前加回调路由）
2. 高风险写操作（如自动派单、推送通知）增加 `interrupt` 人工审批节点
3. 报表定时推送（定时任务调 graph + 消息通道）
4. 对接 YOLO 检测服务（新增一个 tool：提交图片 / 查询单批次明细）
5. 评估集：把部门高频问题整理成 20~50 条问答对，回归验证效果
