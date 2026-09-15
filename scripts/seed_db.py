"""生成演示检测数据：5 家厂家，含"晶捷电子玻璃近 30 天划伤上升"的可发现趋势。

支持命令行参数控制数据规模，便于压力测试 / 大量数据回归：

用法（在项目根目录运行）：
  python scripts/seed_db.py                     # 默认 90 天、2 产线
  python scripts/seed_db.py --days 365          # 一年数据
  python scripts/seed_db.py --days 180 --lines 4
  python scripts/seed_db.py --days 90 --min-total 5000 --max-total 20000  # 大检测量
  python scripts/seed_db.py --days 30 --seed 7  # 固定随机种子复现

规模估算（每厂每天每产线 1 条检测记录）：
  records = days × 厂数(5) × 产线数
  defects  ≈ records × (平均缺陷数/条)，约 records × 6
"""
import argparse
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import adapt_sql, init_db, is_mysql_backend  # noqa: E402
from app.domain import DEFECT_TYPES  # noqa: E402

FACTORIES = [
    ("华南玻璃", "建筑玻璃"),
    ("耀华建筑玻璃", "建筑玻璃"),
    ("信义家电面板", "家电玻璃"),
    ("康宁家电配件", "家电玻璃"),
    ("晶捷电子玻璃", "电子玻璃"),
]

# 各缺陷出现权重（演示分布，可按真实数据调整）
DEFECT_WEIGHTS = [12, 10, 6, 5, 15, 8, 6, 6, 6, 6, 10]

# 晶捷电子玻璃近 N 天划伤上升（异常趋势，供主动洞察识别）
ANOMALY_FACTORY = "晶捷电子玻璃"
ANOMALY_LAST_DAYS = 30
ANOMALY_RATE_BOOST = 0.028          # 检出率抬升幅度
ANOMALY_DEFECT_BOOST = ("划伤", 40)  # 异常期间提高划伤权重

# 班次（demo 两级）
SHIFTS = ["白班", "夜班"]

# 历史案例（Case RAG 种子，根因为人工确认后的结论，供案例检索与沉淀演示）
CASES = [
    {
        "defect_type": "划伤", "glass_type": "电子玻璃", "factory": "晶捷电子玻璃",
        "line_no": "L2", "shift": "白班",
        "symptoms": "近 30 天划伤检出率上升，主要集中在 L2 产线白班时段",
        "metrics": '{"trend": "up", "rate_delta": 0.028, "top_line": "L2"}',
        "root_cause": "L2 产线输送辊表面磨损，划伤玻璃下表面",
        "confidence": 0.85,
        "evidence": [
            ("fact", "L2 产线近 30 天划伤检出率较基线上升 2.8 个百分点", "数据查询"),
            ("inference", "异常集中在白班与 L2 产线，指向设备因素而非原料", "维度下钻"),
        ],
        "actions": [("更换 L2 产线输送辊并做表面抛光", "划伤率一周内回落至基线水平")],
    },
    {
        "defect_type": "气泡", "glass_type": "建筑玻璃", "factory": "华南玻璃",
        "line_no": "L1", "shift": "夜班",
        "symptoms": "夜班气泡不良率突增，伴随窑炉温度波动",
        "metrics": '{"trend": "spike", "top_line": "L1"}',
        "root_cause": "窑炉澄清区温度偏低，气泡未能充分逸出",
        "confidence": 0.78,
        "evidence": [
            ("fact", "夜班气泡不良率较白班高 3.1 倍", "班次对比"),
            ("fact", "窑炉温度曲线在异常时段下降 8℃", "设备参数"),
        ],
        "actions": [("上调澄清区温度至工艺标准", "气泡不良率恢复正常")],
    },
    {
        "defect_type": "崩边", "glass_type": "家电玻璃", "factory": "信义家电面板",
        "line_no": "L1", "shift": "白班",
        "symptoms": "切割工序后崩边比例升高，边缘有锯齿状缺口",
        "metrics": '{"top_defect": "崩边"}',
        "root_cause": "切割刀轮钝化，切割应力过大",
        "confidence": 0.9,
        "evidence": [("fact", "崩边缺陷集中在切割后道工序", "工序分析")],
        "actions": [("更换切割刀轮并按周期保养", "崩边率下降 60%")],
    },
    {
        "defect_type": "结石", "glass_type": "建筑玻璃", "factory": "耀华建筑玻璃",
        "line_no": "L2", "shift": "夜班",
        "symptoms": "成品内偶见结石，位置随机",
        "metrics": '{"top_defect": "结石"}',
        "root_cause": "原料中混入耐火材料颗粒",
        "confidence": 0.7,
        "evidence": [("fact", "结石成分与窑炉耐火砖匹配", "理化分析")],
        "actions": [("加强原料筛分并检查窑炉内衬", "结石检出下降")],
    },
]


def parse_args():
    p = argparse.ArgumentParser(description="生成玻璃检测演示数据")
    p.add_argument("--days", type=int, default=90, help="生成多少天（默认 90）")
    p.add_argument("--lines", type=int, default=2, help="每家产线数（默认 2，L1..Ln）")
    p.add_argument("--min-total", type=int, default=400, help="每批检测总数下限（默认 400）")
    p.add_argument("--max-total", type=int, default=1200, help="每批检测总数上限（默认 1200）")
    p.add_argument("--base-rate-min", type=float, default=0.015, help="基准检出率下限（默认 0.015）")
    p.add_argument("--base-rate-max", type=float, default=0.035, help="基准检出率上限（默认 0.035）")
    p.add_argument("--seed", type=int, default=42, help="随机种子（默认 42）")
    p.add_argument("--no-anomaly", action="store_true", help="不埋异常趋势（纯随机数据）")
    return p.parse_args()


def _line_names(n: int):
    return [f"L{i + 1}" for i in range(n)]


def _seed_v2_tables(cur, run, run_many, lines):
    """生成 V2 扩展数据：班次 / 产线 / 设备 / 设备参数 / 历史案例库。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 班次
    run_many("INSERT INTO shifts (name) VALUES (?)", [(s,) for s in SHIFTS])

    # 产线（"厂家-产线" 命名，与 inspection_records.line_no 文本对应）
    line_rows = [
        (f"{name}-{line}", fid)
        for fid, (name, _) in enumerate(FACTORIES, 1)
        for line in lines
    ]
    run_many("INSERT INTO lines (name, factory_id) VALUES (?,?)", line_rows)

    # 设备（每条产线：检测机 / 相机 / 光源）
    equip_rows = []
    for lid, (lname, _fid) in enumerate(line_rows, 1):
        equip_rows.append((f"{lname}-检测机", "检测机", lid))
        equip_rows.append((f"{lname}-相机", "相机", lid))
        equip_rows.append((f"{lname}-光源", "光源", lid))
    run_many(
        "INSERT INTO equipment (name, equipment_type, line_id) VALUES (?,?,?)",
        equip_rows,
    )

    # 设备参数（检测机近 7 天温度/光源亮度，供设备参数关联分析）
    eq_param_rows = []
    for eid, (_ename, etype, _lid) in enumerate(equip_rows, 1):
        if etype != "检测机":
            continue
        for d in range(7):
            ts = (datetime.now() - timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")
            eq_param_rows.append((eid, "温度", round(random.uniform(30.0, 60.0), 1), ts))
            eq_param_rows.append((eid, "光源亮度", round(random.uniform(70.0, 100.0), 1), ts))
    run_many(
        "INSERT INTO equipment_params (equipment_id, param_name, param_value, recorded_at) "
        "VALUES (?,?,?,?)",
        eq_param_rows,
    )

    # 历史案例 + 证据 + 措施
    for c in CASES:
        run(
            "INSERT INTO cases (defect_type, glass_type, factory, line_no, shift, "
            "symptoms, metrics, root_cause, confidence, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (c["defect_type"], c["glass_type"], c["factory"], c["line_no"], c["shift"],
             c["symptoms"], c["metrics"], c["root_cause"], c["confidence"], "confirmed", now),
        )
        case_id = cur.lastrowid
        run_many(
            "INSERT INTO case_evidence (case_id, kind, claim, source) VALUES (?,?,?,?)",
            [(case_id, k, claim, src) for (k, claim, src) in c["evidence"]],
        )
        run_many(
            "INSERT INTO case_actions (case_id, action, result) VALUES (?,?,?)",
            [(case_id, a, r) for (a, r) in c["actions"]],
        )


def main():
    args = parse_args()
    random.seed(args.seed)

    conn = init_db()
    cur = conn.cursor()

    def run(sql: str, params=()):
        cur.execute(adapt_sql(sql), params)

    def run_many(sql: str, rows):
        if rows:
            cur.executemany(adapt_sql(sql), rows)

    # 清空旧数据（子表 -> 父表，满足外键依赖）
    run("DELETE FROM case_actions")
    run("DELETE FROM case_evidence")
    run("DELETE FROM cases")
    run("DELETE FROM equipment_params")
    run("DELETE FROM equipment")
    run("DELETE FROM lines")
    run("DELETE FROM shifts")
    run("DELETE FROM business_memory")
    run("DELETE FROM defects")
    run("DELETE FROM inspection_records")
    run("DELETE FROM factories")

    # 厂家
    run_many(
        "INSERT INTO factories (id, name, glass_type) VALUES (?,?,?)",
        [(i, name, gt) for i, (name, gt) in enumerate(FACTORIES, 1)],
    )

    lines = _line_names(args.lines)
    start = datetime.now() - timedelta(days=args.days)

    rec_rows = []          # 批量累积 inspection_records
    rec_meta = []          # 每条记录 (fid, name, day_index) 用于后续生成 defects
    defects_rows = []      # 批量累积 defects

    for day in range(args.days):
        ts = (start + timedelta(days=day)).strftime("%Y-%m-%d %H:%M:%S")
        shift = SHIFTS[day % 2]  # 按天交替白班/夜班，便于班次维度分析
        for fid, (name, gt) in enumerate(FACTORIES, 1):
            for line in lines:
                total = random.randint(args.min_total, args.max_total)
                base_rate = random.uniform(args.base_rate_min, args.base_rate_max)
                # 异常：晶捷电子玻璃近 30 天检出率抬升
                if not args.no_anomaly and name == ANOMALY_FACTORY and day >= args.days - ANOMALY_LAST_DAYS:
                    base_rate += ANOMALY_RATE_BOOST
                defect_count = int(total * base_rate)
                rec_rows.append((fid, gt, ts, line, shift, total, defect_count))
                rec_meta.append((fid, name, day))

    run_many(
        "INSERT INTO inspection_records "
        "(factory_id, glass_type, inspected_at, line_no, shift, total_count, defect_count) "
        "VALUES (?,?,?,?,?,?,?)",
        rec_rows,
    )

    # 取 inspection_records 的起始 id（MySQL/SQLite 自增，起始为 1；但为稳妥，先查）
    run("SELECT MIN(id) AS mn FROM inspection_records")
    row = cur.fetchone()
    first_rid = row["mn"] if isinstance(row, dict) else row[0]
    first_rid = first_rid or 1

    # 为每条记录生成缺陷明细
    for idx, (fid, name, day) in enumerate(rec_meta):
        rid = first_rid + idx
        total = rec_rows[idx][5]
        defect_count = rec_rows[idx][6]
        weights = list(DEFECT_WEIGHTS)
        if not args.no_anomaly and name == ANOMALY_FACTORY and day >= args.days - ANOMALY_LAST_DAYS:
            weights[DEFECT_TYPES.index(ANOMALY_DEFECT_BOOST[0])] = ANOMALY_DEFECT_BOOST[1]
        remaining = defect_count
        while remaining > 0:
            dtype = random.choices(DEFECT_TYPES, weights=weights)[0]
            n = min(remaining, random.randint(1, max(1, defect_count // 3)))
            defects_rows.append((
                rid, dtype, n,
                random.choice(["轻微", "一般", "严重"]),
                round(random.uniform(0.55, 0.99), 2),
            ))
            remaining -= n

    run_many(
        "INSERT INTO defects (record_id, defect_type, count, severity, confidence) "
        "VALUES (?,?,?,?,?)",
        defects_rows,
    )

    _seed_v2_tables(cur, run, run_many, lines)

    conn.commit()
    run("SELECT COUNT(*) AS n FROM inspection_records")
    row = cur.fetchone()
    n_rec = row["n"] if isinstance(row, dict) else row[0]
    run("SELECT COUNT(*) AS n FROM defects")
    row = cur.fetchone()
    n_def = row["n"] if isinstance(row, dict) else row[0]
    conn.close()

    print(f"演示数据生成完成：")
    print(f"  - 厂家 {len(FACTORIES)} 家 × 产线 {args.lines} 条 × 天数 {args.days} 天")
    print(f"  - inspection_records: {n_rec} 条")
    print(f"  - defects: {n_def} 条")
    if not args.no_anomaly:
        print(f"  - 已埋异常：{ANOMALY_FACTORY} 近 {ANOMALY_LAST_DAYS} 天检出率上升 + 划伤权重提高，供主动洞察识别")


if __name__ == "__main__":
    main()
