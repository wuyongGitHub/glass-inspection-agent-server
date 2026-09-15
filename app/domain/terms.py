"""术语标准化：同义词 / 别名 -> canonical 实体。

让 Router、RAG、SQL 参数解析、Case 检索在拿到用户原始文本后，先归一到
ontology.py 定义的 canonical 实体，减少"划痕/擦伤/表面划伤"这类同义表达
导致的路由误判、检索漏召回与 SQL 参数错配。
"""
from __future__ import annotations

from app.domain.ontology import DEFECT_TYPES, GLASS_TYPES

# 缺陷别名 -> canonical 缺陷名（注意：别名的 key 需比 canonical 更长/更具体，
# resolve 时先命中 canonical 子串即可，别名仅用于 canonical 未命中的情况）
DEFECT_ALIASES = {
    "划痕": "划伤", "擦伤": "划伤", "刮伤": "划伤", "表面划伤": "划伤",
    "气孔": "气泡",
    "崩口": "崩边", "缺角": "崩边", "边缘崩": "崩边",
    "杂物": "杂质", "异物": "杂质", "杂志": "杂质",
    "石子": "结石",
    "倒角": "倒角不良", "倒边不良": "倒角不良",
    "漏印": "缺印", "缺字": "字符缺失", "缺字符": "字符缺失",
    "油墨": "油墨不良",
    "针孔": "麻点", "麻坑": "麻点", "麻点儿": "麻点",
}

# 玻璃类型别名 -> canonical（"eg" 等英文缩写按小写匹配）
GLASS_ALIASES = {
    "建筑": "建筑玻璃", "建材": "建筑玻璃",
    "家电": "家电玻璃", "电器": "家电玻璃",
    "电子": "电子玻璃", "eg": "电子玻璃", "盖板": "电子玻璃", "基板": "电子玻璃",
}


def normalize_defect(term: str) -> str | None:
    """把单个缺陷词归一为 canonical 缺陷名，无法识别返回 None。"""
    if not term:
        return None
    t = term.strip()
    if t in DEFECT_TYPES:
        return t
    return DEFECT_ALIASES.get(t)


def resolve_defect(text: str) -> str | None:
    """从整句文本中识别缺陷实体，返回 canonical 缺陷名（优先精确命中）。"""
    if not text:
        return None
    for d in DEFECT_TYPES:
        if d in text:
            return d
    for alias, canon in DEFECT_ALIASES.items():
        if alias in text:
            return canon
    return None


def normalize_glass_type(term: str) -> str | None:
    if not term:
        return None
    t = term.strip()
    if t in GLASS_TYPES:
        return t
    return GLASS_ALIASES.get(t.lower())


def resolve_glass_type(text: str) -> str | None:
    """从整句文本中识别玻璃类型实体，返回 canonical 玻璃类型。"""
    if not text:
        return None
    for g in GLASS_TYPES:
        if g in text:
            return g
    low = text.lower()
    for alias, canon in GLASS_ALIASES.items():
        if alias in low:
            return canon
    return None


def resolve_entities(text: str) -> dict:
    """一次性抽取缺陷 / 玻璃类型实体，供路由与参数解析共用。"""
    return {
        "defect_type": resolve_defect(text),
        "glass_type": resolve_glass_type(text),
    }
