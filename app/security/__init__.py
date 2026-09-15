"""内容安全模块：敏感词 / 违禁词过滤。"""
from app.security.filter import SensitiveFilter, get_sensitive_filter

__all__ = ["SensitiveFilter", "get_sensitive_filter"]
