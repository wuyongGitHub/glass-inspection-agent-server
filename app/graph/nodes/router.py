"""意图路由节点：关键词规则前置判定，未命中再交 LLM 结构化输出 qa / data / chat。

说明：网关 LLM 对玻璃行业术语的意图判断不稳定（同一问题多次结果不一），
因此先按业务关键词规则锁定常见问法，减少"应力斑如何形成"被误判成闲聊的问题。
"""
from __future__ import annotations

import re
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config import settings
from app.domain.terms import resolve_defect
from app.llm import get_llm
from app.logging import log
from app.state import AgentState

# 知识问答特征词：机理成因 / 判定标准 / 工艺 / 缺陷与检测术语
QA_HINT = [
    "如何形成", "怎么形成", "如何产生", "成因", "什么原因", "为什么会", "为何",
    "怎么区分", "如何区分", "区别", "差异", "是什么", "什么意思", "定义",
    "判定", "判级", "标准", "等级", "阈值", "合格", "不合格", "规格", "公差",
    "sop", "工艺", "参数", "退火", "钢化", "应力", "应力斑", "条纹", "平整度",
    "透过率", "选型", "光源", "相机", "镜头", "分辨率", "明场", "暗场", "同轴",
    "背光", "线扫", "面阵", "偏光", "检测方法", "怎么检", "怎么测", "缺陷",
    "气泡", "崩边", "杂质", "结石", "划伤", "倒角", "字符", "漏墨", "缺印",
    "油墨", "麻点", "裂纹", "夹渣", "白斑", "黑点", "玻璃", "应该用", "包括哪些",
    # 公司/企业/机构介绍类（知识库可能收录公司简介、行业背景等）
    "怎么样", "介绍", "简介", "概况", "背景", "公司", "企业", "集团",
    "是做什么的", "做什么的", "主营",
]
# 时间范围词：相对时间指向数据查询（"近两周/最近/上周…"）
# 修复"晶捷电子玻璃近两周划伤变多了"因"近两周"不在旧词表而漏判数据流的问题
TIME_HINT = [
    "近两周", "近两星期", "近2周", "近几天", "近三天", "近七天", "近7天", "近3天",
    "近一周", "近一个月", "近30天", "上周", "本周", "这周", "最近", "昨天", "今天",
    "上月", "本月", "这几天", "近些天",
]
# 趋势/变化词：数据上升/下降信号（"变多/增多/升高/下降…"）
TREND_HINT = [
    "变多", "增多", "升高", "上升", "下降", "减少", "变少", "变高", "变低",
    "提升", "恶化", "好转", "飙升", "骤增", "激增", "骤降", "偏高", "偏低",
    "变好", "变差", "多不多", "多了",
]
# 数据查询特征词：统计 / 报表 / 图表 / 时间范围 / 数量对比
DATA_HINT = [
    "检出率", "不良率", "缺陷率", "合格率", "统计", "统计一下", "趋势",
    "对比", "比较", "排行", "排名", "top", "报表", "图表", "饼图", "柱状",
    "折线", "曲线", "数据", "占比", "环比", "同比", "多少", "几个", "多少件",
    "数量", "产量", "检测量", "缺陷量", "产能", "良品", "次品", "最近", "近一周",
    "近一个月", "近30天", "按厂", "厂家", "各厂", "哪家",
    "构成", "分布", "堆叠", "热力", "矩阵", "双轴", "看板", "大屏", "几张图",
    # 数据分析意图常用表述（修复"整体分析…数据"被平局偏向误判为 qa 的问题）
    "整体", "综合分析", "分析一下", "分析", "全面", "综合", "总结", "汇总",
    "报告", "情况", "表现", "业绩", "质量",
    # V2 维度扩展：产线 / 班次 / 严重度 / 设备参数
    "产线", "班次", "白班", "夜班", "严重度", "设备参数",
] + TIME_HINT + TREND_HINT

# 率值/数量类强数据词：只要出现，即便句中同时含缺陷名等知识词，也明确是数据查询
# （修复"划伤检出率多少""气泡不良率"被平局偏向误判为 qa 的问题）
RATE_HINT = [
    "检出率", "不良率", "缺陷率", "合格率",
    "数量", "多少件", "缺陷量", "产量", "检测量", "环比", "同比",
] + TREND_HINT

# 厂家名 + 数据意图词 → 直接走数据流。
# 先读库内真实厂家名（缓存），防止"耀华公司的数据/统计/分析"这类
# 知识词与数据词数量相当的问题，因平局偏向 qa 而去查文档库。
DATA_STRONG_HINT = [
    "数据", "统计", "分析", "趋势", "对比", "报告", "报表", "看板", "大屏",
    "占比", "构成", "分布", "排行", "情况", "整体", "综合", "汇总", "总结",
    "检出率", "合格率", "缺陷率", "不良率", "缺陷量", "产量", "质量", "数量",
] + TIME_HINT + TREND_HINT

# 指代/追问特征：必须是「明确承接上文的指代词」，而非泛泛的疑问词。
# 注意：不要把"为什么/怎么/呢"这类高频疑问词当作强继承信号——
# 否则"为什么玻璃会碎""怎么修打印机"这种新话题也会被误判为对上一轮的追问。
FOLLOWUP_HINT = [
    "那", "那再", "再", "接着", "继续", "然后", "还有", "另外", "顺便",
    "详细", "具体", "展开", "这些", "那些", "它", "它们", "这个", "那个",
    "这样", "再对比", "也对比", "再按", "按厂", "换成", "改成", "别的",
    "其他", "其它", "还有吗", "再查", "再看",
]

# 纯问候/寒暄：整句等于这些词时直接判 chat，防止继承上一轮意图或被 LLM 误判成 data
GREETING_WORDS = {
    "你好", "您好", "你们好", "嗨", "哈喽", "hi", "hello", "hey",
    "在吗", "在不在", "早上好", "下午好", "晚上好", "大家好",
    "你好呀", "您好呀", "你好啊", "您好啊", "你好吗", "您 好", "你 好",
}

# 确认/收尾/致谢词：整句恰为这些词时判 chat 并简短回应，
# 避免"好的/嗯/谢谢/知道了"带着上一轮上下文被 LLM 重复输出长内容
ACK_WORDS = [
    "好的", "好", "好滴", "好嘞", "好呢", "行", "行吧", "可以", "可以吧",
    "ok", "okay",
    "嗯", "嗯嗯", "嗯好", "嗯好的",
    "知道了", "懂了", "明白", "明白了", "了解", "收到", "明白啦", "懂啦",
    "谢谢", "感谢", "谢谢啦", "多谢", "辛苦了", "谢谢哦", "感谢啦",
    "没问题", "没事", "不用谢", "不客气", "别客气",
    "对", "是的", "没错", "对的",
]

# 诊断意图：根因/异常类触发词（需与数据/趋势/厂家信号配合，避免误伤机理问答）
DIAGNOSIS_ROOT = ["为什么", "什么原因", "原因", "异常", "定位", "改善",
                  "怎么改善", "如何改善", "复盘", "有没有类似", "类似案例"]
# 数据/趋势变化信号：说明用户在看"数据为什么变了"，而非纯知识机理
DIAGNOSIS_DATA = ["变多", "增多", "升高", "上升", "突然", "飙升", "骤增", "激增",
                  "偏高", "下降", "骤降", "趋势", "最近", "近一周", "近一个月",
                  "检出率", "缺陷率", "不良率"]


def is_greeting(text: str) -> bool:
    """整句（去空白与常见标点后）恰为问候语，或由问候词反复拼接而成，均算寒暄。"""
    s = re.sub(r"[\s，,。.!！?？~～]+", "", text).lower()
    if s in GREETING_WORDS:
        return True
    # 复读式问候（如"你好你好你好…"）也判 chat，避免落到 LLM 被误判成 data
    for g in GREETING_WORDS:
        if g and s and s.replace(g, "") == "":
            return True
    return False


def is_ack(text: str) -> bool:
    """整句（去空白与常见标点后）恰为确认/收尾/致谢词，或由这些词复读组成。

    识别"好的/嗯/OK/谢谢/知道了"这类会话收尾语，供路由判 chat 并在闲聊节点
    极简回应，避免其带着上一轮上下文被 LLM 重复输出一大段业务内容。
    """
    s = re.sub(r"[\s，,。.!！?？~～]+", "", text).lower()
    if not s:
        return False
    if s in ACK_WORDS:
        return True
    # 复读式确认（"好的好的好的"/"谢谢谢谢"）：多字词才能拆分，避免单字"好/对"误伤
    for a in ACK_WORDS:
        if len(a) >= 2 and s.replace(a, "") == "":
            return True
    return False


def is_gibberish(text: str) -> bool:
    """判定乱码/无意义输入：纯符号、同字符反复、键盘乱打式随机 ASCII 串。

    用于在规则层把"asdadadasd""aaaaaaaaaaaa""###@@@"这类输入直接判 chat，
    避免落到 LLM 兜底（历史上被误判为 data 的来源）。
    """
    s = text.strip()
    if not s:
        return False
    # 纯符号/emoji（不含字母、数字、中文）
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", s):
        return True
    # 同一字符反复（字母/数字/中文），如 aaaaaaaaaa / 111111 / 哈哈哈哈哈
    if len(s) >= 4 and len(set(s)) == 1:
        return True
    # 纯 ASCII（无空格无中文）且字符种类极少、长度较长：典型键盘乱打
    if re.fullmatch(r"[a-z0-9]+", s.lower()):
        if len(s) >= 8 and len(set(s)) <= 4:
            return True
    # 中文乱打：去标点后为纯中文、长度足够，且存在某个 3 字片段重复出现
    # （如"擦大大的发达十大的阿松大啊但是阿松大"中的"阿松大"）。
    # 3 字重复在正常中文里极少出现，能避开"很好很好/谢谢/你好你好"等误伤。
    zh = re.sub(r"[\s，,。.!！?？~～、:：;；]+", "", s)
    if len(zh) >= 6 and re.fullmatch(r"[\u4e00-\u9fff]+", zh):
        seen: set[str] = set()
        for i in range(len(zh) - 2):
            g = zh[i : i + 3]
            if g in seen:
                return True
            seen.add(g)
    return False


def _diagnosis_intent(text: str) -> bool:
    """诊断意图判定：根因/异常触发词 + 数据变化信号 或 厂家名，才走诊断。

    纯"划伤的原因是什么""为什么玻璃会碎"这类无数据/厂家信号的机理问答仍走 qa。
    """
    if not settings.diagnosis_enabled:
        return False
    q = text.lower()
    if not any(w in q for w in DIAGNOSIS_ROOT):
        return False
    if any(w in q for w in DIAGNOSIS_DATA):
        return True
    if any(w in q for w in ("为什么", "原因")):
        return any(name in text for name in _load_factory_names())
    return False


# 库内厂家名缓存：None=未加载，[]=加载失败或库为空
_FACTORY_NAMES: list[str] | None = None


def _load_factory_names() -> list[str]:
    """读取数据库内真实厂家名，用于"厂家名+数据意图词"强命中规则。

    仅在首次调用时连库一次；DB 缺失/异常时返回 []（退回关键词/LLM 判定），
    不让路由因库不可用而崩。
    """
    global _FACTORY_NAMES
    if _FACTORY_NAMES is not None:
        return _FACTORY_NAMES
    try:
        from app.db.session import get_conn

        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM factories")
            _FACTORY_NAMES = [r[0] for r in cur.fetchall() if r and r[0]]
        finally:
            if hasattr(conn, "close"):
                conn.close()
    except Exception:
        _FACTORY_NAMES = []
    return _FACTORY_NAMES


def _factory_data_hint(text: str) -> bool:
    """句中含库内真实厂家名 + 数据类意图词 → 数据流。

    解决"整体分析一下耀华建筑玻璃公司的数据"这类：知识词（玻璃）与数据词（数据）
    数量相当、被平局偏向误判为 qa 的问题。
    纯知识问法（含厂家名但无数据意图词）不受影响，仍走关键词/LLM。
    """
    if not any(w in text for w in DATA_STRONG_HINT):
        return False
    return any(name in text for name in _load_factory_names())


def _is_followup(text: str) -> bool:
    """判定是否为承接上文的追问/指代。

    仅当句子短、且含明确的承接指代词（那/再/还有/换成…）时才视为追问。
    纯粹的疑问句（为什么/怎么…）不在此列，避免误继承上一轮意图。
    """
    stripped = text.strip()
    if not stripped:
        return False
    # 必须命中明确指代词，且句子足够短（追问通常很简短）
    if len(stripped) <= 12 and any(w in stripped for w in FOLLOWUP_HINT):
        return True
    # 极短且是纯疑问语气（如"呢？""还有吗"），视为追问
    if len(stripped) <= 4 and stripped.endswith(("?", "？", "呢")):
        return True
    return False


def _keyword_intent(text: str) -> str | None:
    """规则判定：命中数多者胜；qa 与 data 平局倾向 qa；均未命中返回 None 走 LLM。"""
    q = text.lower()
    qa_hits = sum(1 for w in QA_HINT if w in q)
    data_hits = sum(1 for w in DATA_HINT if w in q)
    if qa_hits == 0 and data_hits == 0:
        return None
    # 率值/数量类强数据词优先于缺陷名知识词
    if any(w in q for w in RATE_HINT):
        return "data"
    # 时间词（今天/昨天/最近…）只是修饰语，不能单独充当数据意图信号，
    # 否则“今天吃什么”“今天天气不错”这类闲聊会被误判成数据查询。
    non_time_hits = sum(1 for w in DATA_HINT if w in q and w not in TIME_HINT)
    if non_time_hits == 0:
        return "qa" if qa_hits > 0 else None
    if data_hits > qa_hits:
        return "data"
    return "qa"


def deterministic_route(text: str) -> str | None:
    """纯规则路由（不调 LLM、不继承上下文），供离线评测与单测复用。

    返回 chat / data / diagnosis / qa 之一；规则层无法判定时返回 None（需 LLM 兜底）。
    与 router_node 的规则优先级保持一致：问候 > 厂家+数据 > 诊断 > 关键词。
    """
    if is_greeting(text):
        return "chat"
    if is_ack(text):
        return "chat"
    if is_gibberish(text):
        return "chat"
    # 诊断优先于"厂家+数据"：避免"为什么…变多/升高"这类根因问法被误判为纯数据查询
    if _diagnosis_intent(text):
        return "diagnosis"
    if _factory_data_hint(text):
        return "data"
    return _keyword_intent(text)


ROUTER_PROMPT = """你是玻璃工业视觉检测部门的智能体路由器。
部门业务：通过相机+光源采集图像，用 YOLO 模型检测建筑/家电/电子玻璃缺陷
（气泡、崩边、杂质、结石、划伤、倒角不良、字符缺失、漏墨、缺印、油墨不良、麻点、应力斑、条纹等），
检测结果上云分析，生成报表大屏与优化建议。

判定用户意图，选择最合理的一类：
- qa：玻璃缺陷成因/工艺机理类提问（"如何形成""什么原因""怎么区分""为什么会出现"）、
      判定标准与数值、检测方法与光源/相机选型、SOP、术语解释，
      以及公司/企业/机构介绍、行业背景等知识问答（如"XX公司怎么样""介绍一下XX""XX是做什么的"）。
      示例："气泡和结石怎么区分？" "玻璃应力斑是如何形成的？" "划伤的判定标准？" "聚玻工业科技怎么样？"
- data：查询或统计生产检测数据并产出报表/图表/建议，
      如检出率、数量、趋势、厂家对比、缺陷排行、饼图/柱状图等。
      示例："最近一个月检出率多少？" "各厂家对比出个报表。"
- chat：问候、闲聊、与玻璃业务完全无关的话题。示例："你好" "你是谁" "讲个笑话"

倾向规则：问题中出现玻璃缺陷名、工艺词、标准、数据类关键词时，偏向 qa/data；
不确定时优先 qa 或 data，不要轻易判为 chat。
但无法理解、乱码、无意义字符、纯重复字母/符号、与玻璃业务完全无关的内容，一律判为 chat。

只输出一个 JSON 对象，例如 {"intent": "qa"}，不要输出任何其他文字。"""


class RouteDecision(BaseModel):
    intent: Literal["qa", "data", "chat"] = Field(description="用户意图分类")


def router_node(state: AgentState) -> dict[str, object]:
    messages = state.get("messages") or []
    last_text = messages[-1].content if messages else ""
    last_intent = state.get("last_intent")

    # 纯问候硬规则：直接进 chat，绝不上数据流，也绝不继承上一轮意图
    if is_greeting(last_text):
        return {"intent": "chat", "last_intent": "chat"}

    # 确认/收尾/致谢词硬规则：直接进 chat 并简短回应，避免带上下文重复输出长内容
    if is_ack(last_text):
        return {"intent": "chat", "last_intent": "chat"}

    # 乱码/无意义输入硬规则：直接进 chat（与 prompt 兜底保持一致）
    if is_gibberish(last_text):
        return {"intent": "chat", "last_intent": "chat"}

    # 诊断意图优先于"厂家+数据"：根因/异常 + 数据信号 → 走 diagnosis，
    # 避免"为什么晶捷电子玻璃划伤变多了"这类根因问法被"厂家+趋势词"误判为纯数据查询
    if _diagnosis_intent(last_text):
        log("info", "router.decide", intent="diagnosis", query=last_text[:40])
        return {"intent": "diagnosis", "last_intent": "diagnosis"}

    # 厂家名 + 数据/时间/趋势词强命中：直接走数据流（优先于关键词/LLM 判定）
    if _factory_data_hint(last_text):
        return {"intent": "data", "last_intent": "data"}

    intent = _keyword_intent(last_text)

    # 多轮继承：仅在「明确的纯指代追问」时沿用上一轮意图。
    # 只有上轮是 data 且当前是承上追问（"那按厂家对比下""还有吗"），
    # 或上轮是 qa 且当前是承上追问，才继承；否则交给关键词/LLM 重新判定，
    # 避免"随便发一句话"被错误地锁死在上轮的 data 上。
    if intent is None and last_intent and _is_followup(last_text):
        intent = last_intent

    if intent is None:
        try:
            # 意图分类只看「当前这条消息」，不塞历史消息给 LLM。
            # 否则上一条是数据分析时，历史里的大量数据报告会带偏 LLM，
            # 导致"下一条无论发什么都判成 data"。
            # 多轮指代继承已由上面的 _is_followup + last_intent 规则处理。
            decision: Any = (  # langchain stub 返回 union，json_mode 下运行时恒为 RouteDecision
                get_llm(temperature=0)
                .with_structured_output(RouteDecision, method="json_mode")
                .invoke([
                    SystemMessage(content=ROUTER_PROMPT),
                    HumanMessage(content=last_text),
                ])
            )
            intent = decision.intent
        except Exception:
            intent = "chat"  # 路由失败时退化为闲聊，保证服务可用
    log("info", "router.decide", intent=intent, query=last_text[:40])
    return {"intent": intent, "last_intent": intent}
