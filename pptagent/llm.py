"""可插拔 LLM 大脑（apikey 驱动，OpenAI 兼容端点）。

产品化方向（docs/PRODUCTIZATION.md）：
- 当前管线「用户材料 → deck_plan.json」靠 Claude Code 当大脑（人机交互）。
- 本模块把这一步抽象成 apikey 驱动的后端：`LLMClient(base_url, api_key, model)`
  走 OpenAI 兼容 `/chat/completions`，**输入一个 API key 即可自动调用主流模型**
  （DeepSeek/OpenAI/vLLM/网关均兼容）。默认 DeepSeek 端点，可经
  `--base-url/--api-key/--model` 或环境变量
  `FUSED_LLM_BASE_URL/FUSED_LLM_API_KEY/FUSED_LLM_MODEL` 覆盖。
- 输出经 `schema.validate_deck_plan` 校验 + `honesty.check` 诚实闸门，
  不合格自动回炉重生成（限次）。确定性渲染段（svggen/run/QA）不依赖 LLM。

用法：
    python -m pptagent.llm generate materials.json --out deck_plan.json \
        --api-key sk-你的key --base-url https://api.deepseek.com/v1 --model deepseek-chat
    python -m pptagent.llm review deck_plan.json --evidence evidence.txt \
        --manifest sources_manifest.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Optional

import requests

from .schema import validate_deck_plan


# 默认 DeepSeek（OpenAI 兼容、可 API 微调）；任意 OpenAI 兼容端点均可经参数覆盖
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"

# 诚实协议：内容来源优先级 L1 模板 > L2 用户文档 > L3 交互 > L4 联网(需 verified+来源)。
# 无来源的数字/头衔/单位/引用 = 编造，必须用 ×× 或（待填）占位。
HONESTY_RULES = """
## 诚实协议（不可违反，比美观更重要）
1. 内容只来自材料里的 L1 模板 / L2 用户文档 / L3 交互信息；联网补充（L4）只用于
   用户没覆盖的客观事实，且必须给出可核实来源，并在 deck 里标注「（来源待补）」。
2. 材料里没有的数字/头衔/单位/引用 → 一律用 ×× 或（待填）占位，绝不编造。
3. 参考文献 [n] 只能引用材料里真实出现的出处，否则不写引用标注。
4. 版式/审美参考（报奖 PPT、kimiPPT 等）只能影响布局与配色，绝不进入内容。
"""

DECK_PROMPT = f"""
你是资深科研汇报 PPT 设计师。根据下面的用户材料，产出一份完整的 deck_plan
（JSON，遵循 schema）。要求：每页一个核心结论、图文表结合、配色克制 ≤4 色、
内容饱满不空洞、单页装得下（要点 ≤4 条，正文句 ≤2 行）。

{HONESTY_RULES}

可用的 layout（每页选一个，content 字段按该布局契约填）：
cover section overview content steps table flow flow_table chart_table hex
print3 refs timeline quote takeaway two_col compare mindmap fig_table_text
fig_points research route profile

通用约束：
- 内容图一律留空占位（figures[].slot + label「图 N」+ caption），不编造配图。
- 数据页优先 chart_table（data 行 = [标签, 数值, 标注(可选), SD(可选)]）或 table。
- 单位必须出现；趋势/结论用结论条（takeaway.closing / route.closing）收口。
- 封面 meta 里姓名/日期可写，单位/学位没有材料就写（待填）。

只输出一个 JSON 对象（deck_plan），不要多余文字、不要代码围栏。
结构：{{"slide_size":"16:9","deck_title":..., "theme":"academic|science-pop",
"footer":..., "slides":[{{"id":"slide_01","layout":...,"title":...,
"kicker":...,"content":{{...}}}}]}}
"""


class LLMError(RuntimeError):
    pass


class LLMClient:
    """OpenAI 兼容 chat/completions 客户端（apikey 驱动）。

    base_url / api_key / model 必须能访问你的 API 提供商；api_key 为空直接报错，
    避免带上空 Bearer 发出无效请求。
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL,
                 api_key: str = "", model: str = DEFAULT_MODEL,
                 timeout: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        if not api_key:
            raise LLMError(
                "缺少 API key：请传 --api-key（或设环境变量 FUSED_LLM_API_KEY），"
                "例如 --api-key sk-你的key。")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def chat(self, messages: list[dict], temperature: float = 0.4,
             max_tokens: int = 8192) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        try:
            resp = requests.post(url, json=payload, headers=headers,
                                 timeout=self.timeout)
        except requests.ConnectionError as e:
            raise LLMError(
                f"无法连接 LLM 后端 {url}（{e}）。请确认网络可达该 API 端点，"
                "并核对 --base-url / FUSED_LLM_BASE_URL、--api-key / FUSED_LLM_API_KEY。"
            ) from e
        if resp.status_code != 200:
            raise LLMError(f"LLM 端点返回 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"响应格式异常（无 choices[0].message.content）：{str(data)[:300]}") from e


def _parse_json_block(text: str) -> dict:
    """从 LLM 文本里提取 JSON（容忍 ```json 围栏 / 前后杂文）。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        # 直接取第一个 { ... } 平衡块
        start = text.find("{")
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    text = text[start:i + 1]
                    break
    return json.loads(text)


def generate_deck_plan(client: LLMClient, materials: dict,
                       style: Optional[dict] = None,
                       manifest: Optional[dict] = None,
                       max_retries: int = 3) -> dict:
    """材料 → schema 合法 + 诚实通过 的 deck_plan。

    materials: {template:{...}, documents:[str...], interaction:[str...],
                budget:{...}}  —— 用户给的原始内容。
    manifest: sources_manifest.json dict；给定则追加诚实闸门（不通过回炉）。
    """
    from .honesty import check as honesty_check

    style_block = ""
    if style:
        style_block = "\n风格种子参考（布局/配色样例）：\n" + json.dumps(
            style, ensure_ascii=False, indent=1)

    sys_prompt = DECK_PROMPT + style_block
    user_payload = json.dumps(materials, ensure_ascii=False, indent=1)

    messages: list[dict] = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_payload},
    ]

    last_err = "LLM 多次输出无效 deck_plan"
    for attempt in range(1, max_retries + 1):
        reply = client.chat(messages)
        try:
            deck = _parse_json_block(reply)
        except (json.JSONDecodeError, ValueError) as e:
            last_err = f"JSON 解析失败: {e}"
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user",
                             "content": f"输出不是合法 JSON，请只输出 deck_plan JSON 本身（{last_err}）。"})
            continue

        errors = validate_deck_plan(deck)
        if errors:
            last_err = "schema 校验未过"
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user",
                             "content": "schema 校验错误如下，请修复后重出完整 deck_plan：\n"
                                        + "\n".join(errors[:20])})
            continue

        if manifest:
            viol = honesty_check(deck, manifest)
            if viol:
                last_err = "诚实检查未过"
                fixes = "\n".join(f"  ❌ {v['path']} = {v['value']!r} —— {v['why']}\n     修法: {v['fix']}"
                                  for v in viol)
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user",
                                 "content": "诚实检查未通过，请按下列问题修复（改用占位/删编造/对齐来源）后重出完整 deck_plan：\n"
                                            + fixes})
                continue

        return deck

    raise LLMError(f"{last_err}（重试 {max_retries} 次仍失败）")


def review_deck(client: LLMClient, deck: dict, evidence: str = "",
                manifest: Optional[dict] = None) -> dict:
    """三视角评审：返回 {scores, verdicts, fixlist:{P0:[...],P1:[...],P2:[...]}}。"""
    from .honesty import check as honesty_check

    honesty_block = ""
    if manifest:
        viol = honesty_check(deck, manifest)
        honesty_block = ("\n诚实检查现状：" +
                         ("通过" if not viol else "\n".join(
                             f"  ❌ {v['path']}: {v['why']}" for v in viol)))

    prompt = f"""
你是「基金评审专家 × 博士生导师 + 内容诚实」三视角评审。
审视这份 deck_plan，输出 JSON：
{{"scores": {{"fund": 0-100, "mentor": 0-100, "honesty": 0-100}},
 "verdicts": {{"slide_id": "✅/⚠️/❌ 一句话"}},
 "fixlist": {{"P0": ["...", ...], "P1": [...], "P2": [...]}}}}
P0=编造/事实错误/不可交付；P1=明显影响观感或阅读；P2=打磨项。
给每项修复指明具体字段路径与修法。只输出 JSON。

评审输入——deck_plan 与程序化证据：{evidence[:6000]}
{honesty_block}
""".strip()

    reply = client.chat([
        {"role": "system", "content": "你是严格的科研 PPT 评审。"},
        {"role": "user", "content": prompt + "\n\n" + json.dumps(deck, ensure_ascii=False)},
    ], temperature=0.1)
    try:
        return _parse_json_block(reply)
    except (json.JSONDecodeError, ValueError):
        return {"scores": {}, "verdicts": {}, "fixlist": {}, "_raw": reply}


def _env_or_arg(args: argparse.Namespace, key: str, default: str) -> str:
    import os
    env = {"base_url": "FUSED_LLM_BASE_URL", "api_key": "FUSED_LLM_API_KEY",
           "model": "FUSED_LLM_MODEL"}
    return getattr(args, key) or os.environ.get(env[key], default)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pptagent.llm",
                                 description="可插拔 LLM 大脑（apikey 驱动，OpenAI 兼容）")
    ap.add_argument("cmd", choices=["generate", "review"])
    ap.add_argument("input", help="generate: materials.json | review: deck_plan.json")
    ap.add_argument("--out", default=None, help="输出 deck_plan.json 路径")
    ap.add_argument("--evidence", default="", help="review 用的程序化证据文本")
    ap.add_argument("--manifest", default=None, help="sources_manifest.json（诚实闸门）")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--style", default=None, help="风格种子 json（可选）")
    ap.add_argument("--max-retries", type=int, default=3)
    args = ap.parse_args(argv)

    client = LLMClient(
        base_url=_env_or_arg(args, "base_url", DEFAULT_BASE_URL),
        api_key=_env_or_arg(args, "api_key", ""),
        model=_env_or_arg(args, "model", DEFAULT_MODEL),
    )

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8")) if args.manifest else None

    if args.cmd == "generate":
        materials = json.loads(Path(args.input).read_text(encoding="utf-8"))
        style = json.loads(Path(args.style).read_text(encoding="utf-8")) if args.style else None
        deck = generate_deck_plan(client, materials, style=style, manifest=manifest,
                                  max_retries=args.max_retries)
        out = args.out or "deck_plan.json"
        Path(out).write_text(json.dumps(deck, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ deck_plan 生成并过校验/诚实闸门 -> {out}（{len(deck['slides'])} 页）")
        return 0

    # review
    deck = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = review_deck(client, deck, evidence=args.evidence, manifest=manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
