# 回复排版与配色说明

## 方案：后端输出带颜色 HTML（已启用）

所有回复节点（数据 / 问答 / 诊断 / 视觉 / 文件问答 / 闲聊）已统一注入排版规范
（见 `app/formatting.py`）。规范要求关键内容用 HTML 内联样式上色：

- 章节标题：蓝色加粗 `<strong style="color:#1f6feb">📊 数据摘要</strong>`
- 关键缺陷名 / 数字 / 结论：红色加粗 `<strong style="color:#d64541">气泡、结石、划伤、崩边、波筋</strong>`
- 警示 / 风险 / 超标：橙色 `<span style="color:#e36209">…</span>`
- 正常 / 达标 / 合规：绿色 `<span style="color:#22863a">…</span>`
- 结构仍用 Markdown 原生：无序列表、表格、`---` 分隔线、emoji 图标

## 前端必须开启 html（否则 `<span>` 会显示成字面文本）

前端初始化 markdown-it 时，务必开启 `html`：

```js
const md = new MarkdownIt({ html: true, linkify: true });
```

注意：`html: true` 会放宽 XSS 面，请在 `v-html` 前用 DOMPurify 等 sanitize 处理
（建议仅放行 `strong` / `span` 及其 `style` 中的 `color` / `font-weight` 属性）。

## 可选：前端 CSS 兜底（不是必需）

后端已输出内联 `style`，内联优先级高于外部 CSS，故 CSS 仅在模型偶发漏上色时兜底：

```css
.markdown-body strong { color: #d64541; }
```
