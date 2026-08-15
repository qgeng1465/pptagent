# 学术 PPT 融合管线 · 架构文档

> 目标：输入一段文字（学术科普 / 研究计划 / 文献 / 论文）→ 提炼 → PPT 计划 →
> 生成**原生可编辑 PPTX**。全程**无 AI 生图**，但表格精美、可画思维导图、
> 阴影/文本框等真实设计元素齐全，目标是**超过 ChatGPT 的 PPT 生成质量**。
>
> 2026-08-04 建立。引擎验证通过（SVG→PPTX 全链路 + 阴影注入）。

---

## 0. 一句话

**以 ppt-master 的 SVG→PPTX 转换器为引擎，吸收四个仓库的精华，外包一层"学术
设计 + 学术规划"定制层，由 Claude Code 当大脑（提炼+规划），确定性代码做渲染。**

## 1. 四大仓库取长补短

| 仓库 | 吸收什么 | 放弃什么 |
|---|---|---|
| **ppt-master** | ① SVG→可编辑PPTX 转换器（原生表格/图表/阴影/预设形状/母版）② skill 工作流模式（Claude Code 当大脑）③ 质量门 svg_quality_checker ④ design_spec 十节结构 | 图片/AI生图环节、TTS 旁白、WebUI 确认（对本管线太重） |
| **paper-ppt-agent** | ① 学术手稿分页预算 page_type_budget（论文结构→页数分配）② LLM 多遍深读结构（Pass1-4）③ 静态 SVG 审查器（溢出/重叠/对比度，无需 LibreOffice）④ 纯 SVG 视觉表达（无 AI 生图） | 前端/后端 Web 服务、在线搜图、Windows COM 依赖 |
| **PPTAgent** | ① "渲染→截图→反思修正"的闭环理念 ② 学术模板与布局归纳思路 | Docker/Playwright/API-key 依赖；训练代码未开源 |
| **guizang-ppt-skill** | ① "约束>创造"设计哲学 ② 单一锚点色 + 发丝线 + 灰阶层级 ③ 卡片类型互斥 ④ rowline 表格 ⑤ SVG 只画几何不写文字（思维导图铁律）⑥ 无图时的视觉手段（色块/几何/巨字/图标） | 纯 HTML 输出（本管线要原生 PPTX） |

## 2. 管线流程

```
  输入文字（学术科普/计划/文献/论文）
        │
        ▼  Stage 1 · 提炼（LLM = Claude Code，出 JSON）
   ContentGraph：标题/章节/要点/表格/概念/结论
        │
        ▼  Stage 2 · 计划（LLM + page_type_budget 规则）
   DeckPlan：逐页布局（cover/section/content/table/mindmap/timeline/quote/takeaway）
        │
        ▼  Stage 3 · 生成（确定性代码，SVG 设计系统）
   每页 SVG（1280×720，设计 token 驱动，阴影/卡片/表格/思维导图几何）
        │
        ▼  Stage 4 · 转换（ppt-master svg_to_pptx 引擎）
   原生可编辑 .pptx（真实形状/表格/阴影）
        │
        ▼  Stage 5 · 质量门（svg_quality_checker + 几何校验）
   QA 报告；不合格 → 回 Stage 3 修正
        │
        ▼
   exports/<deck>.pptx + QA 报告
```

## 3. 目录结构

```
pptagent/
  __init__.py
  schema.py               # ContentGraph / DeckPlan 数据契约
  design/
    __init__.py
    tokens.py             # 设计 token：配色 / 字体 / 阴影 / 布局
  svggen.py               # 【核心】layout → SVG 页面生成器（12+ 种布局）
  plan.py                 # 学术分页预算（page_type_budget 适配）+ DeckPlan 校验
  styles.py               # ★ 风格库：种子存取 / 检索 / CLI
  run.py                  # 编排：DeckPlan → SVG → 质量门 → 转换 → 预览
  llm.py                  # ★ 可插拔 LLM 大脑（OpenAI 兼容 apikey 驱动）
  autopilot.py            # ★ 一键：材料 → 合格 PPTX（生成 + 闸门 + QA）
  honesty.py              # 内容诚实检查器（--honesty 闸门）
  svg_audit.py            # 程序化 SVG 审计（溢出 / 字号 / 配色纪律）
  pixel_qa.py             # 渲染像素级 QA（主题感知掩码）
  apply_template.py       # 套用用户模板主题（字体 / 主题色）
  charts.py               # 联网拉取开放版权参考图（失败回落占位）
  render_preview.py       # SVG → PNG 预览 + index.html
examples/                 # 可直接跑的示例（Transformer 科普 10 页）
styles/                   # ★ 风格库（打磨认可的种子）
docs/                     # 架构 / 诚实协议 / 评审循环 / 产品化蓝图 / 引擎说明 / 参考规范
exports/                  # 输出 .pptx + 预览（.gitignore）
```

## 4. 设计系统（"无 AI 生图但好看"的根基）

来自 guizang + 学术规范，编码进 tokens.py：

- **单一锚点色**：一个 deck 一个 accent（默认学术深蓝 #1B3A5C + 琥珀强调 #E9A13B）
- **灰阶层级**：ink / ink_2 / ink_3 三级文字色，不用 opacity 表达层级
- **发丝线**：0.5-1px 细分割线营造结构感
- **卡片四类互斥**：fill(灰底) / ink(深底反白) / accent(色底) / outlined(描边)
- **阴影克制**：card=md、node=sm（用户明确要阴影，但保持克制）
- **rowline 表格**：首列关键词 + 正文 + 等宽标签，隔行底纹，表头深蓝
- **思维导图**：SVG 只画几何（圆角节点 + 折线连接），文字用 <text> 且受检
- **无图视觉手段**：色块拼贴、几何点阵、巨字、Lucide 风格图标（几何符号）

## 5. LLM 大脑（可插拔）

渲染段（svggen/run/QA）**纯确定性，不依赖 LLM**；只有「用户材料 → deck_plan」这一步
需要大脑。`llm.py` 把它抽象成 **OpenAI 兼容客户端**：

- 默认 `base_url=https://api.deepseek.com/v1`，**必须提供 `api_key`**（缺 key 直接报错）。
- 可指向任意 OpenAI 兼容端点（DeepSeek / OpenAI / vLLM / 本地 llama.cpp）。
- `autopilot.py` 一键编排：材料 → LLM 生成 deck → schema 校验 → **honesty 闸门**
  （编造项自动回炉重生成，限 3 次）→ 确定性渲染 → 程序化 QA → 可选 LLM 三视角评审循环。
- 本地 LoRA 微调留作备选（见 LORA_PLAN.md）：风格库 few-shot 是现成的主路线，零训练、可回滚。

## 6. 验证记录

- 2026-08-04 引擎验证：SVG(1280×720, 阴影 filter) → `/tmp/test_slide.pptx`
  - 原生形状/文本框 ✅，outerShdw 阴影注入 ✅，质量门通过 ✅
- 转换命令（项目化）：
  ```bash
  # 1) 质量检查
  python3 skills/ppt-master/scripts/svg_quality_checker.py "<project>" --quick-generate --stage final --json
  # 2) 转换
  python3 skills/ppt-master/scripts/svg_to_pptx.py --quick-generate -o out.pptx -f ppt169 "<project>"
  ```
