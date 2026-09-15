# 玻璃检测部门智能体 — 服务器部署指南

> 本文档描述如何把本智能体部署到 Linux 服务器（生产环境）。
> 本地开发运行见根目录 `README.md`。技术细节见 `docs/DETAILED.md`。

---

## 目录

1. [部署架构概览](#1-部署架构概览)
2. [前置准备](#2-前置准备)
3. [手工部署（推荐入门）](#3-手工部署推荐入门)
4. [systemd 托管（推荐生产）](#4-systemd-托管推荐生产)
5. [Nginx 反向代理](#5-nginx-反向代理)
6. [Docker 部署](#6-docker-部署)
7. [接入真实数据库（MySQL/PostgreSQL）](#7-接入真实数据库mysqlpostgresql)
8. [生产化改造清单（必读）](#8-生产化改造清单必读)
9. [监控与日志](#9-监控与日志)
10. [常见问题排查](#10-常见问题排查)

---

## 1. 部署架构概览

```
[浏览器/前端/大屏]
        │  HTTPS
        ▼
[ Nginx 反向代理 + TLS ]   ← 对外唯一入口
        │  HTTP (内网)
        ▼
[ Uvicorn 多进程 (gunicorn) ]
        │
        ├── LangGraph 智能体（app）
        │
        ├── LLM 网关（DeepSeek / 私有化 OpenAI 兼容模型）
        │
        ├── 向量索引（vector_store.json）
        │
        └── 数据库（MySQL，已内置支持；SQLite 仅演示）
```

**关键决策点**：

| 项 | 演示/单机 | 生产 |
|---|---|---|
| 会话存储 | `MemorySaver`（内存） | `SqliteSaver` 或 `PostgresSaver` |
| 数据库 | SQLite / MySQL | MySQL |
| 进程 | 单 uvicorn | gunicorn + N worker |
| 反向代理 | 无 | Nginx + TLS |
| 鉴权 | 无 | 网关层统一认证 |

> **MySQL 已内置支持**：代码已实现 SQLite / MySQL 双后端，通过 `.env` 的 `DB_URL` 切换（无需改代码）。占位符（`?`→`%s`）、SQL 方言（`AUTO_INCREMENT`、`NULLIF` 除零保护）、`Decimal` 序列化均已处理。

---

## 2. 前置准备

### 2.1 服务器规格建议

| 指标 | 最低 | 推荐 |
|---|---|---|
| CPU | 2 核 | 4 核 |
| 内存 | 4 GB | 8 GB |
| 磁盘 | 20 GB | 50 GB+（视知识库/数据量） |
| 系统 | Ubuntu 20.04+ / CentOS 7+ | Ubuntu 22.04 LTS |

> 智能体本身不重（CPU 密集主要在 embedding 本地模式）；主要开销是 LLM 网关调用延迟。

### 2.2 安装依赖

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git nginx
# 如需 MySQL：sudo apt install -y mysql-server
```

---

## 3. 手工部署（推荐入门）

### 3.1 拉取代码

```bash
cd /opt
sudo git clone <你的仓库地址> glass-inspection-agent
cd glass-inspection-agent
```

### 3.2 创建虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# 生产需额外安装：
pip install "uvicorn[standard]" gunicorn
```

### 3.3 配置环境变量

```bash
cp .env.example .env
vim .env
```

必填项（示例，改成你的真实值）：

```ini
LLM_BASE_URL=https://newapi.jubocloud.com/v1
LLM_API_KEY=sk-你的真实密钥
LLM_MODEL=deepseek-v4-flash
# 视觉（多模态图片输入）模型；为空时回退使用 LLM_MODEL
LLM_VISION_MODEL=
EMBEDDING_MODEL=text-embedding-ada-002

# LLM 调用健壮性
LLM_TIMEOUT=60        # 单次调用超时（秒）
LLM_MAX_RETRIES=2     # 失败自动重试次数

# 诊断流开关（默认 true）
DIAGNOSIS_ENABLED=true
# 日志级别（DEBUG/INFO/WARNING/ERROR）
LOG_LEVEL=INFO

# 数据库：MySQL（推荐生产）
DB_URL=mysql://
MYSQL_HOST=你的MySQL地址
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的密码
MYSQL_DATABASE=glass_inspection
MYSQL_CHARSET=utf8mb4

# 或用 SQLite（演示，二选一）
# DB_URL=sqlite:///data/inspection.db
```

> 若无远程 embedding 模型，设置 `EMBED_BACKEND=local` 走离线 n-gram 兜底。

### 3.4 初始化数据与知识库

```bash
# MySQL 首次：先建库（自动创建 glass_inspection），再建表 + 灌演示数据
python -c "from app.db.session import init_mysql_database; init_mysql_database()"
python scripts/seed_db.py        # 建表 + 演示数据（生产可跳过，接入真实上云表）

python -m app.rag.ingest         # 构建向量索引
```

### 3.5 启动

```bash
# 前台测试
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# 后台运行
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 &
```

验证：

```bash
curl http://127.0.0.1:8000/health
# 期望输出 {"status":"ok"}
```

---

## 4. systemd 托管（推荐生产）

用 systemd 管理进程，实现开机自启、崩溃自动拉起、日志收集。

### 4.1 创建服务文件

```bash
sudo vim /etc/systemd/system/glass-agent.service
```

内容（注意替换路径和用户）：

```ini
[Unit]
Description=Glass Inspection Agent (LangGraph)
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/glass-inspection-agent
EnvironmentFile=/opt/glass-inspection-agent/.env
ExecStart=/opt/glass-inspection-agent/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=5
# 环境变量也可以直接写在这里（可选）
# Environment=LLM_BASE_URL=...
# Environment=LLM_API_KEY=...

[Install]
WantedBy=multi-user.target
```

> **注意**：LangGraph 的 `MemorySaver` 是进程内状态，`--workers 1` 必须保持单进程；多进程会各自维护独立会话，导致多轮记忆错乱（详见 §8 生产化改造）。

### 4.2 启用

```bash
sudo systemctl daemon-reload
sudo systemctl enable glass-agent
sudo systemctl start glass-agent
sudo systemctl status glass-agent
```

### 4.3 常用命令

```bash
sudo systemctl restart glass-agent   # 重启
sudo systemctl stop glass-agent      # 停止
sudo journalctl -u glass-agent -f    # 实时看日志
sudo journalctl -u glass-agent -n 100 # 看最近 100 行
```

---

## 5. Nginx 反向代理

### 5.1 创建站点配置

```bash
sudo vim /etc/nginx/sites-available/glass-agent
```

内容：

```nginx
server {
    listen 80;
    server_name glass-agent.example.com;   # 换成你的域名/IP

    # 若配置了 HTTPS 证书，另加 443 配置（见下方提示）

    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 流式接口需要的关键配置（关闭缓冲，保持长连接）
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        proxy_set_header Connection "";
    }
}
```

> **SSE 要点**：`/api/chat/stream` 是流式接口，必须 `proxy_buffering off`，否则前端收不到逐帧推送。

### 5.2 启用

```bash
sudo ln -s /etc/nginx/sites-available/glass-agent /etc/nginx/sites-enabled/
sudo nginx -t          # 校验配置
sudo systemctl reload nginx
```

### 5.3 配置 HTTPS（可选但推荐）

用 Certbot 一键签发免费证书：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d glass-agent.example.com
```

---

## 6. Docker 部署

> 若你偏好容器化，可参考以下 Dockerfile（项目当前未内置，需自行创建）。

### 6.1 `Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "uvicorn[standard]" gunicorn

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 6.2 构建与运行

```bash
docker build -t glass-agent:latest .
docker run -d --name glass-agent \
  --env-file .env \
  -p 8000:8000 \
  glass-agent:latest
```

> **数据持久化**：`data/`（含 inspection.db、vector_store.json）需挂载卷：
> ```bash
> docker run -d --name glass-agent \
>   --env-file .env \
>   -v $(pwd)/data:/app/data \
>   -p 8000:8000 \
>   glass-agent:latest
> ```

---

## 7. 接入真实数据库（MySQL）

MySQL 支持已内置在代码中，**无需改代码**，只需在 `.env` 配置连接参数。

### 7.1 配置步骤

1. 确认已安装驱动：`pip install pymysql`（已列入 `requirements.txt`）。
2. `.env` 设置：
   ```ini
   DB_URL=mysql://
   MYSQL_HOST=<服务器地址>
   MYSQL_PORT=3306
   MYSQL_USER=<账号>
   MYSQL_PASSWORD=<密码>
   MYSQL_DATABASE=glass_inspection
   MYSQL_CHARSET=utf8mb4
   ```
3. 创建数据库（幂等，可重复执行）：
   ```bash
   python -c "from app.db.session import init_mysql_database; init_mysql_database()"
   ```
4. 建表 + 灌数据：
   ```bash
   python scripts/seed_db.py   # 演示数据；生产则跳过，接入真实上云表
   ```

### 7.2 已处理的双后端差异

| 差异点 | SQLite | MySQL | 处理方式 |
|---|---|---|---|
| 占位符 | `?` | `%s` | `adapt_sql()` 自动替换 |
| 自增主键 | `AUTOINCREMENT` | `AUTO_INCREMENT` | 独立 `schema_mysql.sql` |
| 建表外键 | `PRAGMA` | `CONSTRAINT` | 独立 `schema_mysql.sql` |
| 除零保护 | `MAX(SUM(x),1)` | `NULLIF(x,0)` | 统一 `NULLIF`（两边通用） |
| 数值类型 | int/float | `Decimal` | `_rows()` 归一为 float |

### 7.3 字段对齐提示

把 YOLO 上云写入的表按 `schema_mysql.sql` 字段映射：

`inspection_records` 需对齐的字段：

| 字段 | 类型 | 来源 |
|---|---|---|
| `factory_id` | int | 上云的厂家 ID |
| `glass_type` | text | 建筑/家电/电子 |
| `inspected_at` | datetime | 检测时间 |
| `line_no` | text | 产线/机台号 |
| `total_count` | int | 检测总数 |
| `defect_count` | int | 不良总数 |

`defects` 表：

| 字段 | 类型 | 来源 |
|---|---|---|
| `record_id` | int | 关联检测记录 |
| `defect_type` | text | 气泡/崩边/…（对齐 `domain.py` 的 `DEFECT_TYPES`） |
| `count` | int | 数量 |
| `severity` | text | 轻微/一般/严重 |
| `confidence` | real | YOLO 置信度 |

> 术语务必对齐 `app/domain.py`，否则智能体查不到数据或口径错乱。

---

## 8. 生产化改造清单（必读）

> **在正式对外服务前，必须完成以下改造**，否则会遇到「多轮记忆丢失」「挂起」「裸奔」等问题。

### 8.1 会话持久化（最高优先级）

当前 `builder.py` 用 `MemorySaver`（进程内存），服务重启丢历史、多进程不共享。

**方案 A：SqliteSaver（零额外依赖，单机推荐）**

```python
# app/graph/builder.py
from langgraph.checkpoint.sqlite import SqliteSaver
import sqlite3

conn = sqlite3.connect("data/checkpoints.db", check_same_thread=False)
checkpointer = SqliteSaver(conn)
return g.compile(checkpointer=checkpointer)
```

**方案 B：PostgresSaver（多副本推荐）**

```python
from langgraph.checkpoint.postgres import PostgresSaver
```

> 无论 A/B，`data/checkpoints.db` 或 PG 都要持久化挂载。

### 8.2 LLM 超时与重试

已内置实现：`app/llm.py` 的 `get_llm()` / `get_vision_llm()` 均读取配置
`LLM_TIMEOUT`（默认 60s）与 `LLM_MAX_RETRIES`（默认 2 次），无需改代码。

只需在 `.env` 中按网关实际情况调整：

```ini
LLM_TIMEOUT=60        # 单次调用超时（秒）
LLM_MAX_RETRIES=2     # 失败自动重试次数
```

> 若你的 LLM 网关延迟较高，可适当调大 `LLM_TIMEOUT`；重试次数过大反而会放大故障，建议 ≤ 3。

### 8.3 节点级异常兜底

`qa_generate` / `data_analyze` 等 LLM 调用处加 try/except，失败返回友好提示而非 500。

### 8.4 鉴权与限流

接口目前无鉴权。生产必须在 Nginx 或网关层加：
- API Key / JWT 认证
- 按 `thread_id` 或 IP 限流
- 防止公网裸奔

### 8.5 结构化日志

已内置实现：`app/logging.py` 提供 JSON 结构化日志（含 `thread_id`、`intent`、耗时、关键参数），
各节点已调用 `log(...)` 记录路由/检索/SQL/诊断等关键步骤。

- 日志级别通过 `.env` 的 `LOG_LEVEL` 控制（默认 `INFO`，排查时可用 `DEBUG`）。
- `main.py` 在每个请求入口调用 `set_thread_id()` 注入会话标识，日志可直接按 `thread_id` 过滤。

如需自定义，直接在 `app/logging.py` 扩展 `log(level, event, **fields)` 的字段即可。

### 8.6 进程模型注意

- **MemorySaver 下必须单 worker**（`--workers 1`），否则多轮记忆错乱。
- 换 SqliteSaver/PostgresSaver 后可安全多 worker。

---

## 9. 监控与日志

### 9.1 健康检查

```bash
curl http://127.0.0.1:8000/health
```

可用 systemd 的 `WatchdogSec` 或外部监控（如 UptimeRobot、Prometheus）定时探活。

### 9.2 日志位置

| 方式 | 日志位置 |
|---|---|
| systemd | `journalctl -u glass-agent` |
| nohup | `nohup.out` |
| Docker | `docker logs glass-agent` |
| Nginx | `/var/log/nginx/access.log` / `error.log` |

### 9.3 关键指标监控

- 接口 P95 延迟（LLM 调用是主要瓶颈）
- LLM 网关超时/失败率
- 数据库连接数
- 磁盘占用（vector_store.json、checkpoints.db、数据库）

---

## 10. 常见问题排查

### Q1：多轮对话「失忆」，问完 A 再问 B 不记得上下文

**原因**：`MemorySaver` + 服务重启，或用了多 worker。
**解决**：换 `SqliteSaver`（§8.1）；确保单 worker。

### Q2：`/api/chat/stream` 前端收不到流式输出

**原因**：Nginx 缓冲了 SSE。
**解决**：Nginx 配置加 `proxy_buffering off`（§5.1）。

### Q3：请求长时间无响应，最终超时

**原因**：LLM 网关抖动，默认 60s 超时后触发重试仍失败。
**解决**：通过 `.env` 调整 `LLM_TIMEOUT` / `LLM_MAX_RETRIES`（见 §8.2）。

### Q4：`/api/chat` 返回 500

**原因**：LLM 网关不可用、或数据库未初始化、或知识库索引缺失。
**排查**：
```bash
# 看服务端日志
sudo journalctl -u glass-agent -f

# 检查数据库（MySQL）
mysql -h<host> -u<user> -p<password> -e "USE glass_inspection; SHOW TABLES;"
# 或检查 SQLite
ls -la data/inspection.db

# 检查向量索引
ls -la data/vector_store.json
```

### Q5：改了 `.env` 不生效

**原因**：`load_dotenv()` 在进程启动时执行一次。
**解决**：改 `.env` 后重启服务 `sudo systemctl restart glass-agent`。

### Q6：查询结果为空

**原因**：术语未对齐 `domain.py`，或数据库未映射真实数据，或 `.env` 数据库配置未生效。
**解决**：核对 `defect_type`/`glass_type` 与 `domain.py` 一致；确认 `inspection_records`/`defects` 已写入；确认 `DB_URL=mysql://` 及 `MYSQL_*` 参数正确。

### Q7：知识库检索不准

**原因**：文档未 ingest，或切块过大。
**解决**：重新运行 `python -m app.rag.ingest`；必要时调整 `CHUNK_SIZE`（`app/rag/ingest.py`）。

---

## 附：快速部署 checklist

- [ ] 服务器装好 Python 3.9+ / 3.11
- [ ] 克隆代码、建 venv、装依赖（含 pymysql）
- [ ] 配好 `.env`（LLM 地址/密钥/模型 + MySQL 连接）
- [ ] 创建数据库 `init_mysql_database()`（MySQL 首次）
- [ ] 初始化数据（演示 `seed_db.py` / 生产接真实上云表）
- [ ] 构建向量索引 `python -m app.rag.ingest`
- [ ] 换成 `SqliteSaver`（生产）
- [ ] 配置 `LLM_TIMEOUT` / `LLM_MAX_RETRIES`（默认 60s/2 次，按需调整）
- [ ] systemd 托管 + 开机自启
- [ ] Nginx 反向代理（SSE 关缓冲）
- [ ] 配置鉴权与限流
- [ ] 配置 HTTPS
- [ ] 监控探活 + 日志收集
- [ ] 冒烟验证：`curl /health` + 发几条测试消息
