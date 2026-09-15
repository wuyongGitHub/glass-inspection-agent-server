# 玻璃检测部门智能体 — HTTP API 文档

> 本文档描述 `app/main.py` 对外暴露的全部接口（共 3 个业务接口 + 1 个健康检查）。
> 服务基于 **FastAPI + LangGraph**，三个业务接口最终都进入同一个智能体图 `build_graph()` 执行。

---

## 1. 基础信息

| 项目 | 内容 |
|---|---|
| 服务框架 | FastAPI + LangGraph |
| 启动命令 | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 服务地址 | `http://<host>:8000` |
| Swagger UI | `GET /docs` |
| ReDoc | `GET /redoc` |
| OpenAPI JSON | `GET /openapi.json` |
| 鉴权 | 无（当前内网裸奔，建议由网关层统一加认证） |

> 注意：本项目为**纯后端服务**，不包含任何前端页面。聊天界面 / 大屏需由前端调用下列接口实现。

---

## 2. 接口一览

| 方法 | 路径 | 说明 | 入参位置 |
|---|---|---|---|
| `GET` | `/health` | 健康检查 | 无 |
| `POST` | `/api/chat` | 一问一答（非流式），返回意图 + 文字答案 + 可选图表；支持多图（URL/base64）与文件文本 | JSON Body |
| `POST` | `/api/chat/upload` | 多图多文件一站式上传问答（图片走视觉流，文档走文件问答流） | multipart/form-data |
| `GET` | `/api/chat/stream` | 流式对话（SSE），逐节点推送执行过程 | URL Query |

---

## 3. 接口详情

### 3.1 `GET /health` — 健康检查

服务探活接口。

**响应示例：**

```json
{ "status": "ok" }
```

---

### 3.2 `POST /api/chat` — 普通对话（主接口）

聊天窗口 / 大屏的主入口。输入一句自然语言，智能体自动完成「意图识别 → 分流执行 → 生成回复」。

**请求头：** `Content-Type: application/json`

**请求体（Body）：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `message` | string | 是 | — | 用户问题，如"气泡的判定标准"、"各厂家检出率对比" |
| `thread_id` | string | 否 | `"default"` | 会话 ID。同一 ID 携带多轮上下文（LangGraph MemorySaver）；服务重启后历史丢失 |
| `image` | string | 否 | — | 现场图片 URL 或 base64 Data URL（单图，向后兼容）。存在时走视觉分析流 |
| `images` | string[] | 否 | — | 多张图片 URL 或 base64 Data URL。存在时优先走视觉分析流 |
| `file_texts` | object[] | 否 | — | 上传文件的解析结果，结构 `[{"filename": string, "text": string}]`。存在且无图时走文件即时问答流 |

**请求示例：**

```json
{
  "message": "最近一个月各厂家的检出率对比，并给出建议",
  "thread_id": "zhangsan"
}
```

**响应体：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `intent` | string | 意图分类，见下表 |
| `answer` | string | 最终回复文字（Markdown 格式） |
| `chart` | object \| null | `intent=data` 时为图表配置，见下方「chart 的两种形态」；否则为 `null` |

**`intent` 取值：**

| 取值 | 含义 | 触发场景示例 |
|---|---|---|
| `qa` | 知识问答（RAG） | 缺陷定义、判级标准、SOP、设备选型 |
| `data` | 数据分析（查库 + 报表/图表） | 检出率、趋势、厂家对比、Top 缺陷 |
| `diagnosis` | 诊断流（多指标取证 + 根因分析） | "划伤根因是什么"、"气泡成因诊断" |
| `vision` | 视觉分析（图片识别缺陷 + 诊断） | 传入图片（`image`/`images`）时 |
| `file_qa` | 文件即时问答（基于上传文件文本） | 传入 `file_texts` 或 `/api/chat/upload` 的文档时 |
| `chat` | 闲聊/无关话题 | 问候、确认收尾词（好的/谢谢）、非业务问题 |

> 路由失败时服务端会安全退化为 `chat`，保证可用性。

**响应示例（`intent=data` 时，chart 结构以实际返回为准）：**

```json
{
  "intent": "data",
  "answer": "近 30 天共检出缺陷 1234 件，整体检出率 3.2%。各厂家对比：A厂 43%、B厂 38%、C厂 19%。建议优先关注 A 厂的气泡与划伤缺陷。",
  "chart": {
    "tooltip": { "trigger": "axis" },
    "xAxis": { "type": "category", "data": ["A厂", "B厂", "C厂"] },
    "yAxis": { "type": "value", "name": "检出率(%)" },
    "series": [{ "type": "bar", "data": [3.8, 3.1, 2.4] }]
  }
}
```

**curl 示例（Windows PowerShell）：**

```bash
curl.exe -X POST http://127.0.0.1:8000/api/chat ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"气泡的判定标准是什么？\",\"thread_id\":\"demo\"}"
```

**前端调用示例：**

```js
const res = await fetch("/api/chat", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ message: "气泡的判定标准", thread_id: "demo" }),
});
const data = await res.json();

// intent: qa | data | chat
console.log(data.intent, data.answer);

// 单图：本身就是 ECharts option，直接交给 ECharts
if (data.chart && data.chart.layout !== "dashboard") {
  chart.setOption(data.chart);
}
// 看板容器：KPI 数字卡 + 逐张渲染内部 charts
if (data.chart && data.chart.layout === "dashboard") {
  renderKpis(data.chart.kpis);              // [{key,label,value,unit}] 顶部数字卡
  data.chart.charts.forEach((item, i) =>
    renderChart(`chart-${i}`, item.option || item)  // 兼容纯 option 旧格式
  );
}
```

> **chart 的两种形态：**
> - **单图**（按天趋势、厂家对比、缺陷排行/饼图、堆叠/热力、双轴组合等）：`chart` 就是标准 ECharts option，`setOption(chart)` 即可。
> - **看板**（`overview` 与 `multi` 指标默认返回）：结构为
>   `{ "layout": "dashboard", "title": "综合分析看板", "kpis": [{key,label,value,unit}], "charts": [{ "option": {...}, "span": 6|12 }, ...] }`。
>   `kpis` 用于顶部数字卡；`charts` 每项包含子图 `option` 与版面提示 `span`：
>   - `span = 12`：整行展示（趋势、热力等高信息密度图）；
>   - `span = 6`：半行展示，可两两并排成一行，版面更紧凑自然；
>   旧格式中 `charts` 元素为纯 option 时按整行（span=12）处理。

---

### 3.3 `POST /api/chat/upload` — 多图多文件上传问答

在一条请求里同时上传多张图片和多个文档文件，由服务端自动分流：图片（jpg/png/webp/gif/bmp）转为 base64 Data URL 走**视觉分析流**；文档（pdf/docx/pptx/xlsx/md/txt）解析文字后走**文件即时问答流**。若同时含图片与文档，图片优先。

**请求头：** `Content-Type: multipart/form-data`

**表单字段：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `message` | string | 是 | — | 用户问题 |
| `thread_id` | string | 否 | `"default"` | 会话 ID，同 `POST /api/chat` |
| `images` | file[] | 否 | — | 图片文件，可传多张（表单字段名 `images` 出现多次） |
| `files` | file[] | 否 | — | 文档文件，可传多个（表单字段名 `files` 出现多次） |

**响应体：** 与 `POST /api/chat` 一致（`intent` / `answer` / `chart`），此时 `intent` 为 `vision` 或 `file_qa`。

**curl 示例（Windows PowerShell）：**

```bash
curl.exe -X POST http://127.0.0.1:8000/api/chat/upload ^
  -F "message=分析这几张图并总结这份文档" ^
  -F "images=@1.jpg" ^
  -F "images=@2.png" ^
  -F "files=@report.pdf"
```

**前端调用示例：**

```js
const fd = new FormData();
fd.append("message", "分析这几张图");
fd.append("images", fileInput.files[0]);  // 多张则多次 append
fd.append("files", docInput.files[0]);

const res = await fetch("/api/chat/upload", { method: "POST", body: fd });
const data = await res.json();
console.log(data.intent, data.answer);
```

> 说明：文件解析在本次请求内即时完成，**不写入知识库**；如需入库请离线运行 `python -m app.rag.ingest`。扫描型 PDF 会触发 OCR，首次可能较慢。

---

### 3.4 `GET /api/chat/stream` — 流式对话（SSE）

把智能体图各节点（路由 → 检索/执行 → 生成）的状态增量实时推送，适合做「思考过程可视化」。

**Query 参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `q` | string | 是 | — | 用户问题 |
| `thread_id` | string | 否 | `"default"` | 会话 ID，同 `POST /api/chat` |

**响应：** `Content-Type: text/event-stream`

每行一帧，形如 `data: <json>\n\n`：

```
data: {"router": {"intent": "data"}}

data: {"data_parse": {"query_params": {"metric": "defect_rate", "days": 30}}}

data: {"data_execute": {"query_rows": 90}}

data: {"report_generate": {"final_answer": "…", "chart_config": {…}}}

data: [DONE]
```

> 帧格式：`{"<节点名>": {<该节点对状态的新增/变更>}}`；收到 `[DONE]` 表示整轮结束。
> 节点名与字段以 `graph.stream` 实际输出为准（随图结构演进）。

**curl 示例：**

```bash
curl.exe -N "http://127.0.0.1:8000/api/chat/stream?q=%E4%BB%8A%E5%A4%A9%E5%90%84%E5%8E%82%E6%8D%9F%E8%80%97%E6%80%8E%E4%B9%88%E6%A0%B7&thread_id=demo"
```

---

## 4. 前端对接建议

| 场景 | 推荐接口 |
|---|---|
| 一次性问答（机器人卡片、大屏摘要） | `POST /api/chat` |
| 类 ChatGPT 逐字/逐步输出 | `GET /api/chat/stream`（SSE） |
| 依赖图表的数据分析 | `POST /api/chat`，有 `chart` 直接用 ECharts 渲染 |
| 上传图片/文档并即时问答 | `POST /api/chat/upload`（multipart） |
| 多用户隔离 | 每个用户分配独立 `thread_id` |

---

## 5. 错误与限制

- **无自定义错误码**：异常走 HTTP 标准错误；LLM 网关超时/不可用时一般为 `500`，根因需查看服务端日志。
- **无鉴权**：接口未做鉴权与限流，暴露到公网前必须加网关认证。
- **会话易失**：对话记忆保存在进程内存（`MemorySaver`），服务重启即清空。
- **数据安全**：数据分析不生成自由 SQL，仅走预定义指标 + 参数绑定（见架构说明），接口侧无需额外注入防护。
