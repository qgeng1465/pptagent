"""autopilot CLI：用户材料 → 合格 PPTX 的一键工作流（产品化 M2/M3）。

流程（docs/PRODUCTIZATION.md）：
    材料目录 → 诚实基座(manifest 骨架) → LLM 生成 deck_plan
        → schema 校验 + honesty 闸门(不通过自动回炉)
        → run.generate(SVG + 质量门 + 原生 PPTX + 套模板主题 + 预览)
        → 程序化 QA(pixel_qa + svg_audit)
        →（可选）LLM 三视角评审 ≥N 轮，按 fixlist 修复后重生成
    产出：.pptx + preview/index.html + sources_manifest + 审计报告

LLM 大脑 apikey 驱动（OpenAI 兼容端点）：
    --api-key sk-你的key --base-url https://api.deepseek.com/v1 --model deepseek-chat
环境变量 FUSED_LLM_BASE_URL / FUSED_LLM_API_KEY / FUSED_LLM_MODEL 亦可。
未指定 --style 时自动从 styles/ 风格库检索最匹配的已打磨种子作 few-shot 参考。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .llm import DEFAULT_BASE_URL, DEFAULT_MODEL, LLMClient, _env_or_arg, generate_deck_plan, review_deck
from .run import generate as run_generate

DOC_EXTS = {".txt", ".md", ".markdown", ".rst", ".json", ".csv"}


def _collect_materials(materials_arg: str) -> dict:
    """材料入参：目录 → 读文本文件；json 文件 → 直接加载；否则当作字符串。"""
    p = Path(materials_arg)
    if p.is_dir():
        docs = []
        for f in sorted(p.rglob("*")):
            if f.is_file() and f.suffix.lower() in DOC_EXTS:
                try:
                    docs.append(f"### 文件 {f.relative_to(p)}\n" + f.read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    pass
        return {"documents": docs, "directory": materials_arg}
    if p.suffix == ".json":
        return json.loads(p.read_text(encoding="utf-8"))
    return {"documents": [p.read_text(encoding="utf-8")], "source": materials_arg}


def _bootstrap_manifest(materials: dict, template: str | None) -> dict:
    """诚实基座骨架：只登记「确定来自模板/材料」的字段，其余一律占位待补。"""
    confirmed = []
    if template:
        confirmed.append({"field": "模板", "value": template,
                          "source": "L1 用户模板（用于主题/字体，非内容）"})
    if materials.get("directory"):
        confirmed.append({"field": "材料目录", "value": materials["directory"],
                          "source": "L2 用户文档"})
    return {
        "user": "（待用户确认）",
        "uploaded_template": template or "（未指定）",
        "uploaded_docs": [],
        "confirmed_facts": confirmed,
        "placeholders": ["单位", "姓名以外的头衔", "教育经历", "研究经历", "论文数",
                          "被引量", "专利数", "参与项目数", "引用文献"],
        "web_supplemented": [],
        "notes": ("autopilot 骨架：仅登记 L1/L2 确定项。任何 LLM 补的数字若不在 "
                  "confirmed_facts，用户须在重建前核对；联网补充必须 verified+来源。"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pptagent.autopilot",
                                 description="一键：材料 → 合格 PPTX")
    ap.add_argument("materials", help="材料目录 / materials.json / 文本文件")
    ap.add_argument("--out", default="exports/autopilot", help="输出目录")
    ap.add_argument("--template", default=None, help="用户模板 .pptx（套用主题）")
    ap.add_argument("--engine", default="../ppt-master", help="ppt-master 引擎路径")
    ap.add_argument("--style", default=None, help="风格种子 json（可选）")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--reviews", type=int, default=0, help="LLM 三视角评审轮数（0=跳过）")
    ap.add_argument("--manifest", default=None, help="指定 sources_manifest.json（否则生成骨架）")
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--skip-qa", action="store_true", help="跳过 pixel_qa/svg_audit")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # 1) 材料与诚实基座
    materials = _collect_materials(args.materials)
    manifest_path = Path(args.manifest) if args.manifest else out / "sources_manifest.json"
    if not manifest_path.exists():
        manifest_path.write_text(
            json.dumps(_bootstrap_manifest(materials, args.template),
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"ℹ️  已生成诚实基座 {manifest_path}（L1/L2 确定项；其余占位，请人工核对）")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # 2) LLM 生成 deck_plan（apikey 驱动，OpenAI 兼容）
    client = LLMClient(
        base_url=_env_or_arg(args, "base_url", DEFAULT_BASE_URL),
        api_key=_env_or_arg(args, "api_key", ""),
        model=_env_or_arg(args, "model", DEFAULT_MODEL),
    )
    print(f"🧠 LLM 后端: {client.base_url} / {client.model}")
    # 风格 finetune：显式 --style 优先；否则自动从风格库检索已打磨种子作 few-shot 参考
    style = None
    if args.style:
        style = json.loads(Path(args.style).read_text(encoding="utf-8"))
    else:
        try:
            from . import styles
            text = "\n".join(materials.get("documents", []))[:4000]
            picks = styles.pick_seed(text, k=1)
            if picks:
                style = picks[0]
                print(f"🎨 风格种子: {style.get('name')}"
                      f"{'（approved）' if style.get('approved') else ''} -> few-shot 参考")
        except Exception as e:
            print(f"ℹ️  风格库检索跳过（{e}）")
    deck = generate_deck_plan(client, materials, style=style, manifest=manifest)
    deck_path = out / "deck_plan.json"
    deck_path.write_text(json.dumps(deck, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ deck_plan 通过 schema + 诚实闸门 -> {deck_path}")

    # 3) 生成（套模板主题 + 诚实前置）
    report = run_generate(
        deck, str(out),
        engine=args.engine,
        template=args.template,
        preview=not args.no_preview,
        honesty_manifest=str(manifest_path),
    )
    print(f"📦 PPTX -> {report['pptx']}")

    # 4) 程序化 QA
    if not args.skip_qa:
        from . import pixel_qa, svg_audit
        pixel_qa.main(str(out), str(deck_path))
        svg_audit.main(str(out / "svg_output"))

    # 5) LLM 评审循环（可选）
    if args.reviews and args.reviews > 0:
        evidence_parts = []
        for line in _load_evidence(out):
            evidence_parts.append(line)
        evidence = "\n".join(evidence_parts)
        for rnd in range(1, args.reviews + 1):
            verdict = review_deck(client, deck, evidence=evidence, manifest=manifest)
            (out / f"review_round{rnd}.json").write_text(
                json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"🔍 评审 round {rnd}: {verdict.get('scores', {})}")
            if not verdict.get("fixlist"):
                break
        print(f"ℹ️  评审结果 -> {out}/review_round*.json（P0/P1/P2 修复清单请人工/后续自动化应用）")

    print(f"\n✅ 完成：{out}\n  PPTX: {report['pptx']}\n  预览: {out}/preview/index.html\n"
          f"  诚实基座: {manifest_path}\n  deck_plan: {deck_path}")
    return 0


def _load_evidence(out: Path) -> list[str]:
    """评审证据：质量门报告 + svg_audit + pixel_qa（若存在）。"""
    lines = []
    q = out / "validation" / "svg_quality_report.json"
    if q.exists():
        lines.append("svg_quality_report: " + json.dumps(
            json.loads(q.read_text(encoding="utf-8")), ensure_ascii=False)[:1500])
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
