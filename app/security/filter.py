"""独立敏感词 / 违禁词过滤库（输入侧与输出侧内容安全）。

设计要点：
- 纯标准库实现，无第三方依赖；Aho-Corasick 多模式匹配，单次扫描命中全部词条；
- 词库按类别分文件存放（文件名即类别），便于维护与合规审计；
- 词条去重、去空白、去注释，英文大小写不敏感；
- 对外提供模块级单例，供 API 入口复用。

词库扩充建议（满足“超级全”的合规要求）：
1) 从专业内容安全服务商（腾讯云天御、网易易盾、百度内容审核等）获取全量词库导入；
2) 或由企业合规 / 法务部门提供内部词库；
3) 词库文件每行一个词，`#` 开头为注释，新增词条保存后重启服务即生效。
"""
from __future__ import annotations

import os
from collections import deque


class _AhoCorasick:
    """Aho-Corasick 自动机：多模式匹配，O(文本长度 + 命中数)。"""

    def __init__(self) -> None:
        self._go: dict[int, dict[str, int]] = {0: {}}
        self._fail: list[int] = [0]
        self._out: dict[int, list[tuple[str, str]]] = {}  # state -> [(word, category)]

    def add(self, word: str, category: str) -> None:
        state = 0
        for ch in word:
            nxt = self._go[state]
            if ch not in nxt:
                nxt[ch] = len(self._fail)
                self._go[nxt[ch]] = {}
                self._fail.append(0)
            state = nxt[ch]
        self._out.setdefault(state, []).append((word, category))

    def build(self) -> None:
        q: deque[int] = deque()
        for st in self._go[0].values():
            q.append(st)
        while q:
            r = q.popleft()
            for ch, s in self._go[r].items():
                q.append(s)
                f = self._fail[r]
                while f and ch not in self._go[f]:
                    f = self._fail[f]
                self._fail[s] = self._go[f].get(ch, 0)
                # 合并 fail 链上的输出，命中短词的同时也能命中其超集长词
                if self._fail[s] in self._out:
                    self._out.setdefault(s, []).extend(self._out[self._fail[s]])

    def search(self, text: str) -> list[tuple[int, int, str, str]]:
        """返回 [(start, end, word, category), ...]。"""
        state = 0
        hits: list[tuple[int, int, str, str]] = []
        for i, ch in enumerate(text):
            while state and ch not in self._go[state]:
                state = self._fail[state]
            state = self._go[state].get(ch, 0)
            for word, cat in self._out.get(state, []):
                hits.append((i - len(word) + 1, i + 1, word, cat))
        return hits


def _load_words_dir(words_dir: str) -> list[tuple[str, str]]:
    """扫描词库目录：每个 .txt 文件名即类别，每行一个词，`#` 开头为注释。"""
    items: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if not os.path.isdir(words_dir):
        return items
    for fn in sorted(os.listdir(words_dir)):
        if not fn.lower().endswith(".txt"):
            continue
        category = os.path.splitext(fn)[0]
        path = os.path.join(words_dir, fn)
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    word = line.strip()
                    if not word or word.startswith("#"):
                        continue
                    # 英文词条统一小写，匹配时对文本同样 lower，保证大小写不敏感
                    key = (word.lower(), category)
                    if key not in seen:
                        seen.add(key)
                        items.append(key)
        except OSError:
            continue
    return items


class SensitiveFilter:
    """敏感词过滤器：加载词库 -> 命中检测 -> 分类报告。"""

    def __init__(self, words_dir: str | None = None) -> None:
        self._ac = _AhoCorasick()
        self._categories: list[str] = []
        self.word_count = 0
        if words_dir:
            self.load(words_dir)

    def load(self, words_dir: str) -> None:
        items = _load_words_dir(words_dir)
        self._ac = _AhoCorasick()
        for word, category in items:
            self._ac.add(word, category)
        self._ac.build()
        self._categories = sorted({c for _, c in items})
        self.word_count = len(items)

    def check(self, text: str) -> list[dict]:
        """返回命中的词条列表，每项含 word / category / start / end（去重，按出现位置排序）。"""
        if not text:
            return []
        hits = self._ac.search(text.lower())
        dedup: dict[tuple[str, str, int], dict] = {}
        for start, end, word, category in hits:
            dedup.setdefault(
                (word, category, start),
                {"word": word, "category": category, "start": start, "end": end},
            )
        return sorted(dedup.values(), key=lambda x: x["start"])

    def is_blocked(self, text: str) -> bool:
        return bool(self.check(text))

    def categories(self) -> list[str]:
        return list(self._categories)


# 模块级单例：词库路径由 settings 注入，避免循环导入
_singleton: SensitiveFilter | None = None


def get_sensitive_filter() -> SensitiveFilter:
    """返回全局单例，惰性初始化（首次调用时从 settings 读取词库目录加载）。"""
    global _singleton
    if _singleton is None:
        from app.config import settings

        _singleton = SensitiveFilter(settings.sensitive_words_dir)
    return _singleton
