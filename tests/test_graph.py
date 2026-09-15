"""图构建冒烟测试：不调用 LLM，只验证依赖与图结构。

用法：
  python tests/test_graph.py
  或  pytest tests/test_graph.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_build_graph():
    from app.graph.builder import build_graph

    graph = build_graph()
    nodes = set(graph.get_graph().nodes.keys())
    expected = {
        "router", "vision_analyze", "qa_retrieve", "qa_generate",
        "data_parse", "data_execute", "data_analyze", "chat_reply",
        "diagnosis_plan", "diagnosis_run", "diagnosis_report",
    }
    missing = expected - nodes
    assert not missing, f"缺少节点: {missing}"
    return nodes


def test_query_executor():
    from datetime import date, timedelta

    from app.tools.query_executor import execute_query

    end = date.today().isoformat()
    start = (date.today() - timedelta(days=30)).isoformat()
    result = execute_query({"metric": "top_defects", "start_date": start, "end_date": end})
    assert result["metric"] == "top_defects"
    return len(result["rows"])


def test_dimension_metrics():
    """V2 维度扩展：按产线/班次/严重度/设备参数查询应能正常执行（无异常）。"""
    from datetime import date, timedelta

    from app.db.session import init_db
    from app.tools.query_executor import execute_query

    init_db()  # 幂等建表 + 轻量迁移（补 shift 列）
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=30)).isoformat()
    for metric in ("rate_by_line", "rate_by_shift", "severity_distribution", "equipment_params"):
        result = execute_query({"metric": metric, "start_date": start, "end_date": end})
        assert result["metric"] == metric, f"{metric} 未返回正确 metric"
        assert isinstance(result["rows"], list), f"{metric} 未返回 rows 列表"


def test_router_gibberish():
    """乱码/无意义输入应被规则层直接判为 chat（不依赖 LLM）。"""
    from app.graph.nodes.router import deterministic_route

    for text in ("asdadadasd", "aaaaaaaaaaaa", "###@@@", "1111111111", "哈哈哈哈哈"):
        assert deterministic_route(text) == "chat", f"{text!r} 应判为 chat"


def test_vision_empty_image():
    """视觉工具：未提供图片时应友好返回，不抛异常。"""
    from app.tools.vision import analyze_defect_image

    finding = analyze_defect_image("", None)
    assert finding["defect_type"] is None
    assert finding["description"]


if __name__ == "__main__":
    nodes = test_build_graph()
    print(f"graph 构建成功，节点: {sorted(nodes)}")
    try:
        n = test_query_executor()
        print(f"查询执行器正常，Top 缺陷返回 {n} 条（需先运行 scripts/seed_db.py）")
    except Exception as e:
        print(f"查询执行器未通过（数据库可能未初始化）: {e}")
    try:
        test_dimension_metrics()
        print("维度查询（产线/班次/严重度/设备参数）执行正常")
    except Exception as e:
        print(f"维度查询未通过（数据库可能未初始化）: {e}")
    test_router_gibberish()
    print("乱码路由规则正常")
    test_vision_empty_image()
    print("视觉工具空输入兜底正常")
    print("smoke test passed")
