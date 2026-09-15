# -*- coding: utf-8 -*-
"""临时端到端冒烟：真实调用 LLM，复现用户失败的问句。运行后删除。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.llm import get_llm

# 1) 最小模型连通测试
r = get_llm(temperature=0).invoke([{"role": "user", "content": "只回复两个字：正常"}])
print("LLM 连通 OK ->", r.content.strip()[:30])

# 2) 全链路：复现用户问句
from langchain_core.messages import HumanMessage

from app.graph.builder import build_graph

graph = build_graph()
out = graph.invoke({"messages": [HumanMessage(content="这周整体情况怎么样")]},
                   {"configurable": {"thread_id": "tmp-e2e"}})
print("intent =", out.get("intent"))
print("final_answer 前 120 字 =", (out.get("final_answer") or "")[:120].replace("\n", " / "))
cc = out.get("chart_config")
if isinstance(cc, dict):
    print("chart_config layout =", cc.get("layout", "single-option"),
          "| charts =", len(cc.get("charts", [])) if cc.get("layout") == "dashboard" else 1)
    print("kpis =", [k["key"] for k in cc.get("kpis", [])])
print("e2e ok")
