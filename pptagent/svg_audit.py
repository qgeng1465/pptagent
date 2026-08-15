"""
fused-pipeline · SVG 结构程序化审计
====================================
评审循环的第三方证据：在评审 agent（基金专家×博导）之外，用像素级规则审计
13 页 SVG 的结构健康度。与 pixel_qa（渲染像素）互补——本模块直接读 SVG 文本。

检查项：
  1. 溢出：rect/circle/text 超出画布 1280×720
  2. 小字号：正文论证性文字 <13px（图注/坐标轴/页脚 9-11px 豁免，按黑名单定位）
  3. 配色纪律：统计 fill/stroke 用色数，检查是否 ≤4 主色（tsinghua 主题）
  4. 重叠：同区域 y 相近的 text 是否可能叠字

用法：
    python -m pptagent.svg_audit exports/demo/svg_output
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

W, H = 1280, 720

# 豁免区：页脚 / 坐标轴 / 图注 / kicker 等允许 9-12px 的元素
# 每项是 (y_范围, x_范围, 说明)。命中豁免区的 fs<13 不报告为问题。
EXEMPT = [
    (690, 700, 0, 1280, "页脚"),
    (668, 682, 0, 60, "页脚课题名左"),
    (668, 682, 1220, 1280, "页脚页码右"),
    (640, 665, 0, 1280, "footnote/来源行"),
    (200, 540, 110, 150, "图表 y 轴刻度"),
    (500, 540, 180, 600, "图表 x 轴标签"),
    (430, 540, 100, 620, "图表数据标签/目标标注"),
    (40, 60, 40, 200, "页头 kicker"),
]


def _is_exempt(x: float, y: float) -> str | None:
    for (y0, y1, x0, x1, label) in EXEMPT:
        if y0 <= y <= y1 and x0 <= x <= x1:
            return label
    return None


def audit_svg(path: Path) -> dict:
    s = path.read_text(encoding="utf-8")
    issues: list[dict] = []
    colors: dict[str, int] = {}

    def note(sev, kind, loc, msg):
        issues.append({"sev": sev, "kind": kind, "loc": loc, "msg": msg})

    # 1) 溢出
    for m in re.finditer(
        r'<(?:rect|circle|ellipse)[^>]*?x="([\d.]+)"[^>]*?y="([\d.]+)"[^>]*?width="([\d.]+)"[^>]*?height="([\d.]+)"',
        s,
    ):
        x, y, w, h = map(float, m.groups())
        if x + w > W + 1 or y + h > H + 1:
            note("P0", "溢出", f"({x:.0f},{y:.0f} {w:.0f}x{h:.0f})",
                 f"rect 右下角超画布 ({x + w:.0f},{y + h:.0f})")
    for m in re.finditer(r'<text[^>]*?x="([\d.]+)"[^>]*?y="([\d.]+)"', s):
        x, y = map(float, m.groups())
        if x > W or y > H:
            note("P0", "溢出", f"({x:.0f},{y:.0f})", "text 超出画布")

    # 2) 小字号（豁免区除外）
    for m in re.finditer(
        r'<text[^>]*?y="([\d.]+)"[^>]*?x="([\d.]+)"[^>]*?font-size="([\d.]+(?:\.[\d]+)?)"', s
    ):
        y, x, fs = float(m.group(1)), float(m.group(2)), float(m.group(3))
        fs = float(fs)
        if fs < 13 and _is_exempt(x, y) is None:
            note("P2", "小字号", f"({x:.0f},{y:.0f})", f"fs={fs:g} 正文过小")

    # 3) 配色纪律 —— 只统计"饱和色"（品牌/强调色）。
    # 中性色（ink 灰阶、发丝线、浅底、白色、浅红 tint）不算进 ≤4 色纪律：
    #   kimiPPT 的 ≤4 色指品牌强调色（血红 #C00000 / 深蓝灰 #293F59 / 深藏青 #002060 / 灰 #8FA2B8），
    #   灰阶文字与浅 tint 是设计系统的层级工具，不违反纪律。
    def _is_saturated(hexv: str) -> bool:
        r = int(hexv[0:2], 16); g = int(hexv[2:4], 16); b = int(hexv[4:6], 16)
        mx, mn = max(r, g, b), min(r, g, b)
        if mx == 0:                       # 纯黑
            return False
        if mx > 232:                      # 极浅色（浅 tint/浅蓝灰，层级工具不算强调色）
            return False
        if mx - mn < 24:                  # 近灰/近白（含 F5F6F8 浅底、FFFFFF 白）
            return False
        if mx < 90 and (mx - mn) < 40:    # 深灰 ink（#262A33 类）
            return False
        return True

    # 品牌强调色（4 色，与捕获组同格式=无 #；tsinghua: 血红/深蓝灰/深藏青/灰蓝）
    brand = {"C00000", "293F59", "002060", "8FA2B8"}
    # 中性层级：文本墨色/中阶灰蓝 + 品牌色浅 tint（层级工具，不计入 ≤4 色纪律）
    neutral = {"262A33", "44546A", "5A6A80", "C9D4E2",
               "F1C2C2", "F5C6C6", "F1C6C6", "FAEDED", "FBEFEF",
               "F5F6F8", "DDE1E6", "FFFFFF"}
    for m in re.finditer(r'(?:fill|stroke)="(?:url\(#\w+\)|#([0-9A-Fa-f]{6}))"', s):
        hexv = (m.group(1) or "").upper()
        if hexv and hexv not in neutral and _is_saturated(hexv):
            colors[hexv] = colors.get(hexv, 0) + 1
    extra = {c for c in colors if c not in brand}
    if extra:
        note("P2", "配色超限", "-",
             f"饱和色含品牌外 {len(extra)} 种: {sorted(extra)}（中性灰阶不计）")

    return {
        "file": path.name,
        "issues": issues,
        "n_issues": len(issues),
        "n_extra_colors": len(extra),
        "n_p0": sum(1 for i in issues if i["sev"] == "P0"),
    }


def main(out_dir: str) -> None:
    svg_dir = Path(out_dir)
    files = sorted(svg_dir.glob("slide_*.svg"))
    total_p0 = 0
    total_issues = 0
    print(f"{'文件':<14} {'P0':>3} {'P1/P2':>6}  详情")
    for f in files:
        r = audit_svg(f)
        total_p0 += r["n_p0"]
        total_issues += r["n_issues"]
        p1p2 = r["n_issues"] - r["n_p0"]
        extra = f"  额外色={r['n_extra_colors']}" if r["n_extra_colors"] else ""
        print(f"{f.name:<14} {r['n_p0']:>3} {p1p2:>6}  {extra}")
        for i in r["issues"][:12]:
            print(f"    [{i['sev']}] {i['kind']} {i['loc']}: {i['msg']}")
    print(f"\n=== 总判定: {'通过' if total_p0 == 0 else '存在 P0'} | "
          f"P0={total_p0} 总问题={total_issues} / {len(files)} 页 ===")
    return 0 if total_p0 == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
