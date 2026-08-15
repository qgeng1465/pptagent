# LoRA 微调方案 · 学术 PPT 风格

> ## ⚠️ 状态：已改向（2026-08-04）
> 用户决定：**不训练本地 7B，走"联网跑 + 风格库 few-shot"**（见 `pptagent/styles.py`
> 与 SKILL.md Step 2/Step 4）。DeepSeek 官方无公开托管微调端点，权重微调不可行；
> 而认可过的 DeckPlan 存成种子、规划前 pick 参考，零训练即可获得稳定风格，且可回滚。
> **本文件保留为"若未来要离线独立运行"的备选方案**（届时再启用）。

> 目标：把管线里的 LLM（提炼 + 规划）微调成"你最喜欢的学术 PPT 风格"。
> 硬件：Tesla V100 32GB（QLoRA 可跑 7-9B）。
> 状态：**备选方案，暂不执行。**

## 0. 关键前提（诚实结论）

调研发现：**PPTAgent 的 DeepPresenter-9B 训练代码未开源**（只有 GGUF 推理部署），
所以"微调 PPTAgent 的模型"这条路走不通。我们改为微调**通用基座**，让它学
**我们管线的 DeckPlan 风格偏好**，而不是生成图片。这更可控，也更便宜。

## 1. 微调什么

管线里"人味"来自两个 LLM 环节（见 SKILL.md Step 1/2）：
- **提炼**：文字 → ContentGraph（忠于原文、结构化、数据入表）
- **规划**：ContentGraph → DeckPlan（学术分页预算、布局选择、文案密度）

LoRA 的目标就是让模型在这两步产出**符合你风格**的 JSON。

| 微调目标 | 学什么 | 数据形态 | 优先级 |
|---|---|---|---|
| **规划风格** | 布局偏好（何时用 table/mindmap/timeline）、章节节奏、密度 | (ContentGraph → DeckPlan) 对 | ★★★ 最先做 |
| **提炼/文案风格** | 标题的取法、金句的挖法、要点的凝练度 | (原文 → ContentGraph) 对 | ★★ |
| **表格表达** | 数据怎么排进表格、高亮哪一行 | (原文数据 → table 内容) 对 | ★★ |

## 2. 基座选择

| 基座 | 参数量 | QLoRA@V100-32G | 理由 |
|---|---|---|---|
| **Qwen2.5-7B-Instruct** | 7B | ✅ 4bit + LoRA r=16 | 中文强、支持长文、生态成熟、好量化 |
| Qwen3-8B | 8B | ✅ | 更强但量化后仍可行 |
| DeepSeek-R1-Distill-Qwen-7B | 7B | ✅ | 推理强，适合"规划"类思考 |

推荐首发 **Qwen2.5-7B-Instruct**：中文指令遵循好，直接可跑。

## 3. 数据集（关键难点，需用户参与）

LoRA 学的是"你喜欢的样子"，所以**必须用你认可的样例**。方案：

**Step A 收集风格样本（人工，1-2 天）**
1. 用当前管线跑 10-15 个你真实场景的 deck（学术科普/计划/文献）。
2. 你在这些 deck 上人工打磨/点评：哪页好、哪页该换布局、标题该怎么说。
3. 把"修改后的 DeckPlan"作为 ground truth 存下来。

**Step B 构造训练对（脚本生成）**
- 规划风格：`(ContentGraph, 打磨后 DeckPlan)` → 全量监督微调对
- 文案风格：`(原文片段, 打磨后 ContentGraph)` 
- 扩充：用 LLM 按你的点评规则改写更多样本（自我对弈扩充），加过滤。

**Step C 质检**：随机 20% 作测试集。

规模建议：300-1000 对足够做规划风格 LoRA（不需要大）。

## 4. 训练

```bash
# 环境
pip install torch transformers peft bitsandbytes datasets trl

# QLoRA 配置（V100-32G）
#  - quantization: 4bit bnb, compute_dtype=float16
#  - LoRA: r=16, alpha=32, target=[q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj]
#  - lr=2e-4, 3 epochs, 截断 4096 token

# 用 trl.SFTTrainer 或标准 Trainer
# 输出: adapter 权重（~几十 MB），不需全量存基座
```

工具链：`transformers + peft + bitsandbytes`，全部 pip 可用（清华镜像）。
V100-32G 跑 Qwen-7B 4bit LoRA：约 6-10 小时 / 1000 对样本。

## 5. 部署与接入

```bash
# vLLM 起 OpenAI 兼容服务（加载 LoRA adapter）
vllm serve Qwen/Qwen2.5-7B-Instruct --enable-lora --lora-modules pptstyle=./adapter
# 或 llama.cpp（GGUF + adapter 合并后量化）
```

管线接入点：`pptagent/llm/adapter.py`（当前占位 = Claude Code 手写 JSON）。
实现 `generate_json(prompt, schema) -> dict`，指向本地 vLLM 即可完成替换。
**Claude Code 模式与本地 LoRA 模式可并存**，用配置切换。

## 6. 评估（客观，不靠感觉）

- **结构分**：DeckPlan 的 layout 分布是否符合 academic_budget（25% 数据页等）
- **表格分**：数据是否进表、列数/行数是否超限（plan.check_deck_structure）
- **风格一致性**：同题重复 3 次，deck 结构相似度（Jaccard of layout sequence）
- **最终目检**：渲染预览，人工打分 1-5（和基线 Claude Code 版对比）

## 7. 里程碑

1. [ ] 用户提供 3-5 个真实风格样例（或认可某个 demo 作为目标风格）
2. [ ] 生成 300-1000 对训练数据（脚本 + 人工打磨）
3. [ ] QLoRA 训练 Qwen2.5-7B（~8h），产出 adapter
4. [ ] vLLM 接入 adapter.py，端到端替换 Claude Code 规划
5. [ ] 客观评估 + 用户盲测（LoRA vs Claude Code 基线）

## 8. 风险与退路

- 样本不够 → 先只做"规划风格"单一目标，300 对起步。
- 风格漂移 → 加系统提示约束 + 评估集回归。
- V100 太旧 → 降低到 Qwen-3B，或用户换卡后升 8B。
- 用户其实只想要"固定的几套模板"而非模型微调 → 那改 tokens.py + DeckPlan
  模板即可，不必真训练。**先确认这一点再决定是否投入训练。**
