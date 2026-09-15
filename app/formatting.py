"""统一回复排版规范：让所有节点的最终回答更醒目、易读、有层次。

前端使用 markdown-it（GFM 风格）+ v-html 渲染，且已开启 html: true（见
docs/answer-formatting.md）。本规范在 Markdown 结构（列表 / 表格 / 分隔线 /
emoji）基础上，要求关键内容用 HTML 内联样式上色。
"""

FORMAT_GUIDE = """【排版与呈现规范】请严格遵守，让回复醒目、易读、有层次。前端已支持 HTML 内联样式，请按要求给关键内容上色：
1. 每个章节用「emoji + 标题」单独成行，标题用蓝色加粗，例如 <strong style="color:#1f6feb">📊 数据摘要</strong>、<strong style="color:#1f6feb">⚠️ 异常与风险</strong>。
2. 关键缺陷名、关键数字、核心结论用红色加粗高亮，例如 <strong style="color:#d64541">气泡、结石、划伤、崩边、波筋</strong>、检出率 <strong style="color:#d64541">3.2%</strong>。
3. 警示 / 风险 / 超标点用橙色 <span style="color:#e36209">…</span>；正常 / 达标 / 合规项用绿色 <span style="color:#22863a">…</span>。
4. 多条并列或需对比的信息，优先用无序列表或表格；列表 / 表格内的关键文字同样按第 2、3 条上色。
5. 章节之间可用 --- 分隔线增强层次。
6. 颜色只用于真正重要的信息，不要整段或整句上色；语言自然、简洁，避免生硬套话。
"""
