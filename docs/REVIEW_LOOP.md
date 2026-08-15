# 评审循环（基金评审专家 × 博士生导师 + 内容诚实）

> 用户要求：反复用一个「基金评审专家 / 博士生导师」agent 审视产出，直至预览
> 对标网上高水平国自然报奖 PPT。本文档固定评审流程与检查单，供每一轮引用。
> v0.6.1 起增加第三视角：**内容诚实**（用户："不能编东西，内容只能来自用户上传的
> 模板/文档/交互信息；联网仅补用户未覆盖的客观事实且必须为真"）。

## 评审输入（证据包）
每轮给评审 agent 提供：
1. `examples/demo/deck_plan.json` —— 内容与布局意图
2. `exports/demo/svg_output/slide_NN.svg` —— 实际生成的矢量结构
3. `python -m pptagent.pixel_qa exports/demo examples/demo/deck_plan.json` 输出
4. `exports/demo/validation/svg_quality_report.json` —— 质量门报告
5. `python -m pptagent.svg_audit exports/demo/svg_output` —— SVG 结构程序化审计
6. `python -m pptagent.honesty examples/demo/deck_plan.json examples/demo/sources_manifest.json`
   —— 内容诚实检查（按 sources_manifest 逐条核对，见 docs/HONESTY_PROTOCOL.md）
7. 设计参考规范（见下）

## 诚实 Protocol 速览（详见 docs/HONESTY_PROTOCOL.md）
- 内容来源 L1 模板 > L2 用户文档 > L3 交互 > L4 联网（需 verified + 来源）
- 无来源的数字/头衔/单位/引用 = 编造，必须 ×× /（待填）占位
- 联网只补用户未覆盖的客观事实，且必须带可核实来源
- 参考材料（kimiPPT/报奖PPT）只做设计规范，绝不进内容

## 三视角检查单

### A. 基金评审专家视角（对标国自然面上/优青/杰青）
- [ ] 每页是否只有一个核心结论，用图表说话（而非文字堆砌）
- [ ] 技术路线/研究方案是否"科学问题→方案→预期成果"分层闭环
- [ ] 数据是否有表格/图表承载、关键数字是否高亮
- [ ] 配色是否克制（整体 ≤4 色）、深浅层级清晰
- [ ] 图槽是否为"内容图留空占位"（用户自己贴真图）

### B. 博士生导师视角（版式与可读性）
- [ ] 单页信息完整：图 + 表 + 文字三件套，而非纯文字框
- [ ] 阴影是否真实生效、层级是否分明（卡片/图槽/流程节点）
- [ ] 有无溢出画布、重叠、文字被截断
- [ ] 页头 kicker chip + 金色下划线 + 深蓝章节页等升级是否到位
- [ ] 数字/单位/表格对齐是否专业

### C. 内容诚实视角（对标 docs/HONESTY_PROTOCOL.md）
- [ ] 每个数字/头衔/单位/引用都能在 sources_manifest 找到出处，或为 ×× /（待填）
- [ ] 联网补充项是否带可核实来源且 verified=true（不能是推测）
- [ ] 参考材料有没有被当成内容塞进 PPT（设计规范≠内容来源）
- [ ] 用户给的（L1-L3）内容是否饱满呈现，占位只用于用户确实没给的字段

## 流程
```
确认 sources_manifest（用户给了什么）
   │
   ▼  内容诚实检查（python -m pptagent.honesty …）→ 有编造即阻断
生成 SVG + PPTX + 预览
   │
   ▼  运行 pixel_qa + 质量门 + svg_audit（程序化证据）
评审 agent（三视角）→ 输出：逐页判定 + 分数 + 可执行修复清单（含函数/坐标）
   │
   ▼  修复 → 再生成
再评审（≥3 轮）直至全部通过
   │
   ▼
存风格种子 + 更新 README
```

## 评审输出格式（agent 遵循）
```
## 逐页判定
slide_NN [layout] ✅/⚠️/❌ —— 一句话
## 修复清单（按优先级 P0/P1/P2）
- [P0] 具体问题 → 具体修法（函数/坐标/颜色）
## 达标项（相对上一轮改善）
```

## 参考规范（联网研究归纳）
- 报奖 PPT：深蓝主色 + 金/橙强调 + 白/浅灰底，≤4 色；模块化卡片；技术路线分层递进；
  面上 3-5 张图；参数用分类表。
- 顶刊图表色板：Nature `#0077BB #EE7733 #009988 #CC3311 #33BBEE`；
  Science/Okabe-Ito `#E69F00 #56B4E9 #009E73 #0072B2 #D55E00`。
- 本管线 tsinghua 主题：主蓝 #4472C4 / 深蓝 #2E5598 / 橙 #ED7D31 / 白底。

## 评审记录（round 2，2026-08-10）

> 内容为**示例 deck**（虚构数据）的评审回写，说明该流程如何把「内容诚实 + 版式」问题
> 闭环；具体数值仅为演示，不代表任何真实数据。

三视角并行复评 → 基金专家 **92/100**、博导 **90/100**、诚实+PowerPoint 功能 **76/100**。
诚实 agent 的 P0 是本轮重点：**编造引用 [13]**（`uploaded_docs=[]`、引用文献在 placeholders、deck 无参考文献表，
[13] 无真实出处）与**数据来源自相矛盾**（confirmed_facts 标 L2 实验数据，slide_10 却写「数据源 [13]（某体系）」）。

已修复（本轮）：
- **[P0] 移除编造引用 [13]**：caption/脚注/结论全部去 [13]；脚注改为「数据由用户提供、数值待自测复核」。
- **[P0] 数据来源矛盾**：sources_manifest 相关字段改为 `L3 交互（用户提供，数值待自测复核）`，
  并补入 `±SD` 与目标值两条 confirmed_facts。honesty 检查 0 issues。
- **[P1] 未验证表述**：「长期不降解」→「长期不降解（待验证）」；「FDA」tag 去营销化；量级表述加限定语。
- **[P2] 版式**：intro 单行（去孤字）+ 表底与面板留 8px；结论卡高度自适应填充；目录行自适应填满；
  判据行「双达标」徽章（`dual: true`）+ A/B 表头对齐。
- **[P1] preview ≠ PPTX 误差棒**：native chart 注入 `c:errBars`（`errValType=cust` + workbook 追加「±SD」列）。
  验证：chart1001.xml 含 `<c:errBars>`，`dPt` 与 workbook SD 列与源数据一致。

验证全绿：pixel_qa 全页通过、svg_audit 0 问题、质量门 0 error、honesty 0 issues、errBars 注入落盘。
