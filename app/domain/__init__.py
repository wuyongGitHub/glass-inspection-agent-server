"""领域层（V2 转包）：本体的 canonical 实体 + 术语标准化 + 历史业务常量。

为兼容既有 `from app.domain import DEFECT_TYPES / GLASS_TYPES / METRICS /
DEFECT_GUIDANCE` 的引用，这里直接重导出这些常量；新增实体见 ontology.py，
同义词归一见 terms.py。
"""
from app.domain.ontology import (  # noqa: F401
    DEFECT_GUIDANCE,
    DEFECT_TYPES,
    GLASS_TYPES,
    METRICS,
    SEVERITIES,
    DefectType,
    GlassType,
    Severity,
)
from app.domain.terms import (  # noqa: F401
    normalize_defect,
    normalize_glass_type,
    resolve_defect,
    resolve_entities,
    resolve_glass_type,
)
