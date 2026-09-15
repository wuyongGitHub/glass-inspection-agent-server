"""全局配置：从 .env 读取，路径默认锚定项目根目录。"""
import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Settings:
    # 模型（OpenAI 兼容接口，支持内网私有化部署）
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "EMPTY")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    # 视觉（多模态图片输入）模型；为空时回退用 llm_model
    llm_vision_model: str = os.getenv("LLM_VISION_MODEL", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-ada-002")

    # 数据与知识库
    db_url: str = os.getenv("DB_URL", f"sqlite:///{os.path.join(BASE_DIR, 'data', 'inspection.db')}")
    # MySQL 连接参数（DB_URL 以 mysql:// 开头时使用）
    mysql_host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    mysql_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_user: str = os.getenv("MYSQL_USER", "root")
    mysql_password: str = os.getenv("MYSQL_PASSWORD", "")
    mysql_database: str = os.getenv("MYSQL_DATABASE", "glass_inspection")
    mysql_charset: str = os.getenv("MYSQL_CHARSET", "utf8mb4")
    kb_dir: str = os.getenv("KB_DIR", os.path.join(BASE_DIR, "data", "docs"))
    vector_store_path: str = os.getenv(
        "VECTOR_STORE_PATH", os.path.join(BASE_DIR, "data", "vector_store.json")
    )

    # 检索（默认 12：容纳复合问题所需的多个来源块，多出的片段由 LLM 自行取舍）
    retrieve_top_k: int = int(os.getenv("RETRIEVE_TOP_K", "12"))
    # Rerank 召回候选数（先粗召回候选，再由 reranker 精排）
    rerank_candidates: int = int(os.getenv("RERANK_CANDIDATES", "30"))
    rerank_top_k: int = int(os.getenv("RERANK_TOP_K", "6"))
    # 查询改写：false 关闭（默认开启，含同义词扩展；LLM 改写需另行配置）
    query_rewrite: bool = os.getenv("QUERY_REWRITE", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )

    # LLM 调用健壮性（P0）：超时与重试
    llm_timeout: int = int(os.getenv("LLM_TIMEOUT", "60"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))

    # 诊断流开关（P1）：开启后 router 可路由到 diagnosis
    diagnosis_enabled: bool = os.getenv("DIAGNOSIS_ENABLED", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )

    # 结构化日志级别
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # 知识库未收录主题时的回答策略：
    # true（默认）= 回退用 LLM 通用知识回答，但标注“非部门标准口径”；
    # false = 严格拒答，仅说明知识库未收录并建议补充文档
    qa_fallback: bool = os.getenv("QA_FALLBACK", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )

    # 敏感词 / 违禁词过滤（内容安全）
    sensitive_words_dir: str = os.getenv(
        "SENSITIVE_WORDS_DIR",
        os.path.join(BASE_DIR, "data", "sensitive_words"),
    )
    sensitive_filter_enabled: bool = os.getenv(
        "SENSITIVE_FILTER_ENABLED", "true"
    ).strip().lower() in ("1", "true", "yes", "on")


settings = Settings()
