---
name: fused-ppt-agent
description: 学术 PPT 融合管线 —— 输入一段文字（学术科普/研究计划/文献/论文），输出原生可编辑 PPTX。无 AI 生图，表格精美、可画思维导图、有阴影与文本框。
version: 0.2.0
---

# 学术 PPT 融合管线 · 工作流

> 本文档是给 Agent（Claude Code / 其他 LLM）看的"完整工作流"。
> 目标：一段文字 → 提炼 → PPT 计划 → 生成原生可编辑 PPTX（比 ChatGPT 的 PPT 更好）。

## 0. 读我顺序

1. 先读 `docs/ARCHITECTURE.md`（融合思路）
2. 按本文件 Step 1-5 执行
3. 涉及"怎么画"的规范见 `pptagent/svggen.py` 与 `pptagent/design/tokens.py`
4. 涉及"内容结构"的契约见 `pptagent/schema.py`

## 1. 输入

用户给一段文字，类型任选：
- **学术科普**（面向大众讲清一个科学话题）
- **研究计划**（课题背景/目标/方法/时间线）
- **文献解读**（一篇论文的背景/方法/结果/意义）
- **综述/讲义**

## 2. Step 1 · 提炼 → ContentGraph

把文字提炼成结构化 JSON（`pptagent/schema.py` 的 CONTENT_GRAPH_SCHEMA），存到
`work/<deck_name>/content_graph.json`。规则：

- **忠实原文**：只提炼不发明，数据/结论必须有出处（原文里有）。
- **学术结构**：识别 背景/方法/结果/意义 等章节骨架。
- **表格优先**：凡有可对比的数据（指标对比、规模、时间线数据），放进 `tables` 或 `comparisons`——好表格比文字更有说服力。
- **思维导图素材**：把"概念之间的层级/分类关系"提取成 `concepts`（每节 2-6 个抽象概念），供 Step 2 决定是否用 mindmap 布局。
- **金句**：原文里有值得单列引用的句子，放进 `takeaway` / `sections[].takeaway`。
- 页数目标：学术科普 8-14 页，研究计划 10-16 页，文献解读 10-15 页，综述 12-20 页。若用户指定页数，遵守。

## 3. Step 2 · 计划 → DeckPlan

用 ContentGraph 生成逐页计划（`DECK_PLAN_SCHEMA`），存 `work/<deck_name>/deck_plan.json`。

**风格库（联网 few-shot，替代权重微调）**：规划前先跑一次检索——
```bash
python3 -m pptagent.styles pick work/<deck_name>/content_graph.json --genre <学术科普|文献解读|研究计划|综述>
```
- 返回**最相似的风格种子**（你打磨认可过的 deck）。参考它的 `layout_signature`
  （页面结构分配）与风格说明来编排新 deck——风格就稳定地贴近你的审美。
- 无匹配时按下方预算规则从零规划；这一版做完打磨认可后 `add` 成新种子。
- 种子越攒越多、越用越像你喜欢的样式——这就是"联网 finetune"（零训练、可回滚）。

**页面结构预算**（学术 PPT 惯例，改编自 paper-ppt-agent 的 page_type_budget）：

| 总页数 | cover | section 章节 | content 内容页 | 表格/对比/图谱 | 结语 |
|---|---|---|---|---|---|
| 8-10 | 1 | 2-3 | 2-3 | 2-3 | 1 |
| 11-15 | 1 | 3-4 | 3-5 | 3-5 | 1 |
| 16-20 | 1 | 4-5 | 5-7 | 4-6 | 1-2 |

**编排规则**：
- 首页一定是 `cover`；最后是 `takeaway`。
- 每 3-5 页内容后插一个 `section` 过渡页，制造节奏。
- 第 2 页（或第 3 页）放 `overview` 目录/全景图。
- 有可比数据的章节 → `table` 或 `compare`（不要塞进文字里）。
- 概念层级清晰、分支 2-4 个 → `mindmap`；有演化/流程 → `timeline`。
- 找到的金句 → 单独 `quote` 页，放在章节末或结尾前。
- 正文页 `content` 每页要点 2-4 条，每条 lead ≤ 16 字、body ≤ 40 字。
- `kicker` 用统一标签（如 `BACKGROUND · 02`），页眉呼应。

## 4. Step 3 · 生成

运行确定性代码：
```bash
cd <fused-pipeline 根目录>
python3 -m pptagent.run work/<deck_name>/deck_plan.json \
    --out work/<deck_name>/exports --engine ../ppt-master
```
这步自动完成：SVG 生成 → 质量门 → 原生 PPTX。

**若质量门失败**：读 `exports/validation/svg_quality_report.json` 的 errors，
回 Step 2 改 DeckPlan（通常是文字太长/表格列太多），重跑。

## 5. Step 4 · 目视检查与反思

- 渲染预览：`python3 -c "import cairosvg; cairosvg.svg2png(url='...svg', write_to='...png', output_width=1024)"`
- 检查：① 标题不换行过密 ② 表格列不挤压 ③ 思维导图分支不重叠 ④ 阴影不过重 ⑤ 无文字溢出页面。
- 不满意 → 改 DeckPlan / svggen 布局参数，重跑。**迭代 1-3 轮是常态。**

**打磨回写（风格积累）**：用户点评满意后，把这一版存成风格种子——
```bash
python3 -m pptagent.styles add <名称> work/<deck_name>/deck_plan.json \
    --genre <类型> --keywords "主题1,主题2" --notes "用户点评要点"
python3 -m pptagent.styles approve <名称>        # 标记认可；覆盖同名用 --overwrite
```
每攒一个种子，后续规划就越贴你的审美（见 Step 2 风格库）。

## 6. 设计纪律（不可违反）

来自 guizang "约束>创造" 哲学 + 学术规范，全部编码在 tokens.py / svggen.py：

1. **单一锚点色**：一个 deck 一种 accent，不混色。
2. **灰阶层级**：文字用 ink / ink_2 / ink_3，不用透明度表达层级。
3. **发丝线**：分隔用 0.5-1px 细线，不用粗边框。
4. **卡片互斥**：fill/ink/accent/outlined 四类，不同时叠加。
5. **阴影克制**：卡片 md、节点 sm，不叠加多层。
6. **无 AI 生图**：视觉表达靠 色块/几何/巨字/图标符号/排版留白。禁止生成图片。
7. **表格第一**：数据一律进表格，不用文字罗列。
8. **思维导图**：几何+短词，分支 ≤4 层，文字在节点内。

## 7. 输出物

```
work/<deck_name>/
  content_graph.json     # 提炼结果
  deck_plan.json         # 逐页计划
  svg_output/*.svg       # 每页 SVG（可编辑元素）
  exports/<name>.pptx    # ★ 原生可编辑 PPTX
  exports/preview/       # 渲染预览图 + index.html
  validation/*.json      # 质量门报告

styles/                  # ★ 风格库：打磨认可的种子（规划参考 + 风格积累）
```

## 8. 当前限制（诚实说明）

- 布局模板：12 种布局 v0.1，复杂图表（柱状/折线）尚未接入（native chart 接口已具备，未启用）。
- 中文折行是估算的，长词/长 URL 可能超出卡片。
- 阴影/圆角在 PPTX 中保留为原生效果，不同 Office 版本渲染略有差异。
- 无 LibreOffice 时无法把 PPTX 直接渲染成图做"截图像反思"，目前用 SVG 渲染预览代替。
