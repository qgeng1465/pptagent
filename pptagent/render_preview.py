"""
fused-pipeline · 预览渲染
========================
把 out_dir/svg_output/slide_NN.svg 渲染成 1280×720 PNG + index.html，
供浏览器逐页审阅（不依赖 playwright / chromium）。

用法：
    python -m pptagent.render_preview exports/demo \
        --title "示例 Deck"

依赖 cairosvg；中文字体经 fontconfig 别名（见 ~/.config/fontconfig/fonts.conf）
落到本地 Noto Sans CJK SC。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import cairosvg


def _safe_stem(p: Path) -> int:
    try:
        return int(p.stem.split("_")[1])
    except Exception:
        return 0


def render_preview(out_dir: str, title: str = "学术 PPT 预览") -> dict:
    out = Path(out_dir)
    svg_dir = out / "svg_output"
    preview_dir = out / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    svgs = sorted([p for p in svg_dir.glob("slide_*.svg")], key=_safe_stem)
    cards = []
    for i, svg_path in enumerate(svgs, start=1):
        png_path = preview_dir / f"slide_{i:02d}.png"
        cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), output_width=1280, output_height=720)
        cards.append((i, png_path.name))
        print(f"  render {png_path.name}  ({svg_path.stat().st_size} B svg)")
    # index.html
    html_rows = []
    for i, name in cards:
        html_rows.append(
            f'<div class="card"><div class="num"><b>Slide {i:02d}</b><span class="tag">#{i}</span></div>'
            f'<img src="{name}"></div>'
        )
    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>{title} · 预览</title><style>
body{{background:#f0f2f5;color:#333f4e;font-family:system-ui,sans-serif;padding:28px}}
.wrap{{max-width:1400px;margin:0 auto}}
h1{{font-size:20px;border-left:5px solid #4472C4;padding-left:12px}}
h1 small{{color:#8fa2b8;font-weight:normal}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(440px,1fr));gap:22px;margin-top:20px}}
.card{{background:#fff;border:1px solid #d8e1ec;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(68,114,196,.08)}}
.num{{padding:8px 14px;font-size:12px;color:#8fa2b8;border-bottom:1px solid #d8e1ec;background:#f8fafc}}
.num b{{color:#4472C4}}
img{{width:100%;display:block}}
.tag{{display:inline-block;font-size:11px;color:#fff;background:#4472C4;border-radius:4px;padding:1px 6px;margin-left:6px}}
</style></head><body><div class="wrap">
<h1>{title} <small>· {len(cards)} 页渲染预览（浏览器打开原图）</small></h1>
<div class="grid">{"".join(html_rows)}</div></div></body></html>"""
    (preview_dir / "index.html").write_text(html, encoding="utf-8")
    return {"rendered": len(cards), "preview_dir": str(preview_dir)}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="渲染 SVG 预览 PNG")
    ap.add_argument("out_dir")
    ap.add_argument("--title", default="学术 PPT 预览")
    args = ap.parse_args(argv)
    print(json.dumps(render_preview(args.out_dir, args.title), ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
