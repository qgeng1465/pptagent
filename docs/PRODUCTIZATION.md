# 产品化蓝图（fused-pipeline → 可发布工具）

> 用户方向（2026-08-10）：① 再审几轮把 PPT 做好看；② **完美对接 Microsoft PowerPoint 原生功能，不丢功能**；
> ③ **发布 GitHub**——输入一个 API key 就能自动调用、形成用户自己的工作流；
> ④ **用 apikey 驱动主流模型**（不用自建 ollama）；风格可 finetune（风格种子 few-shot + 可选 API 微调）。
>
> 本文档是目标架构规格，不承诺已实现。当前管线 = 确定性渲染引擎（DeckPlan→SVG→PPTX）已经完全可脚本化，
> 缺的是"**用户材料 → DeckPlan**"这一步的 LLM 大脑被硬编码在交互里（靠 Claude Code 当大脑）。

## 现状（已可脚本化）

```
用户内容/模板 ──(手动/交互：LLM 大脑)──▶ deck_plan.json ──▶ python -m pptagent.run ... --honesty manifest
                                                                    │
                                              确定性 SVG 渲染 + 质量门 + 原生 PPTX + 预览
```

- 渲染段（svggen/run/schema/honesty/pixel_qa/svg_audit）**纯确定性**，无 LLM 依赖，开箱即用。
- 唯一缺的可编程环节：**从用户上传的模板 + 文档 + 交互信息生成 deck_plan.json**。

## 目标架构：LLM 大脑可插拔（apikey 驱动，OpenAI 兼容）

新增 `pptagent/llm.py`（OpenAI 兼容 chat/completions 客户端）：

```
LLMClient(base_url, api_key, model)
├─ generate_deck_plan(materials, template_analysis, style, honesty_manifest) -> deck_plan
│    协议：L1 模板 > L2 用户文档 > L3 交互 > L4 联网(仅补缺口, 需 verified+来源)
│    无来源 → ××/（待填）占位（与 pptagent.honesty 校验同源）
└─ review_deck(deck, evidence_pack) -> {scores, fixlist}   # 复用 REVIEW_LOOP.md 三视角检查单
```

- 默认 `base_url=https://api.deepseek.com/v1`，**必须提供 `api_key`**（缺 key 直接报错）→ apikey 驱动主流模型。
- 也可指向任何 OpenAI 兼容端点（OpenAI / vLLM / Claude 兼容网关）——输入一个 API key 即形成自己的工作流。
- `model` 可配：`deepseek-chat` / `gpt-4o-mini` / `deepseek-v3` 或 **API 微调后的自定义模型 id**。
  模型能力决定生成质量上限；确定性渲染段保证**再差的模型也不会产出烂版式**
  （形状/字号/配色/溢出由代码锁定）。

## 一键工作流（CLI / API）

```
python -m pptagent.autopilot examples/demo/材料/ \
    --template 组会模板.pptx \
    --api-key <key> --base-url http://localhost:11434/v1 --model llama3 \
    --style research-plan-13p --out exports/autopilot
```

autopilot 编排（复用现有模块）：
1. 解析用户材料（模板字体/主题色 + 文档正文）→ 生成 `sources_manifest.json`（诚实基座）
2. `llm.generate_deck_plan()` → schema 校验 → **honesty 闸门**（不通过自动回炉重生成）
3. `run.generate()`（SVG + 质量门 + 原生 PPTX + 套模板主题 + 预览）
4. 程序化 QA（pixel_qa + svg_audit）
5. （可选）`llm.review_deck()` 评审循环 ≥2 轮，修复后重生成
6. 产出：.pptx + 预览 index.html + sources_manifest + 审计报告

## GitHub 发布形态

- `pyproject.toml`（pip install fused-ppt），依赖收敛到 ppt-master 引擎 + python-pptx 校验 + cairosvg 渲染。
- `.env` 配置 `FUSED_LLM_BASE_URL / FUSED_LLM_API_KEY / FUSED_LLM_MODEL`，或 `--api-key` 直接传。
- `--honesty` 默认开启（无 manifest 时按"严格：一切无来源视为占位"生成）。
- 文档：README（Quickstart 一键 PPT）、docs/HONESTY_PROTOCOL.md、docs/PRODUCTIZATION.md、docs/REVIEW_LOOP.md。
- 字体注意：输出 PPTX 用模板字体（等线），Windows/Office 自带；跨平台 fallback 靠 fontconfig（见 v0.6.1 记录）。

## 风格 finetune（两条路，均 apikey 可达）

用户方向："风格可以 finetune 一下"。两条路，工程内已有一条现成：

1. **风格种子 few-shot（现成，推荐）**：`pptagent/styles.py` 把打磨认可的 deck 存成种子
   （`styles/<name>.json`），autopilot 生成前自动 `pick_seed` 检索最匹配种子注入 prompt 作参考——
   不训练、可回滚、越用越贴你的审美。`styles/research-plan-demo.json` 已沉淀 v0.5–v0.6.2 全部版式经验
   （血红组会主题/禁空区/原生图表+误差棒/双达标徽章）。
2. **API 微调模型（可选）**：主流 OpenAI 兼容服务（DeepSeek/OpenAI）支持 fine-tune API。
   把真实记录"用户材料 → 合格 deck_plan → 评审修复对"整理成 `{instruction, output_deck_plan}`
   数据集（含诚实占位与版式修复），调成自家风格模型后 `--model` 填微调后模型 id，
   **工作流完全不变**——`LLMClient` 只是换一个 model 参数。

## 里程碑

- [x] M1 渲染段 + 诚实闸门 + 程序化 QA 全绿（**13 页 0 问题**）
- [x] M2 `pptagent/llm.py` OpenAI 兼容客户端 + `autopilot` CLI（apikey 驱动）——
      **骨架已落地**（2026-08-10）：`LLMClient.chat` / `generate_deck_plan`（schema+诚实回炉）/
      `review_deck`（三视角 fixlist）/ `python -m pptagent.llm generate|review` /
      `python -m pptagent.autopilot`。实测：mock LLM 走「坏 layout→编造[13]→干净 deck」回炉 3 次成功；
      缺 api_key 直接友好报错。**尚未**用真实 API key 端到端跑通（等用户 key）。
- [ ] M3 评审循环自动化（`review_deck` 生成可执行 fixlist → **自动应用到 deck_plan 并重建**；
      当前 autopilot 只输出 fixlist，应用仍靠人/Claude）
- [ ] M4 打包发布（pyproject + README + GitHub Actions 样例 + 示例 deck）
- [ ] M5 风格 finetune：风格种子持续沉淀（每次认可 deck `styles add`）+ 可选 API 微调模型接入 + 数据集整理

### M2 落地记录（2026-08-10）
- `pptagent/llm.py`：OpenAI 兼容 `/chat/completions` 客户端（requests，无新重依赖）；
  `generate_deck_plan` 带 **schema 校验 + honesty 闸门回炉**（≤3 次，错误回喂 LLM）；
  `_parse_json_block` 容忍 ```json 围栏；诚实协议/布局契约写进 system prompt。
- `pptagent/autopilot.py`：材料目录/JSON → 诚实基座骨架（L1/L2 确定项，其余占位）
  → LLM 生成 → `run.generate`（--template/--honesty）→ pixel_qa + svg_audit → 可选评审轮。
- `pptagent/honesty.py` **补两类漏检**（二轮评审 P0 暴露）：
  ①文本里 `[N]` 文献引用无出处 → 编造；②值含个人成果关键词+数字（如 "12 篇一作"）无来源 → 编造。
  为排除误报：stat `label` 字段豁免（"近5年一作/共同一作论文" 是描述非成果）；预期成果类
  （"论文 1-2 篇 + 方法专利"）如实登记进 manifest confirmed_facts（L2 研究计划，非已实现）。
