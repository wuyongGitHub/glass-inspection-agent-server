"""数据库连接：支持 SQLite（演示）与 MySQL（生产）。

通过 DB_URL 前缀选择后端：
- sqlite:///data/inspection.db  → SQLite
- mysql://                      → MySQL（连接参数取 MYSQL_* 环境变量）
"""
import os

from app.config import settings

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
SCHEMA_MYSQL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema_mysql.sql")


def _is_mysql(url: str) -> bool:
    return url.startswith("mysql://")


def is_mysql_backend() -> bool:
    """当前是否为 MySQL 后端（供调用方决定占位符风格等）。"""
    return _is_mysql(settings.db_url)


def adapt_sql(sql: str) -> str:
    """把 SQLite 的 ? 占位符适配为当前后端的占位符。

    SQLite 用 ?，MySQL(pymysql) 用 %s。本项目 SQL 均为固定模板，
    不含字符串字面量中的 ?，可安全替换。
    """
    if is_mysql_backend():
        return sql.replace("?", "%s")
    return sql


def get_conn():
    """返回数据库连接（SQLite 为 sqlite3.Connection，MySQL 为 pymysql.Connection）。

    两种连接的共同约定：cursor 支持 ? 占位符参数化、支持 context manager、
    有 .execute()/.commit()/.close()/.row_factory（后者仅 SQLite 使用）。
    """
    url = settings.db_url

    if url.startswith("sqlite:///"):
        import sqlite3

        path = url[len("sqlite:///"):]
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    if _is_mysql(url):
        import pymysql

        return pymysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            database=settings.mysql_database,
            charset=settings.mysql_charset,
            cursorclass=pymysql.cursors.DictCursor,
        )

    raise ValueError(f"暂不支持的 DB_URL：{url}（支持 sqlite:/// 与 mysql://）")


def init_db():
    """按 schema.sql 建表（幂等），返回连接。

    注意：MySQL 需先用 init_mysql_database() 创建数据库本身，
    建表脚本是纯 SQL 的 schema.sql（不含 PRAGMA）。
    """
    conn = get_conn()
    schema_path = SCHEMA_MYSQL_PATH if _is_mysql(settings.db_url) else SCHEMA_PATH
    with open(schema_path, encoding="utf-8") as f:
        script = f.read()
    if _is_mysql(settings.db_url):
        _exec_mysql_script(conn, script)
    else:
        conn.executescript(script)
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn) -> None:
    """轻量迁移：为已存在的旧库补充新增字段（幂等）。

    CREATE TABLE IF NOT EXISTS 不会修改已存在的表，故新增列需单独 ALTER。
    """
    if is_mysql_backend():
        cur = conn.cursor()
        cur.execute("SHOW COLUMNS FROM inspection_records LIKE 'shift'")
        if cur.fetchone() is None:
            cur.execute("ALTER TABLE inspection_records ADD COLUMN shift VARCHAR(16)")
    else:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(inspection_records)")
        cols = [row[1] for row in cur.fetchall()]
        if "shift" not in cols:
            cur.execute("ALTER TABLE inspection_records ADD COLUMN shift TEXT")
    conn.commit()


def _exec_mysql_script(conn, script: str) -> None:
    """MySQL 不兼容 executescript，需按分号拆分逐条执行（跳过注释与空语句）。"""
    cur = conn.cursor()
    for stmt in _split_sql(script):
        cur.execute(stmt)
    cur.close()


def _split_sql(script: str):
    """把 SQL 脚本按分号拆成单条语句，跳过注释行与空行。"""
    stmts = []
    buf = []
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buf).rstrip(";").strip()
            if stmt:
                stmts.append(stmt)
            buf = []
    if buf:
        stmt = "\n".join(buf).rstrip(";").strip()
        if stmt:
            stmts.append(stmt)
    return stmts


def init_mysql_database():
    """创建 MySQL 数据库本身（若不存在），供首次初始化时调用。"""
    if not _is_mysql(settings.db_url):
        raise RuntimeError("当前 DB_URL 不是 mysql://，无需创建 MySQL 数据库")
    import pymysql

    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        charset=settings.mysql_charset,
    )
    try:
        cur = conn.cursor()
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{settings.mysql_database}` "
            f"CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
        )
        conn.commit()
    finally:
        conn.close()
