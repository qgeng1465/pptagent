"""
fused-pipeline · 像素级 QA
==========================
对 preview/slide_NN.png 做结构性检查（白底 / 墨迹率 / 主题强调色 / 表格表头 /
示意图区域 / 页脚页码），弥补无法直接看图的问题。输出 JSON 报告。

强调色感知：从 deck_plan.theme 取主题 → tsinghua=红色系掩码，其余=蓝色系掩码。

用法：python -m pptagent.pixel_qa exports/demo [deck_plan.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

W, H = 1280, 720


def _load(p: Path) -> np.ndarray:
    return np.array(Image.open(p).convert("RGB")).astype(int)


def _ink(img) -> float:
    """非白像素占比。"""
    return (img.sum(axis=2) < 720).mean()


def _white_bg(img) -> bool:
    """页面平均亮度是否接近白。
    阈值取 >232：真正深色底页均值 ~45（section 深藏青），刻意设计的浅底面板
    （route 灰轨道 bg_alt #F5F6F8 + 深蓝目标条）会把均值压到 ~235，属白底。"""
    return img.mean() > 232


def _accent_ratio(img, theme: str) -> float:
    """主题强调色系像素占比。
    tsinghua(血红 #C00000)：r 强、明显高于 g/b；其余主题(蓝系)：b 强、明显高于 r/g。"""
    r, g, b = img[:, :, 0], img[:, :, 1], img[:, :, 2]
    if theme == "tsinghua":
        mask = (r > 130) & (r > g + 60) & (r > b + 60)
    else:
        mask = (b > 120) & (b > r + 40) & (b > g + 20)
    return mask.mean()


def _region_ink(img, x0, y0, x1, y1) -> float:
    return _ink(img[y0:y1, x0:x1])


def _dark_ratio(img) -> float:
    """低亮度像素占比（检测深色表头/色带，主题无关）。"""
    return (img.sum(axis=2) < 380).mean()


# 深色整页布局（section 深藏蓝）：白底检查按"预期深底"豁免
DARK_PAGE = {"section"}
# 有刻意深色元素的页（cover 顶带）：跳过白底判定，但仍检查密度/页脚
BG_SKIP = {"cover"}


def qa_page(p: Path, layout: str, theme: str) -> dict:
    img = _load(p)
    mean = float(img.mean())
    is_dark = layout in DARK_PAGE
    res = {
        "file": p.name,
        "white_bg": _white_bg(img),
        "dark_expected": is_dark,
        "mean_luma": round(mean, 1),
        "ink": round(float(_ink(img)), 4),
        "accent": round(float(_accent_ratio(img, theme)), 5),
        "layout": layout,
        "footer_ink": round(float(_region_ink(img, W - 160, H - 30, W - 30, H - 6)), 4),
    }
    return res


def main(out_dir: str, deck_plan: str | None = None) -> None:
    preview = Path(out_dir) / "preview"
    layouts = {}
    theme = "academic"
    if deck_plan:
        plan = json.loads(Path(deck_plan).read_text(encoding="utf-8"))
        layouts = {s["id"].split("_")[1]: s["layout"] for s in plan["slides"]}
        theme = plan.get("theme", "academic")
    accent_name = "红" if theme == "tsinghua" else "蓝"
    results = []
    for p in sorted(preview.glob("slide_*.png")):
        idx = p.stem.split("_")[1]
        res = qa_page(p, layouts.get(idx, "?"), theme)
        results.append(res)
    # 汇总
    ok = True
    print(f"{'页':>4} {'布局':<14} {'白底/深底':<8} {'墨迹率':>8} {'强调色':>9} {'页脚':>6}  状态")
    for r in results:
        issues = []
        skip_bg = r["dark_expected"] or r["layout"] in BG_SKIP
        if r["dark_expected"]:
            # 深色整页：期望深底（mean_luma 明显低），不做白底/密度判定
            if r["mean_luma"] > 200:
                issues.append("深底页太亮!"); ok = False
        elif r["layout"] in BG_SKIP:
            # 有刻意深色元素（cover 底带）：仍检查密度
            if r["ink"] < 0.008:
                issues.append("太空!"); ok = False
        else:
            if not r["white_bg"]:
                issues.append("非白底!"); ok = False
            if r["ink"] > 0.42:
                issues.append("过密!"); ok = False
            if r["ink"] < 0.008:
                issues.append("太空!"); ok = False
        if r["footer_ink"] < 0.01:
            issues.append("缺页脚!"); ok = False
        status = "OK" if not issues else " | ".join(issues)
        bg_mark = "深底✓" if r["dark_expected"] and r["mean_luma"] < 200 else (
            "跳过" if r["layout"] in BG_SKIP else ("✓" if r["white_bg"] else "✗"))
        print(f"{r['file']:<8} {r['layout']:<14} {bg_mark:<8} "
              f"{r['ink']:.3f}    {r['accent']:.5f}  {r['footer_ink']:.3f}  {status}")
        if issues:
            ok = False
    # 特殊区域检查
    print("\n--- 区域检查 ---")
    hdr_y = {"table": (168, 210), "chart_table": (184, 224), "flow_table": (362, 400)}
    for r in results:
        if r["layout"] in ("table", "flow_table", "chart_table"):
            img = _load(preview / r["file"])
            y0, y1 = hdr_y[r["layout"]]
            x0 = 693 if r["layout"] == "chart_table" else 56  # chart_table 表格在右半栏
            hdr_dark = _dark_ratio(img[y0:y1, x0:1184])
            print(f"{r['file']} [{r['layout']}] 表头深色带 y{y0}-{y1}: {hdr_dark:.3f} "
                  f"{'✓' if hdr_dark>0.5 else '✗ 无深色表头!'}")
            if hdr_dark <= 0.5:
                ok = False
        if r["layout"] == "hex":
            img = _load(preview / r["file"])
            fig_ink = _region_ink(img, 40, 150, 620, 620)
            print(f"{r['file']} [hex] 六边形图区域墨迹: {fig_ink:.3f} {'✓' if fig_ink>0.02 else '✗ 无图形!'}")
            if fig_ink <= 0.02:
                ok = False
        if r["layout"] == "chart_table":
            img = _load(preview / r["file"])
            chart_ink = _region_ink(img, 56, 150, 660, 560)
            print(f"{r['file']} [chart_table] 柱状图区域墨迹: {chart_ink:.3f} {'✓' if chart_ink>0.03 else '✗ 图太虚!'}")
            if chart_ink <= 0.03:
                ok = False
        if r["layout"] == "print3":
            img = _load(preview / r["file"])
            p3 = _region_ink(img, 56, 150, 1224, 380)
            print(f"{r['file']} [print3] 三步图区域墨迹: {p3:.3f} {'✓' if p3>0.02 else '✗ 图太虚!'}")
            if p3 <= 0.02:
                ok = False
        if r["layout"] == "refs":
            img = _load(preview / r["file"])
            dashed = _region_ink(img, 56, 150, 1224, 620)
            print(f"{r['file']} [refs] 占位框区域墨迹: {dashed:.3f} {'✓' if dashed>0.01 else '✗ 无占位框!'}")
            if dashed <= 0.01:
                ok = False
        if r["layout"] in ("fig_table_text", "fig_points", "research", "profile", "two_col"):
            img = _load(preview / r["file"])
            # 左侧图槽区域应有墨迹（占位框或图片）
            fig_ink = _region_ink(img, 56, 150, 620, 620)
            print(f"{r['file']} [{r['layout']}] 左侧区域墨迹: {fig_ink:.3f} "
                  f"{'✓' if fig_ink>0.015 else '✗ 左侧太空!'}")
            if fig_ink <= 0.015:
                ok = False
        if r["layout"] == "fig_table_text":
            img = _load(preview / r["file"])
            # 右下表格表头深色带（表 y≈518..556）
            hdr_dark = _dark_ratio(img[512:556, 610:1224])
            print(f"{r['file']} [fig_table_text] 右下表头深色带: {hdr_dark:.3f} "
                  f"{'✓' if hdr_dark>0.5 else '✗ 无深色表头!'}")
            if hdr_dark <= 0.5:
                ok = False
        if r["layout"] == "route":
            img = _load(preview / r["file"])
            route_ink = _region_ink(img, 56, 180, 1224, 520)
            print(f"{r['file']} [route] 技术路线卡区域墨迹: {route_ink:.3f} {'✓' if route_ink>0.03 else '✗ 路线太虚!'}")
            if route_ink <= 0.03:
                ok = False
        if r["layout"] == "cover":
            img = _load(preview / r["file"])
            band = img[H - 14:H, 0:W]
            band_dark = float(band.mean(axis=(1, 2)).mean())   # 底部深藏青带平均亮度
            print(f"{r['file']} [cover] 底部深藏青带均亮: {band_dark:.1f} "
                  f"{'✓' if band_dark < 180 else '✗ 无深色底带!'}")
            if band_dark >= 180:
                ok = False
    print(f"\n=== 总判定: {'全部通过' if ok else '存在问题'} ===")


if __name__ == "__main__":
    deck = sys.argv[2] if len(sys.argv) > 2 else None
    main(sys.argv[1], deck)
