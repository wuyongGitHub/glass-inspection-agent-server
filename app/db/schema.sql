-- 玻璃检测上云数据表（演示结构）
-- 生产环境：把 YOLO 检测结果上云写入的表按此字段映射即可
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS factories (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,          -- 厂家名称
    glass_type TEXT NOT NULL           -- 主营玻璃类型：建筑/家电/电子
);

CREATE TABLE IF NOT EXISTS inspection_records (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    factory_id   INTEGER NOT NULL REFERENCES factories(id),
    glass_type   TEXT NOT NULL,
    inspected_at TEXT NOT NULL,        -- 检测时间 YYYY-MM-DD HH:MM:SS
    line_no      TEXT,                 -- 产线/机台号
    shift        TEXT,                 -- 班次：白班/夜班
    total_count  INTEGER NOT NULL,     -- 检测总数
    defect_count INTEGER NOT NULL DEFAULT 0  -- 不良总数
);
CREATE INDEX IF NOT EXISTS idx_records_date ON inspection_records(inspected_at);
CREATE INDEX IF NOT EXISTS idx_records_factory ON inspection_records(factory_id);

CREATE TABLE IF NOT EXISTS defects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id   INTEGER NOT NULL REFERENCES inspection_records(id),
    defect_type TEXT NOT NULL,         -- 气泡/崩边/划伤/结石...
    count       INTEGER NOT NULL DEFAULT 1,
    severity    TEXT,                  -- 轻微/一般/严重
    confidence  REAL                   -- YOLO 置信度
);
CREATE INDEX IF NOT EXISTS idx_defects_record ON defects(record_id);
CREATE INDEX IF NOT EXISTS idx_defects_type ON defects(defect_type);

-- ===== V2 扩展：班次 / 产线 / 设备 / 设备参数（设备与工艺数据关联）=====
CREATE TABLE IF NOT EXISTS shifts (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE           -- 白班/夜班
);

CREATE TABLE IF NOT EXISTS lines (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,           -- L1/L2...
    factory_id INTEGER REFERENCES factories(id)
);
CREATE INDEX IF NOT EXISTS idx_lines_factory ON lines(factory_id);

CREATE TABLE IF NOT EXISTS equipment (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,       -- 设备名/编号
    equipment_type TEXT,                -- 相机/光源/钢化炉/检测机
    line_id        INTEGER REFERENCES lines(id)
);
CREATE INDEX IF NOT EXISTS idx_equipment_line ON equipment(line_id);

CREATE TABLE IF NOT EXISTS equipment_params (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id),
    param_name   TEXT NOT NULL,         -- 曝光/光源亮度/温度/压力...
    param_value  REAL,
    recorded_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_eq_params_eq ON equipment_params(equipment_id);
CREATE INDEX IF NOT EXISTS idx_eq_params_ts ON equipment_params(recorded_at);

-- ===== V2 扩展：历史案例库（Case RAG）=====
CREATE TABLE IF NOT EXISTS cases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    defect_type TEXT NOT NULL,          -- 缺陷类型（canonical）
    glass_type  TEXT,                   -- 玻璃类型
    factory     TEXT,                   -- 厂家
    line_no     TEXT,                   -- 产线
    shift       TEXT,                   -- 班次
    symptoms    TEXT NOT NULL,          -- 症状/异常描述
    metrics     TEXT,                   -- JSON：关键指标快照
    root_cause  TEXT,                   -- 根因（人工确认后沉淀）
    confidence  REAL DEFAULT 0.5,
    status      TEXT DEFAULT 'confirmed', -- confirmed / pending
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_cases_defect ON cases(defect_type);
CREATE INDEX IF NOT EXISTS idx_cases_factory ON cases(factory);

CREATE TABLE IF NOT EXISTS case_evidence (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    kind    TEXT,                       -- fact/inference/hypothesis/recommendation
    claim   TEXT NOT NULL,
    source  TEXT
);
CREATE INDEX IF NOT EXISTS idx_case_evidence_case ON case_evidence(case_id);

CREATE TABLE IF NOT EXISTS case_actions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    action  TEXT NOT NULL,
    result  TEXT
);
CREATE INDEX IF NOT EXISTS idx_case_actions_case ON case_actions(case_id);

-- ===== V2 扩展：长期业务记忆 =====
CREATE TABLE IF NOT EXISTS business_memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_type TEXT NOT NULL,          -- factory_risk/line_risk/confirmed_case/user_preference
    subject     TEXT NOT NULL,          -- 主体：厂家名/产线名/用户
    payload     TEXT,                   -- JSON
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_type ON business_memory(memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_subject ON business_memory(subject);
