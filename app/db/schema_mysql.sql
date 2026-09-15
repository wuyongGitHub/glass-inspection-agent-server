-- 玻璃检测上云数据表（MySQL 8.0 版本）
-- 生产环境：把 YOLO 检测结果上云写入的表按此字段映射即可
-- 术语对齐 app/domain.py 的 DEFECT_TYPES / GLASS_TYPES

CREATE TABLE IF NOT EXISTS factories (
    id         INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name       VARCHAR(255) NOT NULL,          -- 厂家名称
    glass_type VARCHAR(64) NOT NULL,           -- 主营玻璃类型：建筑/家电/电子
    UNIQUE KEY uk_factories_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS inspection_records (
    id           INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    factory_id   INT NOT NULL,
    glass_type   VARCHAR(64) NOT NULL,
    inspected_at DATETIME NOT NULL,            -- 检测时间 YYYY-MM-DD HH:MM:SS
    line_no      VARCHAR(64),                  -- 产线/机台号
    shift        VARCHAR(16),                  -- 班次：白班/夜班
    total_count  INT NOT NULL,                 -- 检测总数
    defect_count INT NOT NULL DEFAULT 0,       -- 不良总数
    KEY idx_records_date (inspected_at),
    KEY idx_records_factory (factory_id),
    CONSTRAINT fk_records_factory FOREIGN KEY (factory_id) REFERENCES factories(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS defects (
    id          INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    record_id   INT NOT NULL,
    defect_type VARCHAR(64) NOT NULL,          -- 气泡/崩边/划伤/结石...
    count       INT NOT NULL DEFAULT 1,
    severity    VARCHAR(16),                   -- 轻微/一般/严重
    confidence  DOUBLE,                        -- YOLO 置信度
    KEY idx_defects_record (record_id),
    KEY idx_defects_type (defect_type),
    CONSTRAINT fk_defects_record FOREIGN KEY (record_id) REFERENCES inspection_records(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ===== V2 扩展：班次 / 产线 / 设备 / 设备参数（设备与工艺数据关联）=====
CREATE TABLE IF NOT EXISTS shifts (
    id   INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(64) NOT NULL,
    UNIQUE KEY uk_shifts_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS lines (
    id         INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name       VARCHAR(64) NOT NULL,
    factory_id INT,
    KEY idx_lines_factory (factory_id),
    CONSTRAINT fk_lines_factory FOREIGN KEY (factory_id) REFERENCES factories(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS equipment (
    id             INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name           VARCHAR(128) NOT NULL,
    equipment_type VARCHAR(64),
    line_id        INT,
    KEY idx_equipment_line (line_id),
    CONSTRAINT fk_equipment_line FOREIGN KEY (line_id) REFERENCES lines(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS equipment_params (
    id           INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    equipment_id INT NOT NULL,
    param_name   VARCHAR(64) NOT NULL,
    param_value  DOUBLE,
    recorded_at  DATETIME,
    KEY idx_eq_params_eq (equipment_id),
    KEY idx_eq_params_ts (recorded_at),
    CONSTRAINT fk_eq_params_eq FOREIGN KEY (equipment_id) REFERENCES equipment(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ===== V2 扩展：历史案例库（Case RAG）=====
CREATE TABLE IF NOT EXISTS cases (
    id          INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    defect_type VARCHAR(64) NOT NULL,
    glass_type  VARCHAR(64),
    factory     VARCHAR(255),
    line_no     VARCHAR(64),
    shift       VARCHAR(16),
    symptoms    TEXT NOT NULL,
    metrics     TEXT,
    root_cause  TEXT,
    confidence  DOUBLE DEFAULT 0.5,
    status      VARCHAR(16) DEFAULT 'confirmed',
    created_at  DATETIME,
    KEY idx_cases_defect (defect_type),
    KEY idx_cases_factory (factory)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS case_evidence (
    id      INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    case_id INT NOT NULL,
    kind    VARCHAR(32),
    claim   TEXT NOT NULL,
    source  VARCHAR(255),
    KEY idx_case_evidence_case (case_id),
    CONSTRAINT fk_case_evidence_case FOREIGN KEY (case_id) REFERENCES cases(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS case_actions (
    id      INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    case_id INT NOT NULL,
    action  TEXT NOT NULL,
    result  TEXT,
    KEY idx_case_actions_case (case_id),
    CONSTRAINT fk_case_actions_case FOREIGN KEY (case_id) REFERENCES cases(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ===== V2 扩展：长期业务记忆 =====
CREATE TABLE IF NOT EXISTS business_memory (
    id          INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    memory_type VARCHAR(64) NOT NULL,
    subject     VARCHAR(255) NOT NULL,
    payload     TEXT,
    updated_at  DATETIME,
    KEY idx_memory_type (memory_type),
    KEY idx_memory_subject (subject)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
