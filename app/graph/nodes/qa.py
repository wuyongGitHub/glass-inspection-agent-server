"""问答流：向量检索部门知识库 -> 基于片段生成带引用的回答。"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.config import settings
from app.formatting import FORMAT_GUIDE
from app.llm import get_embeddings, get_llm
from app.rag.query_rewrite import rewrite_query
from app.rag.reranker import rerank_top
from app.rag.store import SimpleVectorStore
from app.state import AgentState

# QA_FALLBACK=true（默认）：片段不足/无关时，先用 LLM 通用知识回答（标注参考口径）
QA_PROMPT_FALLBACK = """你是玻璃检测部门的知识助手。根据【知识库片段】的情况分两类作答：

【情况A】片段与问题主题相关且足以支撑回答：
- 只依据片段回答；给出数值/等级时以片段为准；片段未覆盖的细节不要编造。

【情况B】片段与问题主题明显无关（知识库未收录该主题），或片段不足以支撑：
- 先在回答开头用一句话说明："当前知识库尚未收录该主题的文档，以下为通用知识参考。"
- 再基于你自身的通用玻璃行业知识给出参考性回答。
- 结尾标注："以上为通用知识参考，非本部门检验标准口径，正式判定请以部门文档或实测为准。"
- 不得引用与主题无关的知识库片段强行作答。

通用要求：
1. 使用简体中文，条理清晰。
2. 只回答对话中【最后一条用户消息】提出的问题；更早的历史问题仅作背景参考，不要复述或重答旧问题。
3. 正文结束后，固定追加一个「💡 聚玻明视建议」板块（蓝色加粗标题"💡 聚玻明视建议"单独成行），
   给出针对本问题的可执行建议：
   - 用无序列表，每条写成「<strong style="color:#1f6feb">小标题</strong>：说明」格式，2~4 条为宜；
   - 小标题要短（如"先诊断后处理""产品本身质量问题""设备维保"），说明给出具体可落地动作；
   - 建议贴合问题场景，可结合片段与通用玻璃行业经验，但不得编造具体数值、等级或标准；
   - 若问题偏定义/机理，建议转为"如何识别 / 预防 / 处理"的实操角度。

示例（仅示范格式，内容以实际问题为准）：
<strong style="color:#1f6feb">💡 聚玻明视建议</strong>
- <strong style="color:#1f6feb">先诊断后处理</strong>：用手触摸感受是否有物理起伏，结合光源反射判断是光学变形还是表面附着物。
- <strong style="color:#1f6feb">产品本身质量问题</strong>：若是新采购原片或成品出现明显波筋且非加工造成，建议联系供应商退货或索赔。
- <strong style="color:#1f6feb">设备维保</strong>：对加工厂，建立定期辊道水平度检测与温控系统校准机制是预防波纹的关键。

【知识库片段】
{context}"""

# QA_FALLBACK=false：严格模式，仅依据知识库片段，未收录主题一律拒答
QA_PROMPT_STRICT = """你是玻璃检测部门的知识助手，仅依据下面给出的知识库片段回答问题。
要求：
1. 若片段不足以回答，明确说明知识库中没有相关内容，并建议补充文档，不要编造
2. 使用简体中文，条理清晰，可在末尾标注来源文件
3. 涉及判定标准时给出具体数值/等级，片段没有数值就不要虚构
4. 只回答对话中【最后一条用户消息】提出的问题；更早的历史问题仅作背景参考，不要复述或重答旧问题
5. 若知识库片段与当前问题主题明显无关（检索未命中该主题），明确回答"知识库尚未收录该主题的相关资料，可补充文档后重试"，不要用无关片段强行作答
6. 正文结束后，固定追加一个「💡 聚玻明视建议」板块（蓝色加粗标题"💡 聚玻明视建议"单独成行），
   给出针对本问题的可执行建议：
   - 用无序列表，每条写成「<strong style="color:#1f6feb">小标题</strong>：说明」格式，2~4 条为宜；
   - 建议严格依据片段，片段未覆盖的实操细节不要虚构；
   - 若片段不足以支撑建议，可简要说明"该主题暂无可参考的实操建议，建议补充文档"。

示例（仅示范格式，内容以实际问题为准）：
<strong style="color:#1f6feb">💡 聚玻明视建议</strong>
- <strong style="color:#1f6feb">先诊断后处理</strong>：用手触摸感受是否有物理起伏，结合光源反射判断是光学变形还是表面附着物。
- <strong style="color:#1f6feb">产品本身质量问题</strong>：若是新采购原片或成品出现明显波筋且非加工造成，建议联系供应商退货或索赔。
- <strong style="color:#1f6feb">设备维保</strong>：对加工厂，建立定期辊道水平度检测与温控系统校准机制是预防波纹的关键。

【知识库片段】
{context}"""


def qa_retrieve(state: AgentState) -> dict:
    store = SimpleVectorStore(settings.vector_store_path)
    if not store.chunks:
        return {"qa_context": [], "citations": []}
    question = state["messages"][-1].content
    query = rewrite_query(question)
    query_vec = get_embeddings().embed_query(query)
    hits = rerank_top(store, query, query_vec)
    citations = [h["meta"]["source"] for h in hits]
    return {
        "qa_context": [f'{h["text"]}\n(来源: {h["meta"]["source"]})' for h in hits],
        "citations": citations,
    }


def qa_generate(state: AgentState) -> dict:
    context = "\n\n---\n\n".join(state.get("qa_context") or [])
    if not context:
        return {
            "final_answer": (
                "知识库还是空的。请把部门文档（检测标准、判级细则、SOP 等，支持 .md/.txt）"
                "放入 data/docs/ 目录，然后运行 `python -m app.rag.ingest` 构建索引。"
            ),
            "chart_config": None,
        }
    # 知识库未收录主题时：默认回退模型通用知识（QA_FALLBACK=true），
    # 设 false 则严格拒答并建议补充文档
    prompt = QA_PROMPT_FALLBACK if settings.qa_fallback else QA_PROMPT_STRICT
    # 历史消息只作背景，模型只需回答最后一条用户消息，避免逐条复述旧问题
    msgs = [SystemMessage(content=prompt.format(context=context) + "\n\n" + FORMAT_GUIDE)]
    history = state.get("messages") or []
    if len(history) > 1:
        hist_lines = [
            f"{'用户' if isinstance(m, HumanMessage) else '助手'}: {m.content}"
            for m in history[:-1]
            if isinstance(m, (HumanMessage, AIMessage))
        ]
        if hist_lines:
            msgs.append(
                SystemMessage(content="以下是此前的对话历史（仅作背景参考，不要回答其中的旧问题）：\n" + "\n".join(hist_lines))
            )
    msgs.append(HumanMessage(content=history[-1].content if history else ""))
    try:
        answer = get_llm().invoke(msgs)
    except Exception:
        # 模型网关不可用时给出友好提示，不让问答流 500
        return {
            "final_answer": "抱歉，模型服务暂时不可用，请稍后重试。",
            "chart_config": None,
        }
    return {"final_answer": answer.content, "chart_config": None}
