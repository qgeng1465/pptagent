"""
fused-pipeline · 联网图表参考拉取
================================
给 DeckPlan 里标了 want_chart/query 的图槽从开放版权源（openverse/wikimedia/
pexels/pixabay，走 ppt-master 的 image_search.py）搜索并下载参考图，回填
figures[].src = "images/<file>"。失败/无网 → 保持空 src，svggen 自动回落为
"图留空"占位框，不中断管线。

用法：
    python -m pptagent.charts <deck_plan.json> --out exports/<deck>
    （out 需包含 svg_output/ 的上一级；图片写入 <out>/images/，SVG 用 ../images/ 引用；
      拉取成功会把 figures[].src 回填并写回 deck_plan.json）
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SEARCH_STATUS_SOURCED = "Sourced"
SEARCH_STATUS_PENDING = "Pending"


# ================================================================ 收集
def _slide_figures(slide: dict) -> List[dict]:
    """从任意 layout 的 slide 里取出 figures[] 列表。"""
    content = slide.get("content", {})
    if not isinstance(content, dict):
        return []
    figs = content.get("figures", [])
    return figs if isinstance(figs, list) else []


def collect_figure_queries(deck_plan: dict) -> List[dict]:
    """扫所有 slide 的 figures[]，收集需要联网的条目。
    返回 image_queries.json 的 items 列表（每条含 filename/query/status）。"""
    items: List[dict] = []
    seen = set()
    slides = deck_plan.get("slides", [])
    for i, slide in enumerate(slides):
        figs = _slide_figures(slide)
        for j, fig in enumerate(figs):
            if not isinstance(fig, dict):
                continue
            # 已有图（src/embed）就不再联网
            if fig.get("src") or fig.get("embed"):
                continue
            want = fig.get("want_chart")
            if not want:
                continue
            query = fig.get("query") or (want if isinstance(want, str) else "")
            if not query:
                continue
            filename = f"fig_{i + 1:02d}_{j}.jpg"
            if filename in seen:
                filename = f"fig_{i + 1:02d}_{j}_{len(seen)}.jpg"
            seen.add(filename)
            items.append({
                "filename": filename,
                "query": query,
                "status": SEARCH_STATUS_PENDING,
                "orientation": "landscape",
                "min_width": 800,
                "min_height": 600,
            })
    return items


# ================================================================ 拉取
def fetch_chart_references(deck_plan: dict, out_dir: str,
                           engine_scripts: Optional[Path] = None,
                           concurrency: int = 2) -> dict:
    """执行联网拉取并原地回填 deck_plan 里 figures[].src。
    engine_scripts: ppt-master 的 skills/ppt-master/scripts 目录。
    返回 {"fetched": n, "failed": n, "queries": m}。软失败：任何异常不抛，留占位。"""
    items = collect_figure_queries(deck_plan)
    if not items:
        return {"fetched": 0, "failed": 0, "queries": 0}

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    q_path = out / "image_queries.json"
    q_path.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2),
                      encoding="utf-8")

    # 定位 image_search.py
    scripts = None
    if engine_scripts is not None:
        p = Path(engine_scripts)
        cand = p / "image_search.py" if p.is_dir() else p
        if cand.exists():
            scripts = cand
    if scripts is None:
        # 默认 ../ppt-master/skills/ppt-master/scripts
        fallback = Path(__file__).resolve().parents[2] / "ppt-master" / "skills" / "ppt-master" / "scripts" / "image_search.py"
        if fallback.exists():
            scripts = fallback
    if scripts is None:
        print("[charts] 找不到 image_search.py，跳过联网拉取（保持图留空）")
        return {"fetched": 0, "failed": len(items), "queries": len(items)}

    cmd = [sys.executable, str(scripts),
           "--batch", str(q_path),
           "-o", str(out / "images"),
           "--concurrency", str(concurrency)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        if res.stdout:
            print(res.stdout[-1200:])
        if res.returncode != 0 and res.stderr:
            print("[charts] image_search stderr:", res.stderr[-600:])
    except Exception as exc:  # 无网/超时等 → 软失败
        print(f"[charts] 联网拉取失败（{exc}），保持图留空占位")
        return {"fetched": 0, "failed": len(items), "queries": len(items)}

    # 读回状态，回填 src
    fetched = failed = 0
    try:
        updated = json.loads(q_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"fetched": 0, "failed": len(items), "queries": len(items)}

    status_by_name = {
        it["filename"]: it.get("status")
        for it in updated.get("items", [])
        if isinstance(it.get("filename"), str)
    }
    slides = deck_plan.get("slides", [])
    for i, slide in enumerate(slides):
        for j, fig in enumerate(_slide_figures(slide)):
            if not isinstance(fig, dict) or fig.get("src") or fig.get("embed"):
                continue
            filename = f"fig_{i + 1:02d}_{j}.jpg"
            if status_by_name.get(filename) == SEARCH_STATUS_SOURCED:
                fig["src"] = f"images/{filename}"
                fetched += 1
            else:
                failed += 1
    print(f"[charts] 联网图表参考：fetched={fetched} 留空占位={failed}")
    return {"fetched": fetched, "failed": failed, "queries": len(items)}


# ================================================================ CLI
def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(prog="python -m pptagent.charts",
                                 description="联网图表参考图拉取（按需，失败留空）")
    ap.add_argument("deck_plan", help="DeckPlan JSON 路径")
    ap.add_argument("--out", default="exports/demo", help="输出目录（放 images/）")
    ap.add_argument("--engine", default=None, help="ppt-master scripts 目录")
    ap.add_argument("--concurrency", type=int, default=2)
    args = ap.parse_args(argv)

    plan = json.loads(Path(args.deck_plan).read_text(encoding="utf-8"))
    report = fetch_chart_references(plan, args.out, args.engine, args.concurrency)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # 回填后写回 deck_plan（供后续 svggen 直接读）
    Path(args.deck_plan).write_text(json.dumps(plan, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
