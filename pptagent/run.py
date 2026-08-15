"""
fused-pipeline · 编排器
======================
DeckPlan → SVG 页面 → 质量门 → ppt-master 转换 → 原生 .pptx。

用法（见 SKILL.md）：
    python -m pptagent.run examples/demo_deck_plan.json --out exports/demo --engine ../ppt-master

engine 指向 ppt-master 仓库（含 skills/ppt-master/scripts）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .svggen import generate_deck
from .schema import validate_deck_plan


def _clean_stale_outputs(out: Path) -> None:
    """清掉上一次迭代留下的引擎产物，保证每次只保留一套 preview。

    只删 svg_output / preview / validation（每次都会重新生成），
    保留 images/（联网图表参考图，跨迭代复用）。
    """
    import shutil
    for d in ("svg_output", "preview", "validation"):
        p = out / d
        if p.exists():
            shutil.rmtree(p)
            print(f"  清理旧产物: {p}")


def _find_engine(engine: Optional[str]) -> Path:
    """定位 ppt-master 的 scripts 目录。

    优先用 `--engine` 显式指定；否则按约定在仓库旁找 `../ppt-master`。
    找不到时给出引导（SVG 生成不依赖引擎，但 PPTX 转换必需），
    见 docs/ENGINE.md。
    """
    if engine:
        p = Path(engine)
    else:
        p = Path(__file__).resolve().parents[2] / "ppt-master"
    scripts = p / "skills" / "ppt-master" / "scripts"
    if not (scripts / "svg_to_pptx.py").exists():
        raise FileNotFoundError(
            f"找不到 ppt-master 转换引擎: {scripts}\n"
            f"  PPTX 转换需要该引擎（SVG 生成本身不依赖）。\n"
            f"  安装：git clone https://github.com/hugohe3/ppt-master.git ../ppt-master\n"
            f"  或运行 --engine <ppt-master 仓库路径>。详见 docs/ENGINE.md。")
    return scripts


def generate(deck_plan: dict, out_dir: str, engine: Optional[str] = None,
             fmt: str = "ppt169", extra_flags: Optional[list] = None,
             template: Optional[str] = None, preview: bool = True,
             fetch_charts: bool = False, honesty_manifest: Optional[str] = None) -> dict:
    """完整执行：诚实校验 → 校验 → (联网图表参考) → 生成 SVG → 质量门 → 转换 → 套模板主题 → 预览。

    template: 用户模板 .pptx 路径，转换后把其主题嵌入成品。
    preview:  是否渲染 preview/slide_NN.png + index.html。
    fetch_charts: 是否对 deck 里标了 want_chart 的图槽联网拉取开放版权参考图
                  （失败自动回落为"图留空"占位，不中断）。
    honesty_manifest: sources_manifest.json 路径。给定则先跑内容诚实检查
                      （docs/HONESTY_PROTOCOL.md），有编造项即阻断生成。

    返回报告 dict（含路径、QA、状态）。
    """
    # 0) 内容诚实检查（可选，前置闸门）
    if honesty_manifest:
        from .honesty import check as honesty_check
        import json as _json
        manifest = _json.loads(Path(honesty_manifest).read_text(encoding="utf-8"))
        viol = honesty_check(deck_plan, manifest)
        if viol:
            raise ValueError(
                "内容诚实检查未通过（docs/HONESTY_PROTOCOL.md）:\n"
                + "\n".join(f"  ❌ {v['path']} = {v['value']!r} —— {v['why']}\n"
                            f"     修法: {v['fix']}" for v in viol))

    errors = validate_deck_plan(deck_plan)
    if errors:
        raise ValueError("DeckPlan 校验失败:\n" + "\n".join(errors[:20]))

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _clean_stale_outputs(out)
    scripts = _find_engine(engine)

    # 0) 联网图表参考（可选，按需）
    chart_report = None
    if fetch_charts:
        from .charts import fetch_chart_references
        chart_report = fetch_chart_references(deck_plan, str(out), scripts)
        print(f"[0/5] 联网图表参考: {chart_report}")

    # 1) 生成 SVG 页面
    paths = generate_deck(deck_plan, str(out))
    print(f"[1/3] 生成 SVG: {len(paths)} 页 -> {out}/svg_output/")

    # 2) 质量门
    qa_cmd = [sys.executable, str(scripts / "svg_quality_checker.py"),
              str(out), "--quick-generate", "--stage", "final", "--json"]
    qa = subprocess.run(qa_cmd, capture_output=True, text=True)
    qa_passed = "With errors: 0" in qa.stdout
    print(f"[2/3] 质量门: {'通过' if qa_passed else '未通过'}")
    if not qa_passed:
        print(qa.stdout[-2000:])
        print(qa.stderr[-1000:])

    # 3) 转换
    pptx_name = f"{deck_plan.get('deck_title', 'deck')}.pptx"
    pptx_path = out / pptx_name
    conv_cmd = [sys.executable, str(scripts / "svg_to_pptx.py"),
                "--quick-generate",
                "--native-charts-and-tables",
                "-o", str(pptx_path), "-f", fmt, str(out)]
    conv = subprocess.run(conv_cmd, capture_output=True, text=True)
    ok = pptx_path.exists() and pptx_path.stat().st_size > 0
    print(f"[3/4] 转换: {'成功' if ok else '失败'} -> {pptx_path}")
    if not ok:
        print(conv.stdout[-1500:])
        print(conv.stderr[-1000:])

    # 4) 套用用户模板主题（可选）
    if ok and template and Path(template).exists():
        from .apply_template import apply_template, theme_accent1
        apply_template(str(pptx_path), template)
        accent = theme_accent1(template) or "?"
        print(f"[4/4] 套用模板主题: {Path(template).name} -> theme accent1=#{accent} "
              f"（字体/主题身份来自模板；版面配色仍由设计 token 控制）")

    # 5) 预览渲染（可选）
    preview_dir = None
    if ok and preview:
        from .render_preview import render_preview
        prev = render_preview(str(out), deck_plan.get("deck_title", "学术 PPT"))
        preview_dir = prev["preview_dir"]
        print(f"[5/5] 预览: {prev['rendered']} 页 -> {preview_dir}/index.html")

    return {
        "status": "ok" if ok else "failed",
        "pptx": str(pptx_path) if ok else None,
        "preview": preview_dir,
        "svg_count": len(paths),
        "qa_stdout": qa.stdout[-800:],
        "converter_stdout": conv.stdout[-800:],
        "out_dir": str(out),
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    import argparse
    ap = argparse.ArgumentParser(description="学术 PPT 融合管线")
    ap.add_argument("deck_plan", help="DeckPlan JSON 路径")
    ap.add_argument("--out", default="exports/demo", help="输出目录")
    ap.add_argument("--engine", default=None, help="ppt-master 仓库路径")
    ap.add_argument("--template", default=None, help="用户模板 .pptx，转换后套用其主题")
    ap.add_argument("--no-preview", action="store_true", help="跳过预览渲染")
    ap.add_argument("--fetch-charts", action="store_true",
                    help="联网拉取 want_chart 图槽的开放版权参考图（失败回落图留空占位）")
    ap.add_argument("--honesty", default=None, metavar="SOURCES_MANIFEST.json",
                    help="内容诚实检查（docs/HONESTY_PROTOCOL.md）：manifest 有编造项则阻断生成")
    ap.add_argument("-f", "--format", default="ppt169", choices=["ppt169", "ppt43"])
    args = ap.parse_args(argv)

    plan = json.loads(Path(args.deck_plan).read_text(encoding="utf-8"))
    report = generate(plan, args.out, args.engine, args.format,
                      template=args.template, preview=not args.no_preview,
                      fetch_charts=args.fetch_charts,
                      honesty_manifest=args.honesty)
    print("\n=== 报告 ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
