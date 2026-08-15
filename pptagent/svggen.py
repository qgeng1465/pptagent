"""
fused-pipeline · SVG 页面生成器（v2 · 模板忠实版式）
====================================================
从 DeckPlan（pptagent/schema.py）生成 ppt-master 转换器兼容的 SVG 页面
（1280×720），再由 ppt-master 的 svg_to_pptx 转成原生可编辑 PPTX。

v2 版式规范（2026-08-04，来自高校组会模板 + 述职版式两类模板的解析）：
  · 白底永远（bg=#FFFFFF）
  · 内容页：标题左上角小标题（≈26px 粗）+ 发丝线；内容区从 y≈140 起，
    密度优先——页面要"饱满"，减少留白
  · 页脚：左下 = 汇报人·课题（述职风格），右下 = 页码（组会模板风格）
  · 表格走 native（data-pptx-replace-with="table"，表头=主题主深色，如红主题=深藏青 #293F59）
  · 示意图用纯 SVG 几何（多边形/圆/折线），无 AI 生图
  · 文献首页占位 = 虚线框，留空待用户粘贴论文首页截图
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Dict, List, Optional, Sequence

from .design.tokens import Theme, ACADEMIC, SCIENCE_POP, TSINGHUA, Color

# 当前主题（由 DeckPlan 的 theme 字段驱动，见 generate_deck）。
THEME: Theme = ACADEMIC

# 页脚左侧文字（generate_deck 时从 deck_plan.footer 设置）
_FOOTER_LEFT = ""


def _pal():
    return THEME.palette


_THEMES = {
    "academic": ACADEMIC,
    "science-pop": SCIENCE_POP,
    "tsinghua": TSINGHUA,
}

# ---------------------------------------------------------------- 画布
W, H = 1280, 720
MARGIN = 56                       # 页边距
CONTENT_W = W - 2 * MARGIN        # 1168
TITLE_TOP = 50                    # kicker 文本基线
TITLE_Y = 96                      # 标题文本基线（v0.6 放大到 32px，对标模板 34pt 标题）
HAIRLINE_Y = 130                  # 标题下发丝线
CONTENT_TOP = 152                 # 内容区起点（随标题放大下移）
CONTENT_BOTTOM = 668              # 内容区底线（footnote 上方）
FOOTER_Y = 692

# 字体（组会模板 = 等线/DengXian 首选，微软雅黑仅页码；预览无等线时 fontconfig 回退 Noto CJK）
FONT_CJK = '"DengXian","等线","Microsoft YaHei","PingFang SC","Noto Sans CJK SC","Arial","sans-serif"'
FONT_MONO = '"Consolas","Courier New",monospace'
FONT_SERIF = '"SimSun","宋体","Songti SC","Georgia",serif'


# ---------------------------------------------------------------- 工具
def esc(text) -> str:
    """XML 转义。"""
    if text is None:
        return ""
    return (str(text)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _c(color: Color) -> str:
    return "#" + color.hexstr()


def wrap_text(text: str, max_width: float, font_size: float) -> List[str]:
    """按估算宽度折行。中文≈1em，拉丁≈0.55em。
    支持显式换行符 \n（先按段切分再逐段折行），供内容里"语义断行"用。"""
    if not text:
        return [""]
    em = font_size
    est = lambda ch: em if ord(ch) > 0x2E80 else em * 0.55
    out: List[str] = []
    for seg in str(text).split("\n"):
        lines, cur, cur_w = [], "", 0.0
        for ch in seg:
            w = est(ch)
            if cur_w + w > max_width and cur:
                lines.append(cur)
                cur, cur_w = "", 0.0
            cur += ch
            cur_w += w
        if cur:
            lines.append(cur)
        out.extend(lines or [""])
    return out or [""]


def _shadow_on(fid: str) -> bool:
    """该阴影 id 在当前主题是否启用（无阴影主题 → 跳过 filter，去 AI 味）。"""
    m = {"sh-card": THEME.shadow_card is not None,
         "sh-node": THEME.shadow_node is not None,
         "sh-lg": THEME.shadow_panel is not None}
    return m.get(fid, False)


def _rect(x, y, w, h, fill=None, stroke=None, sw=None, rx=None, opacity=None, fid=None) -> str:
    a = f'x="{x}" y="{y}" width="{w}" height="{h}"'
    if rx: a += f' rx="{rx}"'
    if fill: a += f' fill="{fill}"'
    if stroke: a += f' stroke="{stroke}"' + (f' stroke-width="{sw}"' if sw else "")
    if opacity is not None: a += f' fill-opacity="{opacity}"'
    if fid and _shadow_on(fid): a += f' filter="url(#{fid})"'
    return f"<rect {a}/>"


def _dashed(x, y, w, h, stroke, sw=1, rx=0) -> str:
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="none" stroke="{stroke}" stroke-width="{sw}" stroke-dasharray="6 5"/>')


def _circle(cx, cy, r, fill=None, stroke=None, sw=None, opacity=None) -> str:
    a = f'cx="{cx}" cy="{cy}" r="{r}"'
    if fill: a += f' fill="{fill}"'
    if stroke: a += f' stroke="{stroke}"' + (f' stroke-width="{sw}"' if sw else "")
    if opacity is not None: a += f' fill-opacity="{opacity}"'
    return f"<circle {a}/>"


def _hex_pts(cx, cy, r, flat=True) -> str:
    """六边形顶点，flat=True 平顶（上边水平），False 尖顶（顶点向上）。"""
    pts = []
    for i in range(6):
        ang = math.radians(60 * i + (0 if flat else 30))
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in pts)


def _poly(pts_str, fill=None, stroke=None, sw=None, opacity=None) -> str:
    a = f'points="{pts_str}"'
    if fill: a += f' fill="{fill}"'
    if stroke: a += f' stroke="{stroke}"' + (f' stroke-width="{sw}"' if sw else "")
    if opacity is not None: a += f' fill-opacity="{opacity}"'
    return f"<polygon {a}/>"


def _line_seg(x1, y1, x2, y2, color, w=2, opacity=0.6, dash=None) -> str:
    a = f'x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{w}"'
    if opacity is not None: a += f' stroke-opacity="{opacity}"'
    if dash: a += f' stroke-dasharray="{dash}"'
    return f"<line {a}/>"


def _arrow(x1, y1, x2, y2, color, w=2) -> str:
    """带三角箭头的连线（用于流程图）。"""
    ang = math.atan2(y2 - y1, x2 - x1)
    L = 9
    ax = x2 - L * math.cos(ang - 0.42)
    ay = y2 - L * math.sin(ang - 0.42)
    bx = x2 - L * math.cos(ang + 0.42)
    by = y2 - L * math.sin(ang + 0.42)
    tri = f'{x2:.1f},{y2:.1f} {ax:.1f},{ay:.1f} {bx:.1f},{by:.1f}'
    return (_line_seg(x1, y1, x2 - L * 0.55 * math.cos(ang), y2 - L * 0.55 * math.sin(ang), color, w)
            + _poly(tri, fill=color))


def _text(x, y, s, size, fill, weight="normal", family=FONT_CJK,
          anchor="start", spacing=None, italic=False, opacity=None) -> str:
    a = f'x="{x}" y="{y}" font-family="{esc(family)}" font-size="{size}" fill="{fill}" font-weight="{weight}"'
    if anchor != "start": a += f' text-anchor="{anchor}"'
    if spacing is not None: a += f' letter-spacing="{spacing}"'
    if italic: a += ' font-style="italic"'
    if opacity is not None: a += f' opacity="{opacity}"'
    return f'<text {a}>{esc(s)}</text>'


def _tspan(dy, s, size, fill, weight="normal", family=FONT_CJK) -> str:
    return (f'<tspan x="0" dy="{dy}" font-family="{esc(family)}" font-size="{size}" '
            f'fill="{fill}" font-weight="{weight}">{esc(s)}</tspan>')


def _g(body: str, gid: str = None, bounds: Sequence[int] = None) -> str:
    a = ""
    if gid: a += f' id="{gid}"'
    if bounds: a += f' data-pptx-bounds="{" ".join(str(int(v)) for v in bounds)}"'
    return f"<g{a}>{body}</g>"


def _card(x, y, w, h, fill="#FFFFFF", stroke=None, sw=1, rx=0, fid=None) -> str:
    # v0.6 默认直角：学术规范"禁圆角卡片做层级"（kimiPPT academic-research）——用规则/留白/字号对比做层级
    return _rect(x, y, w, h, fill=fill, stroke=stroke, sw=sw, rx=rx, fid=fid)


def _chip(x, y, text_s, fill, color, size=12, pad=10, h=26, weight="600", gid=None) -> str:
    est = sum(size if ord(c) > 0x2E80 else size * 0.55 for c in text_s) + pad * 2
    w = max(est, 36)
    body = _rect(x, y, w, h, fill=fill, rx=h / 2) + \
           _text(x + w / 2, y + h / 2 + size * 0.36, text_s, size, color, weight, anchor="middle")
    return _g(body, gid=gid, bounds=(x, y, w, h))


# ---------------------------------------------------------------- 阴影
def _filters() -> str:
    def f(name, dx, dy, blur, color, alpha):
        return (f'<filter id="{name}" x="-30%" y="-30%" width="160%" height="160%">'
                f'<feDropShadow dx="{dx}" dy="{dy}" stdDeviation="{blur}" '
                f'flood-color="{color}" flood-opacity="{alpha}"/></filter>')
    # 只输出当前主题实际启用的阴影（无阴影主题 → 空 defs，SVG 无死代码）
    _ORIG = {  # 保持蓝主题历史观感：dx, dy, blur, color, alpha
        "sh-card": (0, 2, 4, "#44546A", 0.16),
        "sh-node": (0, 2, 3, "#44546A", 0.14),
        "sh-lg": (0, 4, 7, "#1A3E6F", 0.20),
    }
    enabled = {"sh-card": THEME.shadow_card, "sh-node": THEME.shadow_node,
               "sh-lg": THEME.shadow_panel}
    parts = [f(name, *_ORIG[name]) for name, sh in enabled.items() if sh is not None]
    return f"<defs>{''.join(parts)}</defs>"


def _chart_pal() -> List[str]:
    """图表系列色（hex）。手绘 fallback 与原生 chart point_colors 共用，保证预览与 PPTX 一致。"""
    return [_c(c) for c in _pal().chart_colors]


def _bar_colors(n: int, highlight: Optional[int] = None) -> List[str]:
    """柱状图逐柱配色（克制 2 色）：常规柱 = 弱化灰（chart_colors[-1]），
    高亮柱 = 锚点色（chart_colors[0]）——报奖 PPT 常见的"灰底 + 一柱强调"。"""
    pal = _pal()
    base = _c(pal.chart_colors[-1]) if pal.chart_colors else _c(pal.primary_2)
    cols = [base for _ in range(max(n, 1))]
    if highlight is not None and 0 <= highlight < n:
        cols[highlight] = _c(pal.chart_colors[0])
    return cols


def _bar_fill(color: str) -> str:
    """柱 fill：实色（去掉垂直渐变，更朴素克制，避免"AI 渲染感"）。"""
    return color


def _nice_max(raw: float) -> float:
    """把 y 轴最大值上取整为"4 格整数刻度"的数（0/…/maxv 每格为整数）。"""
    import math
    if raw <= 0:
        return 1.0
    return math.ceil(raw / 4.0) * 4.0


def _tint(color: Color, alpha: float) -> str:
    """color 与白底按 alpha 混合后的实色 hex（原生表格 cell fill 用，避免 rgba）。"""
    r = int(255 - (255 - color.r) * alpha)
    g = int(255 - (255 - color.g) * alpha)
    b = int(255 - (255 - color.b) * alpha)
    return f"#{r:02X}{g:02X}{b:02X}"


def _rgba_hex(color: Color, alpha: float) -> str:
    return f"rgba({color.r},{color.g},{color.b},{alpha})"


# ---------------------------------------------------------------- 页头 / 页脚
def _accent_color(accent: str) -> Color:
    pal = _pal()
    return {
        "primary": pal.primary,
        "teal": pal.teal,
        "accent": pal.accent,
        "danger": pal.danger,
    }.get(accent, pal.primary_2)


def _page_head(title: str, kicker: str, accent: str) -> str:
    """模板式页头（克制学院风）：左上角红色小节标签 + 大标题 + 短红下划线 + 发丝线。
    v0.6 标题 25→32px（对标组会模板 34pt 内容标题 / kimiPPT 学术页标题 32-40pt）。"""
    pal = _pal()
    a = _c(_accent_color(accent))
    body = ""
    if kicker:
        body += _text(MARGIN, TITLE_TOP, kicker, 12, a, "700", spacing=2)
    body += _text(MARGIN, TITLE_Y, title, 32, _c(pal.ink), "700")
    body += _rect(MARGIN, HAIRLINE_Y, 60, 3, fill=a)
    body += _rect(MARGIN + 70, HAIRLINE_Y, CONTENT_W - 70, 1, fill=_c(pal.line))
    return body


def _footer(page_no: int, total: int, color=None) -> str:
    """页脚：左下=课题短名（无个人姓名，对标报奖 PPT 页脚规范），右下=页码导航。"""
    pal = _pal()
    c = color or _c(pal.ink_2)
    left = _FOOTER_LEFT or ""
    body = ""
    if left:
        body += _rect(MARGIN, FOOTER_Y - 4, 3, 12, fill=_c(pal.accent))
        body += _text(MARGIN + 12, FOOTER_Y, left, 10.5, c)
    body += _text(W - MARGIN, FOOTER_Y, f"{page_no:02d} / {total:02d}",
                  11, c, family=FONT_MONO, anchor="end")
    return body


# ================================================================ 布局
def _cover(s: dict) -> str:
    """模板式封面（对标《组会模板》slide 1）：
    白底 + 顶部细红带 + 血红大标题 + 短红分隔线 + 居中信息块（无 chip/点阵/ghost 字）。"""
    pal = _pal()
    c = s.get("content", {})
    body = ""
    body += _rect(0, 0, W, 6, fill=_c(pal.primary))
    kick = s.get("kicker", "") or "组会汇报 · GROUP MEETING"
    body += _text(W / 2, 178, kick, 13, _c(pal.ink_3), "700", anchor="middle", spacing=4)
    # 血红大标题（自动折行；v0.6 44→50px 对标 kimiPPT 学术封面标题 48-64pt）
    title = s.get("title", "无标题")
    lines = wrap_text(title, CONTENT_W - 60, 50)
    ty = 262
    for i, ln in enumerate(lines):
        size = 50 if i == 0 else 40
        body += _text(W / 2, ty, ln, size, _c(pal.primary), "800", anchor="middle")
        ty += int(size * 1.26)
    body += _rect(W / 2 - 54, ty + 6, 108, 3, fill=_c(pal.primary))
    ty += 46
    sub = c.get("subtitle", "")
    if sub:
        body += _text(W / 2, ty, sub, 18, _c(pal.ink_2), "500", anchor="middle")
        ty += 36
    head = c.get("headline", "")
    if head:
        body += _text(W / 2, ty, head, 15, _c(pal.ink_3), "400", anchor="middle", italic=True)
        ty += 36
    # meta：居中文本行（无 chip）
    metas = [m for m in str(c.get("meta", "")).split("\n") if m.strip()]
    if metas:
        for m in metas:
            body += _text(W / 2, ty, m, 13, _c(pal.ink_2), "500", anchor="middle")
            ty += 26
    # 底部深藏青带（模板封面底部暗块，加厚以压住页面）
    body += _rect(0, H - 16, W, 16, fill=_c(pal.deep))
    return body


def _overview(s: dict) -> str:
    """目录页（模板式清单，无卡片/chip/竖条）：
    页头与全篇一致（红 kicker 在上 + 大标题 + 红短横 + 发丝线）；
    红色 Part 编号与标题同一基线，灰色描述在下，发丝线分隔。"""
    pal = _pal()
    body = ""
    items = s.get("content", {}).get("items", [])
    # 页头与全篇统一（kicker 12px/spacing=2 + 标题 32px，复用 _page_head，避免本页独异）
    body += _page_head(s.get("title", "内容概览"), s.get("kicker", "OVERVIEW"), s.get("accent", ""))
    rows = items if items else []
    y0 = CONTENT_TOP + 18
    gap = 10
    n = len(rows)
    max_bottom = CONTENT_BOTTOM - 16
    # 行高自适应填满内容区（6 行 → 72px/行），而非固定 54 留下底部大片空白（评审 P2）
    row_h = min(80, (max_bottom - y0 - gap * max(n - 1, 0)) / max(n, 1)) if n else 54
    for i, it in enumerate(rows):
        y = y0 + i * (row_h + gap)
        part = it.get("part", f"{i+1:02d}") if isinstance(it, dict) else f"{i+1:02d}"
        ttl = it.get("title", "") if isinstance(it, dict) else str(it)
        desc = it.get("desc", "") if isinstance(it, dict) else ""
        # 序号与标题同一基线（21px 序号 + 18px 标题共用 0.46 基线）
        body += _text(MARGIN, y + row_h * 0.46, part, 21, _c(pal.primary), "800", family=FONT_MONO)
        body += _text(MARGIN + 74, y + row_h * 0.46, ttl, 18, _c(pal.ink), "700")
        body += _text(MARGIN + 74, y + row_h * 0.76, desc, 13.5, _c(pal.ink_3), "400")
        if i < n - 1:
            body += _rect(MARGIN, y + row_h - 2, CONTENT_W, 1, fill=_c(pal.line))
    return body


def _section(s: dict) -> str:
    """Part 章节页（模板式深藏青整页）：红色 kicker/短横 + 白色大标题 + 描述 + 红进度点。"""
    pal = _pal()
    c = s.get("content", {})
    idx = c.get("index", "01")
    body = _rect(0, 0, W, H, fill=_c(pal.deep))
    body += _rect(0, 0, W, 6, fill=_c(pal.primary))
    kick = s.get("kicker", "")
    if kick:
        body += _text(MARGIN, 268, kick, 13, "#F1C2C2", "700", spacing=3)  # 浅红：深藏青底上对比度≈4.8:1
    body += _rect(MARGIN, 292, 92, 5, fill=_c(pal.primary))
    body += _text(MARGIN, 386, s.get("title", ""), 42, "#FFFFFF", "700")
    desc = c.get("desc", "")
    dy = 446
    for ln in wrap_text(desc, CONTENT_W - 120, 17):
        body += _text(MARGIN, dy, ln, 17, "#C9D4E2", "400")
        dy += 28
    # 章节进度点（inactive 提亮对比度；当前页放大红点 + 白描边，一目了然）
    total = c.get("total", 2)
    for i in range(total):
        active = (f"{i+1:02d}" == idx)
        r = 9 if active else 7
        body += _circle(MARGIN + i * 30, 516, r,
                        fill=_c(pal.primary) if active else "#5A6A80",
                        stroke="#FFFFFF" if active else "#8FA2B8", sw=1.5)
    body += _rect(0, H - 8, W, 8, fill=_c(pal.primary))
    return body


def _content(s: dict) -> str:
    """密集内容页：intro + 要点卡 + 可选图槽（图+文字结合，不再全是文字框）。"""
    pal = _pal()
    body = _page_head(s.get("title", ""), s.get("kicker", ""), s.get("accent", ""))
    c = s.get("content", {})
    figs = c.get("figures", [])
    y = CONTENT_TOP
    intro = c.get("intro", "")
    if intro:
        body += _text(MARGIN, y, intro, 16, _c(pal.ink_2), "500")
        y += 32
        body += _rect(MARGIN, y - 16, 56, 3, fill=_c(pal.primary))
        y += 14

    pts = c.get("points", [])
    px, pw, py, ph = MARGIN, CONTENT_W, y, CONTENT_BOTTOM - (32 if c.get("footnote") else 0) - y
    # 图槽：右/左分栏，或顶/全宽横幅
    if figs:
        fig = figs[0]
        slot = fig.get("slot", "right") if isinstance(fig, dict) else "right"
        if slot in ("left", "right"):
            fx, fy, fw, fh = _slot_rect(slot)
            fh = min(fh, CONTENT_BOTTOM - 40 - fy)
            body += _fig_slot(fx, fy, fw, fh, fig, idx=0)
            if slot == "right":
                px, pw = MARGIN, fw - 24
            else:
                px, pw = fx + fw + 24, CONTENT_W - fw - 24
        else:
            fx, fy, fw, fh = _slot_rect("top")
            body += _fig_slot(fx, fy, fw, fh, fig, idx=0)
            py = fy + fh + 20
            ph = CONTENT_BOTTOM - 32 - py
    n = len(pts)
    if n == 0:
        return body
    cols = 2 if n >= 4 else 1
    gap = 22
    cw = (pw - gap * (cols - 1)) / cols
    rows_n = (n + cols - 1) // cols
    card_h = min(158, (ph - gap * (rows_n - 1)) / rows_n)
    for i, pt in enumerate(pts[:8]):
        r, cc = divmod(i, cols)
        x = px + cc * (cw + gap)
        yy = py + r * (card_h + gap)
        body += _card(x, yy, cw, card_h, fill=_c(pal.card),
                      stroke=_c(pal.line), rx=0, fid="sh-card")
        body += _rect(x, yy, 5, card_h, fill=_c(_accent_color("primary")))
        lead = pt.get("lead", "")
        for j, ln in enumerate(wrap_text(lead, cw - 44, 15.5)):
            body += _text(x + 24, yy + 34 + j * 21, ln, 15.5, _c(pal.ink), "700")
            if j >= 2:
                break
        b = pt.get("body", "")
        by = yy + 76
        for ln in wrap_text(b, cw - 40, 12.5)[:4]:
            body += _text(x + 24, by, ln, 12.5, _c(pal.ink_2), "400")
            by += 19
    footnote = c.get("footnote", "")
    if footnote:
        body += _text(MARGIN, CONTENT_BOTTOM + 8, footnote, 11, _c(pal.ink_3), "400")
    return body


def _steps(s: dict) -> str:
    """竖向编号步骤（打印三步 / 合成流程），左侧连接线。"""
    pal = _pal()
    body = _page_head(s.get("title", ""), s.get("kicker", ""), s.get("accent", ""))
    steps = s.get("content", {}).get("steps", [])
    x0 = MARGIN + 30
    y0 = CONTENT_TOP + 4
    step_h = 92
    gap = 26
    # 竖向连接线
    total_h = len(steps) * (step_h + gap)
    body += _rect(x0 + 17, y0 + 40, 3, total_h - 40, fill=_c(pal.primary_2), opacity=0.25)
    y = y0
    for i, st in enumerate(steps):
        no = st.get("no", f"{i+1:02d}")
        ttl = st.get("title", "")
        desc = st.get("desc", "")
        tags = st.get("tags", [])
        # 编号圆
        body += _circle(x0 + 18, y + 34, 20, fill=_c(pal.primary))
        body += _text(x0 + 18, y + 40, str(no), 15, "#FFFFFF", "700", anchor="middle")
        # 内容卡
        body += _card(x0 + 60, y, CONTENT_W - 60, step_h, fill=_c(pal.card),
                      stroke=_c(pal.line), rx=0, fid="sh-card")
        body += _text(x0 + 88, y + 32, ttl, 16.5, _c(pal.ink), "700")
        dy = y + 62
        for ln in wrap_text(desc, CONTENT_W - 190, 13)[:2]:
            body += _text(x0 + 88, dy, ln, 13, _c(pal.ink_2), "400")
            dy += 20
        # 右侧标签（小字文本，去 chip）
        tx = x0 + 60 + CONTENT_W - 60 - 20
        for tag in reversed(tags):
            tw = sum(11 if ord(c2) > 0x2E80 else 11 * 0.55 for c2 in tag)
            body += _text(tx, y + 30, tag, 11, _c(pal.primary), "600", anchor="end")
            tx -= tw + 12
        y += step_h + gap
    footnote = s.get("content", {}).get("footnote", "")
    if footnote:
        body += _text(MARGIN, CONTENT_BOTTOM + 8, footnote, 11, _c(pal.ink_3), "400")
    return body


def _native_rows(rows, highlight_row=None):
    """原生表格行：首列加粗 + 浅蓝底，高亮行 accent 淡底（与 SVG fallback 一致）。"""
    pal = _pal()
    col0_fill = _tint(pal.primary, 0.07)
    hl_fill = _tint(pal.accent, 0.18)
    out = []
    for i, r in enumerate(rows):
        row = []
        for j, v in enumerate(r):
            cell = {"text": str(v)}
            if j == 0:
                cell["bold"] = True
                cell["color"] = _c(pal.primary)
                cell["fill"] = col0_fill
            if highlight_row is not None and i == highlight_row:
                cell["fill"] = hl_fill
            row.append(cell)
        out.append(row)
    return out


def _table_style(font_size=12) -> dict:
    pal = _pal()
    return {
        "header_fill": _c(pal.primary_2),
        "header_text": "#FFFFFF",
        "body_fill": "#FFFFFF",
        "body_text": _c(pal.ink),
        "band_fill": _c(pal.bg_alt),
        "band_row": True,
        "font_family": "Microsoft YaHei",
        "font_size": font_size,
        "header_font_size": font_size,
        "border_color": _c(pal.line),
        "border_width": 0.75,
    }


def _table(s: dict) -> str:
    """原生表格页：主题深色表头 + 斑马行 + 首列着色 + 高亮行。"""
    pal = _pal()
    body = _page_head(s.get("title", ""), s.get("kicker", ""), s.get("accent", ""))
    c = s.get("content", {})
    cols = c.get("columns", [])
    rows = c.get("rows", [])
    caption = c.get("caption", "")
    if caption:
        body += _text(MARGIN, CONTENT_TOP + 6, caption, 13, _c(pal.primary_2), "600")
    fy = CONTENT_TOP + 26
    fh = min(452, 40 + len(rows) * 36)
    header = cols or []
    style = _table_style()
    meta = {
        "x": MARGIN, "y": fy, "width": CONTENT_W, "height": fh,
        "columns": header,
        "rows": _native_rows(rows, c.get("highlight_row")),
        "header_rows": 1 if header else 0,
        "column_widths": [1.0] * len(cols) if cols else [],
        "style": style,
        "strict_grid": True,
    }
    meta_xml = esc(json.dumps(meta, ensure_ascii=False))
    native = (
        f'<g data-pptx-replace-with="table" data-pptx-bounds="{MARGIN} {fy} {CONTENT_W} {fh}">'
        f'<metadata type="application/json">{meta_xml}</metadata>'
        f'{_table_fallback(MARGIN, fy, CONTENT_W, fh, header, rows, c.get("highlight_row"))}'
        f'</g>'
    )
    body += native
    fn = c.get("footnote", "")
    if fn:
        body += _text(MARGIN, CONTENT_BOTTOM + 8, fn, 11, _c(pal.ink_3), "400")
    return body


def _table_fallback(x, y, w, h, cols, rows, highlight_row=None) -> str:
    """与原生表格对应的可见 SVG（预览 / 非原生导出用）。首列着色 + 高亮行。"""
    pal = _pal()
    n = max(len(cols), 1)
    header_h = 38
    nrows = len(rows)
    row_h = max(26, (h - header_h) / max(nrows, 1)) if nrows else 0
    cw = w / n
    body = ""
    # 表头（深蓝 primary_2）
    body += _rect(x, y, w, header_h, fill=_c(pal.primary_2))
    for j, cname in enumerate(cols):
        body += _text(x + cw * j + 12, y + header_h * 0.66, cname, 12, "#FFFFFF", "600")
    # 行
    yy = y + header_h
    for i, r in enumerate(rows):
        if i % 2 == 1:
            body += _rect(x, yy, w, row_h, fill=_c(pal.bg_alt))
        if highlight_row is not None and i == highlight_row:
            body += _rect(x, yy, w, row_h, fill=_tint(pal.accent, 0.22))
        # 首列浅蓝底
        body += _rect(x, yy, cw, row_h, fill=_tint(pal.primary, 0.07))
        for j, v in enumerate(r[:n]):
            hl = highlight_row is not None and i == highlight_row
            col = _c(pal.primary) if j == 0 else (_c(pal.ink) if not hl else _c(pal.primary))
            wt = "600" if j == 0 else ("600" if (hl and j == 0) else "400")
            body += _text(x + cw * j + 12, yy + row_h * 0.62, str(v), 12, col, wt)
        yy += row_h
    return body


# ================================================================ 示意图
def _fig_hexlobule(x, y, w, h, c=None) -> str:
    """仿生肝小叶六边形示意图：外圈内皮框架 + 中心实质区 + Kupffer 交界。"""
    pal = _pal()
    c = c or {}
    cx, cy = x + w * 0.48, y + h * 0.56
    R = min(w, h) * 0.30
    r_inner = R * 0.62
    rc = R * 0.20
    # 径向射线（血窦示意；端点收到 0.78R 边内，避免触到六边形边界/右列标签）
    for i in range(6):
        ang = math.radians(60 * i)
        bx, by = cx + R * 0.78 * math.cos(ang), cy + R * 0.78 * math.sin(ang)
        body = _line_seg(cx + rc * 1.3 * math.cos(ang), cy + rc * 1.3 * math.sin(ang), bx, by,
                         _c(pal.primary_2), w=1.5, opacity=0.35)
    # 外六边形：HUVEC 内皮屏障（粗蓝描边）
    body += _poly(_hex_pts(cx, cy, R), fill="none", stroke=_c(pal.primary), sw=7)
    # 内六边形：HepaRG 实质区（浅蓝填充）
    body += _poly(_hex_pts(cx, cy, r_inner), fill=_c(pal.bg_alt), stroke=_c(pal.primary_2), sw=2)
    # 中心：中央静脉 / 中心区
    body += _circle(cx, cy, rc, fill=_c(pal.primary_2))
    body += _circle(cx, cy, rc * 0.5, fill=_c(pal.bg))
    # 标注（右列标签用 start 对齐，确保完全在六边形外，不压图形/卡片边）
    labels = [
        (cx, cy - R - 20, "周边 2-3 层 · HUVEC 内皮屏障", _c(pal.primary), "600", "middle"),
        (cx, cy, "HepaRG", _c(pal.primary_2), "700", "middle"),
        (cx, cy + 22, "中心实质区", _c(pal.primary_2), "400", "middle"),
        (cx + R + 62, cy - 22, "THP-1 → Kupffer", _c(pal.ink), "600", "start"),
        (cx + R + 62, cy, "预混 / 交界层", _c(pal.ink_3), "400", "start"),
    ]
    for (lx, ly, t, col, wt, an) in labels:
        body += _text(lx, ly, t, 13.5, col, wt, anchor=an)
    # 中心标注线（连到右列标签）
    body += _line_seg(cx + R + 8, cy - 10, cx + R + 58, cy - 14, _c(pal.line), w=1.5)
    return body


def _fig_process(x, y, w, h, steps) -> str:
    """竖向流程示意图（图槽内）：步骤盒 + 编号圆 + 向下一步 + 状态 tag。
    用于"内容是工艺/流程、暂无真实照片"的图槽，把版面填实而非留白。"""
    pal = _pal()
    n = len(steps)
    if n == 0:
        return ""
    body = ""
    gap = 14
    arrow_h = 16
    node_h = min(82, (h - gap * (n - 1) - arrow_h * max(n - 1, 0)) / n)
    yy = y
    for i, st in enumerate(steps):
        label = st.get("label", "") if isinstance(st, dict) else str(st)
        sub = st.get("sub", "") if isinstance(st, dict) else ""
        tag = st.get("tag", "") if isinstance(st, dict) else ""
        cy_ = yy + node_h / 2
        body += _card(x + 16, yy, w - 32, node_h, fill=_c(pal.bg_alt), stroke=_c(pal.line), rx=0, fid="sh-node")
        body += _circle(x + 44, cy_, 14, fill=_c(pal.primary))
        body += _text(x + 44, cy_ + 5, f"{i+1}", 11, "#FFFFFF", "700", anchor="middle")
        tx = x + 70
        body += _text(tx, cy_ - 4, label, 13.5, _c(pal.ink), "700")
        if sub:
            body += _text(tx, cy_ + 16, sub, 12.5, _c(pal.ink_3), "400")
        if tag:
            body += _text(x + w - 20, cy_ - 4, tag, 10, _c(pal.primary), "600", anchor="end")
        if i < n - 1:
            ay0 = yy + node_h + 2
            ay1 = yy + node_h + gap - 2
            body += _arrow(x + w / 2, ay0, x + w / 2, ay1, _c(pal.primary_2), w=1.8)
        yy += node_h + gap
    return body


def _fig_barchart_fallback(x, y, w, h, data, target=None, highlight=1, unit="kPa", title="") -> str:
    """柱状图 SVG fallback（预览/非原生导出用；原生导出时被数据对象替换）。
    data=[(label, value, note)] 或 [(label, value, note, err)]；渐变柱 + 误差棒 + 显著性星标。"""
    pal = _pal()
    body = ""
    t_top = 34 if title else 8
    plot_l, plot_r, plot_t, plot_b = 74, 12, t_top + 10, 40
    pw, ph = w - plot_l - plot_r, h - plot_t - plot_b
    bx0, by0 = x + plot_l, y + plot_t
    if title:
        body += _text(x + plot_l - 30, y + 18, title, 13, _c(pal.primary_2), "700")
    raw = max([v for _, v, *_ in data] + [target or 0]) * 1.08
    maxv = _nice_max(raw)
    cols = _bar_colors(len(data), highlight)
    # 网格线 + y 轴（4 格整数刻度；g=0 的 "0" 由下方单独画，避免重复）
    for g in range(1, 5):
        gy = by0 + ph - ph * g / 4
        body += _line_seg(bx0, gy, bx0 + pw, gy, _c(pal.line), w=1, opacity=0.7)
        val = maxv * g / 4
        body += _text(bx0 - 10, gy + 4, f"{val:.0f}", 10.5, _c(pal.ink_3), anchor="end")
    body += _text(bx0 - 10, by0 + ph + 4, "0", 10.5, _c(pal.ink_3), anchor="end")
    # y 轴单位标题（与原生图表 value axis title 逐字一致）
    body += _text(bx0 - 10, by0 - 8, unit, 10.5, _c(pal.ink_3), anchor="end")
    # 柱（与原生图表 point_colors 同源：常规灰 + 高亮锚色）
    n = len(data)
    bw = pw / n * 0.56
    hl_col = _c(pal.chart_colors[0]) if pal.chart_colors else _c(pal.accent)
    for i, item in enumerate(data):
        label, v = item[0], float(item[1])
        note = item[2] if len(item) > 2 else ""
        err = float(item[3]) if len(item) > 3 else 0.0
        hh = ph * v / maxv
        bx = bx0 + pw * (i + 0.5) / n - bw / 2
        by = by0 + ph - hh
        body += _rect(bx, by, bw, hh, fill=_bar_fill(cols[i]), rx=2)
        # 误差棒（±err 以柱顶=均值为中心，上下均匀）
        if err > 0:
            ey = by
            half_e = ph * err / maxv
            body += _line_seg(bx + bw / 2, ey - half_e, bx + bw / 2, ey + half_e, _c(pal.ink_3), w=1.2)
            body += _line_seg(bx + bw / 2 - 4, ey - half_e, bx + bw / 2 + 4, ey - half_e, _c(pal.ink_3), w=1.2)
            body += _line_seg(bx + bw / 2 - 4, ey + half_e, bx + bw / 2 + 4, ey + half_e, _c(pal.ink_3), w=1.2)
        # 显著性星标（标签上移，避免贴到 target 虚线上）
        if err > 0 and i == highlight:
            body += _text(bx + bw / 2, by - 30, "★", 13, _c(pal.accent), "700", anchor="middle")
            body += _text(bx + bw / 2, by - 16, f"{v:g}", 12.5, _c(pal.ink), "700", anchor="middle")
        else:
            body += _text(bx + bw / 2, by - 14, f"{v:g}", 12.5, _c(pal.ink), "700", anchor="middle")
        # x 标签
        for j, ln in enumerate(wrap_text(label, bw + 30, 10.5)):
            body += _text(bx + bw / 2, by0 + ph + 16 + j * 14, ln, 10.5, _c(pal.ink_2), "400", anchor="middle")
        # 柱内注记：高亮红柱白字，灰柱用深字（灰上白字对比度不足）
        if note and hh > 26:
            ncol = "#FFFFFF" if cols[i] == hl_col else _c(pal.ink)
            body += _text(bx + bw / 2, by + 18, note, 10, ncol, "600", anchor="middle")
    body += _rect(bx0, by0 + ph, pw, 1.5, fill=_c(pal.ink_3))
    # target 线（画在柱后 → 虚线压柱上可见）；标注锚到虚线左端
    # （左端上方通常是矮柱空区，标注不被最高柱遮挡；若左端无空会由数据标签上移避让）
    if target:
        ty = by0 + ph - ph * target / maxv
        body += _line_seg(bx0, ty, bx0 + pw, ty, _c(pal.accent), w=2, dash="7 5")
        body += _text(bx0 + 8, ty - 8, f"目标 ≈ {target} {unit}", 11,
                      _c(pal.accent), "700", anchor="start")
    return body


def _fig_barchart_native(x, y, w, h, data, target=None, highlight=1, unit="kPa", title="") -> str:
    """原生 PowerPoint 柱状图（column/clustered）：data-pptx-replace-with="chart"。
    逐点配色 point_colors、数据标签、网格线；chart_area_fill=none 露出背后阴影卡片。
    必须保留可见 SVG fallback（title 逐字一致，否则 chrome 校验报错）。"""
    pal = _pal()
    categories = [d[0] for d in data]
    values = [float(d[1]) for d in data]
    errors = [float(d[3]) if len(d) > 3 else 0.0 for d in data]
    n = len(data)
    cols = _bar_colors(n, highlight)
    meta = {
        "x": int(x), "y": int(y), "width": int(w), "height": int(h),
        "type": "column", "grouping": "clustered",
        "title": title,
        "categories": categories,
        "series": [{"name": f"{unit} 值", "values": values, "point_colors": cols,
                    "error_values": errors if any(errors) else None}],
        "data_labels": {"show_value": True, "position": "outside_end",
                        "font_size": 11, "color": _c(pal.ink)},
        "style": {"colors": _chart_pal(),
                  "chart_area_fill": "none", "plot_area_fill": "none",
                  "text_color": _c(pal.ink_2), "axis_color": _c(pal.line),
                  "grid_color": _c(pal.line)},
        "axes": {"category": {"visible": True},
                 "value": {"visible": True, "major_gridlines": True}},
    }
    meta_xml = esc(json.dumps(meta, ensure_ascii=False))
    body = (
        f'<g id="chart-{int(x)}-{int(y)}" data-pptx-replace-with="chart" '
        f'data-pptx-bounds="{int(x)} {int(y)} {int(w)} {int(h)}">'
        f'<metadata type="application/json">{meta_xml}</metadata>'
        f'{_fig_barchart_fallback(x, y, w, h, data, target, highlight, unit, title)}'
        f'</g>'
    )
    return body


def _fig_barchart(x, y, w, h, data, target=None, highlight=1, unit="kPa") -> str:
    """兼容别名：非原生场景直接手绘（无 title）。"""
    return _fig_barchart_fallback(x, y, w, h, data, target, highlight, unit, "")


def _fig_flow(x, y, w, h, steps, pal_col=None) -> str:
    """横向流程 N 步：编号圆 + 圆角卡 + 箭头。"""
    pal = _pal()
    n = len(steps)
    if n == 0:
        return ""
    gap = 46
    bw = (w - gap * (n - 1)) / n
    body = ""
    for i, st in enumerate(steps):
        bx = x + i * (bw + gap)
        by = y + h * 0.20
        bh = h * 0.60
        no = st.get("no", f"{i+1:02d}")
        body += _card(bx, by, bw, bh, fill=_c(pal.card), stroke=_c(pal.primary_2), sw=1.5, rx=0, fid="sh-node")
        body += _rect(bx, by, bw, 5, fill=_c(pal.primary))
        body += _circle(bx + 20, by + 22, 13, fill=_c(pal.primary))
        body += _text(bx + 20, by + 26, str(no), 11, "#FFFFFF", "700", anchor="middle")
        lab = st.get("label", "")
        sub = st.get("sub", "")
        body += _text(bx + 18, by + 56, lab, 14, _c(pal.ink), "700")
        sy = by + 76
        for ln in wrap_text(sub, bw - 36, 12.5)[:3]:
            body += _text(bx + 18, sy, ln, 12.5, _c(pal.ink_2), "400")
            sy += 18
        if i < n - 1:
            body += _arrow(bx + bw + 2, by + bh / 2, bx + bw + gap - 2, by + bh / 2,
                           _c(pal.primary_2), w=2)
    return body


def _fig_print3(x, y, w, h) -> str:
    """三步打印示意图：HUVEC 框架 → 中心填充 → Kupffer 布点。"""
    pal = _pal()
    body = ""
    n = 3
    cx_each = [x + w * (i + 0.5) / n for i in range(n)]
    cy = y + h * 0.44
    R = min(w / n, h) * 0.34
    steps = [
        ("Step 1", "HUVEC 内皮框架", "挤出打印六边形边框", "primary"),
        ("Step 2", "HepaRG 实质区", "中心填充打印 ≈100 μm/层", "primary_2"),
        ("Step 3", "Kupffer 布放", "预混或交界单独一层", "accent"),
    ]
    for i, (sname, ttl, desc, col) in enumerate(steps):
        cxc = cx_each[i]
        col_c = _pal().primary if col == "primary" else (_pal().primary_2 if col == "primary_2" else _pal().accent)
        # 外六边形
        body += _poly(_hex_pts(cxc, cy, R), fill="none", stroke=_c(col_c), sw=4)
        # 内六边形（Step2/3 填充）
        if i >= 1:
            body += _poly(_hex_pts(cxc, cy, R * 0.62), fill=_rgba_hex(col_c, 0.28))
        # 中心圆
        body += _circle(cxc, cy, R * 0.16, fill=_c(col_c))
        # Step3 Kupffer 点
        if i == 2:
            for k in range(4):
                ang = math.radians(60 * k + 20)
                body += _circle(cxc + R * 0.42 * math.cos(ang), cy + R * 0.42 * math.sin(ang),
                                4.5, fill=_c(pal.danger))
        # 标签
        body += _text(cxc, cy + R + 26, sname, 12, _c(col_c), "700", anchor="middle")
        body += _text(cxc, cy + R + 48, ttl, 14, _c(pal.ink), "700", anchor="middle")
        body += _text(cxc, cy + R + 70, desc, 11.5, _c(pal.ink_3), "400", anchor="middle")
        if i < n - 1:
            body += _arrow(cxc + R + 6, cy, cx_each[i + 1] - R - 6, cy, _c(pal.primary_2), w=2)
    return body


def _placeholder(x, y, w, h, label, note="文献首页占位 · 待粘贴论文首页截图") -> str:
    """文献首页占位：虚线框 + 页面图标 + 标签。"""
    pal = _pal()
    body = _dashed(x, y, w, h, _c(pal.line), sw=1.5, rx=6)
    # 页面图标
    ix, iy = x + w / 2 - 16, y + h / 2 - 26
    body += _rect(ix, iy, 32, 42, fill=_c(pal.bg_alt), stroke=_c(pal.primary_2), sw=1.5)
    body += _rect(ix + 8, iy + 8, 16, 2, fill=_c(pal.primary_2))
    body += _rect(ix + 8, iy + 14, 16, 2, fill=_c(pal.primary_2))
    body += _rect(ix + 8, iy + 20, 11, 2, fill=_c(pal.primary_2))
    body += _text(x + w / 2, y + h / 2 + 26, label, 12.5, _c(pal.ink_2), "600", anchor="middle")
    body += _text(x + w / 2, y + h / 2 + 47, note, 10.5, _c(pal.ink_3), "400", anchor="middle")
    return body


def _slot_rect(slot: str, x0: float = MARGIN, y0: float = CONTENT_TOP,
               cw: float = CONTENT_W, ch: float = CONTENT_BOTTOM - CONTENT_TOP) -> tuple:
    """各 slot 在内容区的矩形 (x, y, w, h)。ch=526。"""
    half = (cw - 24) / 2
    return {
        "full":   (x0, y0, cw, 300),
        "hero":   (x0, y0, cw, 220),
        "top":    (x0, y0, cw, 220),
        "bottom": (x0, y0 + ch - 220, cw, 220),
        "left":   (x0, y0, half, 420),
        "right":  (x0 + half + 24, y0, half, 420),
        "thumb":  (x0, y0, 280, 200),
    }.get(slot, (x0, y0, cw, 300))


def _fig_placeholder(x, y, w, h, label, note="内容图占位 · 后续贴真实图表"):
    """图槽内的虚线占位：虚线框 + 图片图标 + 标签（图留空，用户自己贴真图）。"""
    pal = _pal()
    body = _dashed(x, y, w, h, _c(pal.ink_3), sw=1.5, rx=6)
    # 图片图标（山+日）
    icx, icy = x + w / 2, y + h / 2 - 16
    body += _rect(icx - 20, icy - 13, 40, 26, fill="none", stroke=_c(pal.primary_2), sw=1.5, rx=3)
    body += _poly(f"{icx-13:.0f},{icy+4} {icx-4:.0f},{icy-8} {icx+3:.0f},{icy} {icx+10:.0f},{icy-6} {icx+13:.0f},{icy+4}",
                  fill=_c(pal.primary_2))
    body += _circle(icx + 12, icy - 6, 2.6, fill=_c(pal.accent))
    body += _text(x + w / 2, y + h / 2 + 30, "此处贴图", 13.5, _c(pal.ink_2), "600", anchor="middle")
    body += _text(x + w / 2, y + h / 2 + 51, note, 10.5, _c(pal.ink_3), "400", anchor="middle")
    return body


def _fig_slot(x, y, w, h, fig, idx=0) -> str:
    """内容图槽：统一 = 卡片(fid 阴影) + 顶部色条 + 图区 + 图号/图注。
    决策：src 可解析 → 真实 <image>；embed → data-URI；否则 → 虚线占位（图留空）。
    filter 只放卡片 rect；<image> 用 clip-path，不与其共存 filter。"""
    pal = _pal()
    if not isinstance(fig, dict):
        fig = {}
    label = fig.get("label", "")
    caption = fig.get("caption", "")
    src = fig.get("src", "")
    embed = fig.get("embed", "")
    bar = _c(pal.primary_2)
    accent = _accent_color(fig.get("bar", "primary"))
    if accent:
        bar = _c(accent)

    card = _card(x, y, w, h, fill=_c(pal.card), stroke=_c(pal.line), rx=0, fid="sh-card")
    strip = _rect(x, y, w, 5, fill=bar)
    img_y = y + 10
    img_h = h - 10 - 28
    img_w = w - 20

    img_body = ""
    use_image = False
    # 1) src：相对 "images/xxx.png"（放 out/images/），SVG 在 svg_output/ → href 前缀 "../"
    if src:
        href = src if src.startswith("../") or src.startswith("/") else ("../" + src if src.startswith("images/") else src)
        img_body = (f'<clipPath id="fig-clip-{idx}"><rect x="{x+10}" y="{img_y}" '
                    f'width="{img_w}" height="{img_h}" rx="6"/></clipPath>'
                    f'<image x="{x+10}" y="{img_y}" width="{img_w}" height="{img_h}" '
                    f'href="{esc(href)}" preserveAspectRatio="xMidYMid slice" '
                    f'clip-path="url(#fig-clip-{idx})"/>')
        use_image = True
    elif embed:
        img_body = (f'<clipPath id="fig-clip-{idx}"><rect x="{x+10}" y="{img_y}" '
                    f'width="{img_w}" height="{img_h}" rx="6"/></clipPath>'
                    f'<image x="{x+10}" y="{img_y}" width="{img_w}" height="{img_h}" '
                    f'href="{esc(embed)}" preserveAspectRatio="xMidYMid slice" '
                    f'clip-path="url(#fig-clip-{idx})"/>')
        use_image = True
    # 2) 内容化示意图（无真实图但内容是流程/结构时，SVG 画出来填实版面）
    diagram = fig.get("diagram")
    if not use_image and diagram:
        if isinstance(diagram, str) and diagram.lower() == "hex":
            img_body = _fig_hexlobule(x + 10, img_y, img_w, img_h)
            use_image = True
        elif isinstance(diagram, dict) and diagram.get("type") == "process":
            img_body = _fig_process(x + 10, img_y, img_w, img_h, diagram.get("steps", []))
            use_image = True
    if not use_image:
        img_body = _fig_placeholder(x + 10, img_y, img_w, img_h, label)

    # 图区描边（盖在图上，统一边线）
    frame = _rect(x + 10, img_y, img_w, img_h, fill="none", stroke=_c(pal.line), sw=1, rx=6) if use_image else ""
    cap_y = y + h - 12
    lab = _text(x + 14, cap_y, label, 12, bar, "600") if label else ""
    cap = _text(x + w - 14, cap_y, caption, 10.5, _c(pal.ink_3), "400", anchor="end") if caption else ""

    return _g(card + strip + img_body + frame + lab + cap,
              gid=f"fig-slot-{idx}", bounds=(x, y, w, h))


# ================================================================ 布局（图）
def _flow(s: dict) -> str:
    """横向流程布局 + 右侧要点。"""
    pal = _pal()
    body = _page_head(s.get("title", ""), s.get("kicker", ""), s.get("accent", ""))
    c = s.get("content", {})
    steps = c.get("steps", [])
    # 流程图占据上方 3/5
    body += _fig_flow(MARGIN, CONTENT_TOP, CONTENT_W, 230, steps)
    # 下方要点区
    notes = c.get("notes", [])
    if notes:
        y = CONTENT_TOP + 260
        body += _rect(MARGIN, y, 56, 3, fill=_c(_accent_color("primary")))
        body += _text(MARGIN, y + 20, c.get("notes_title", "要点"), 14.5, _c(pal.ink), "700")
        ny = y + 52
        for nt in notes[:5]:
            body += _rect(MARGIN, ny - 9, 6, 6, fill=_c(_pal().primary))
            for ln in wrap_text(nt, CONTENT_W - 40, 13):
                body += _text(MARGIN + 20, ny, ln, 13, _c(pal.ink_2), "400")
                ny += 21
            ny += 4
    fn = c.get("footnote", "")
    if fn:
        body += _text(MARGIN, CONTENT_BOTTOM + 8, fn, 11, _c(pal.ink_3), "400")
    return body


def _flow_table(s: dict) -> str:
    """流程示意图（上）+ 表格（下）组合页。"""
    pal = _pal()
    body = _page_head(s.get("title", ""), s.get("kicker", ""), s.get("accent", ""))
    c = s.get("content", {})
    steps = c.get("steps", [])
    body += _fig_flow(MARGIN, CONTENT_TOP, CONTENT_W, 180, steps)
    cap = c.get("caption", "")
    if cap:
        body += _text(MARGIN, CONTENT_TOP + 200, cap, 13, _c(pal.primary_2), "600")
    fy = CONTENT_TOP + 220
    cols = c.get("columns", [])
    rows = c.get("rows", [])
    fh = min(200, 38 + len(rows) * 30)
    meta = {
        "x": MARGIN, "y": fy, "width": CONTENT_W, "height": fh,
        "columns": cols, "rows": _native_rows(rows, c.get("highlight_row")),
        "header_rows": 1 if cols else 0, "column_widths": [1.0] * len(cols) if cols else [],
        "style": _table_style(),
        "strict_grid": True,
    }
    meta_xml = esc(json.dumps(meta, ensure_ascii=False))
    body += (f'<g data-pptx-replace-with="table" data-pptx-bounds="{MARGIN} {fy} {CONTENT_W} {fh}">'
             f'<metadata type="application/json">{meta_xml}</metadata>'
             f'{_table_fallback(MARGIN, fy, CONTENT_W, fh, cols, rows, c.get("highlight_row"))}'
             f'</g>')
    fn = c.get("footnote", "")
    if fn:
        body += _text(MARGIN, CONTENT_BOTTOM + 8, fn, 11, _c(pal.ink_3), "400")
    return body


def _chart_table(s: dict) -> str:
    """原生柱状图（左，阴影卡片）+ 数据表（右）组合页。"""
    pal = _pal()
    body = _page_head(s.get("title", ""), s.get("kicker", ""), s.get("accent", ""))
    c = s.get("content", {})
    data = c.get("data", [])
    target = c.get("target")
    highlight = c.get("highlight", 1)
    unit = c.get("unit", "kPa")
    # 左图：阴影卡片在前（文档序=层序在后），chart marker 无 filter/opacity
    cw = CONTENT_W * 0.52
    cx, cy, chh = MARGIN, CONTENT_TOP + 10, 400
    body += _card(cx, cy, cw, chh, fill=_c(pal.card), stroke=_c(pal.line), rx=0, fid="sh-card")
    body += _fig_barchart_native(cx + 12, cy + 10, cw - 24, chh - 20,
                                 data, target, highlight, unit,
                                 title=c.get("chart_title", ""))
    # 右表
    rx = MARGIN + CONTENT_W * 0.52 + 30
    rw = CONTENT_W - CONTENT_W * 0.52 - 30
    cols = c.get("columns", [])
    rows = c.get("rows", [])
    caption = c.get("caption", "")
    body += _text(rx, CONTENT_TOP + 24, caption, 13, _c(pal.primary_2), "600")
    fy = CONTENT_TOP + 42
    fh = min(410, 38 + len(rows) * 34)
    meta = {
        "x": rx, "y": fy, "width": rw, "height": fh,
        "columns": cols, "rows": _native_rows(rows, c.get("highlight_row")),
        "header_rows": 1 if cols else 0, "column_widths": [1.0] * len(cols) if cols else [],
        "style": _table_style(11.5),
        "strict_grid": True,
    }
    meta_xml = esc(json.dumps(meta, ensure_ascii=False))
    body += (f'<g data-pptx-replace-with="table" data-pptx-bounds="{rx} {fy} {rw} {fh}">'
             f'<metadata type="application/json">{meta_xml}</metadata>'
             f'{_table_fallback(rx, fy, rw, fh, cols, rows, c.get("highlight_row"))}'
             f'</g>')
    # 右栏下半段：结论 callout + 读图提示（对齐左图底部）
    conclusion = c.get("conclusion", "")
    if conclusion:
        cy0 = fy + fh + 22
        body += _card(rx, cy0, rw, 108, fill=_c(pal.bg_alt), stroke=_c(pal.accent), sw=1, rx=0)
        body += _rect(rx, cy0, 5, 108, fill=_c(pal.accent))
        body += _text(rx + 18, cy0 + 26, "结论", 13, _c(pal.accent), "700")
        cty = cy0 + 48
        for ln in wrap_text(conclusion, rw - 36, 14)[:3]:
            body += _text(rx + 18, cty, ln, 14, _c(pal.ink), "600")
            cty += 22
    read = c.get("read", "")
    if read:
        ry0 = fy + fh + 148
        body += _card(rx, ry0, rw, 54, fill=_c(pal.card), stroke=_c(pal.line), rx=0, fid="sh-node")
        body += _circle(rx + 16, ry0 + 21, 4, fill=_c(pal.primary))
        tyy = ry0 + 22
        for ln in wrap_text(read, rw - 40, 13)[:3]:
            body += _text(rx + 30, tyy, ln, 13, _c(pal.ink_2), "400")
            tyy += 18
    fn = c.get("footnote", "")
    if fn:
        body += _text(MARGIN, CONTENT_BOTTOM + 8, fn, 11, _c(pal.ink_3), "400")
    return body


def _hex(s: dict) -> str:
    """六边形肝小叶示意图 + 右侧标注。"""
    pal = _pal()
    body = _page_head(s.get("title", ""), s.get("kicker", ""), s.get("accent", ""))
    c = s.get("content", {})
    # 左图
    body += _fig_hexlobule(MARGIN - 20, CONTENT_TOP - 6, CONTENT_W * 0.50, 480, c)
    # 右标注区
    rx = MARGIN + CONTENT_W * 0.50 + 16
    rw = CONTENT_W - CONTENT_W * 0.50 - 16
    items = c.get("items", [])
    y = CONTENT_TOP + 26
    for it in items:
        bar = it.get("bar", "primary")
        body += _rect(rx, y - 10, 5, 40, fill=_c(_accent_color(bar)))
        body += _text(rx + 20, y, it.get("label", ""), 15.5, _c(pal.ink), "700")
        dy = y + 24
        for ln in wrap_text(it.get("body", ""), rw - 40, 12.5)[:4]:
            body += _text(rx + 20, dy, ln, 12.5, _c(pal.ink_2), "400")
            dy += 19
        y = dy + 18
    fn = c.get("footnote", "")
    if fn:
        body += _text(MARGIN, CONTENT_BOTTOM + 8, fn, 11, _c(pal.ink_3), "400")
    return body


def _print3(s: dict) -> str:
    """三步打印示意图 + 下方参数要点。"""
    pal = _pal()
    body = _page_head(s.get("title", ""), s.get("kicker", ""), s.get("accent", ""))
    c = s.get("content", {})
    body += _fig_print3(MARGIN, CONTENT_TOP, CONTENT_W, 250)
    notes = c.get("notes", [])
    if notes:
        y = CONTENT_TOP + 286
        body += _rect(MARGIN, y, 56, 3, fill=_c(_accent_color("primary")))
        body += _text(MARGIN, y + 20, c.get("notes_title", "要点"), 14.5, _c(pal.ink), "700")
        ny = y + 52
        for nt in notes[:6]:
            body += _rect(MARGIN, ny - 9, 6, 6, fill=_c(_pal().primary))
            for ln in wrap_text(nt, CONTENT_W - 40, 13):
                body += _text(MARGIN + 20, ny, ln, 13, _c(pal.ink_2), "400")
                ny += 21
            ny += 4
    fn = c.get("footnote", "")
    if fn:
        body += _text(MARGIN, CONTENT_BOTTOM + 8, fn, 11, _c(pal.ink_3), "400")
    return body


def _refs(s: dict) -> str:
    """参考文献页：文献首页占位框（留空待粘贴）。"""
    pal = _pal()
    body = _page_head(s.get("title", "参考文献"), s.get("kicker", "REFERENCES"), s.get("accent", ""))
    items = s.get("content", {}).get("items", [])
    cols = 2
    gap = 24
    cw = (CONTENT_W - gap) / 2
    ch = 150
    gy = gap
    for i, it in enumerate(items):
        r, cc = divmod(i, cols)
        x = MARGIN + cc * (cw + gap)
        y = CONTENT_TOP + r * (ch + gy)
        body += _placeholder(x, y, cw, ch, it.get("label", ""), it.get("note", "文献首页占位 · 待粘贴"))
    body += _text(MARGIN, CONTENT_BOTTOM + 10,
                  "注：把关键论文首页截图粘贴到对应占位框，即可补齐引用图。", 11, _c(pal.ink_3), "400", italic=True)
    return body


def _timeline(s: dict) -> str:
    """全工艺时间轴（高密度版）：顶部阶段统计条 + 编号节点 + 阶段卡片（名称/时长/描述）
    + 末节点里程碑（橙环+批注）+ 进度标签。底部合计条。"""
    pal = _pal()
    body = _page_head(s.get("title", ""), s.get("kicker", ""), s.get("accent", ""))
    c = s.get("content", {})
    stages = c.get("stages", [])
    n = len(stages)
    # 顶部阶段统计条（填满 y=142-190 空白）；轴置于内容区中央
    summary = c.get("summary", "")
    if summary:
        body += _card(MARGIN, CONTENT_TOP, CONTENT_W, 40, fill=_c(pal.bg_alt), stroke=_c(pal.line), rx=0)
        body += _rect(MARGIN, CONTENT_TOP, 4, 40, fill=_c(pal.primary))
        body += _text(MARGIN + 18, CONTENT_TOP + 26, summary, 13, _c(pal.ink), "600")
        axis_y = 404
    else:
        axis_y = 384
    x0, x1 = MARGIN + 24, W - MARGIN - 24
    body += _rect(x0, axis_y, x1 - x0, 3, fill=_c(pal.primary_2))
    # 轴上进度箭头（右端小箭头）
    body += _poly(f"{x1:.0f},{axis_y} {x1-14:.0f},{axis_y-6} {x1-14:.0f},{axis_y+6}",
                  fill=_c(pal.primary_2))
    if n == 0:
        return body
    step = (x1 - x0) / n
    card_w = min(300, step * 0.9)
    for i, st in enumerate(stages):
        nx = x0 + step * (i + 0.5)
        up = i % 2 == 0
        last = i == n - 1
        # 节点：末节点 = 里程碑（橙环），其余 = 蓝色实心
        if last:
            body += _circle(nx, axis_y, 13, fill=_c(pal.bg), stroke=_c(pal.accent), sw=3)
            body += _circle(nx, axis_y, 7, fill=_c(pal.accent))
        else:
            body += _circle(nx, axis_y, 9, fill=_c(pal.primary), stroke=_c(pal.bg), sw=2)
        # 阶段卡片（上/下交替，描述入卡，填满轴两侧空白）
        if up:
            card_y = axis_y - 148
        else:
            card_y = axis_y + 22
        body += _card(nx - card_w / 2, card_y, card_w, 124, fill=_c(pal.card), stroke=_c(pal.line), rx=0, fid="sh-card")
        body += _rect(nx - card_w / 2, card_y, card_w, 4, fill=_c(pal.accent) if last else _c(pal.primary))
        # 顶部行：编号（红色小字，去 chip）+ 进度标签（红色小字，去 chip）
        tag = f"{i+1:02d}"
        body += _text(nx - card_w / 2 + 12, card_y + 27, tag,
                      12, _c(pal.primary), "800", family=FONT_MONO)
        if st.get("progress"):
            ptag = st.get("progress")
            body += _text(nx + card_w / 2 - 12, card_y + 27, ptag,
                          11, _c(pal.primary), "700", anchor="end")
        # 阶段名 + 时长（同一行，左对齐，时长接在名称后）
        stxt = st.get("stage", "")
        body += _text(nx - card_w / 2 + 12, card_y + 36, stxt, 14.5, _c(pal.ink), "700")
        days = st.get("days", "")
        if days:
            nw = sum(14.5 if ord(ch2) > 0x2E80 else 14.5 * 0.55 for ch2 in stxt)
            body += _text(nx - card_w / 2 + 12 + nw + 14, card_y + 36, "· " + days, 11, _c(pal.primary_2), "600")
        body += _rect(nx - card_w / 2 + 12, card_y + 46, card_w - 24, 1, fill=_c(pal.line))
        dy = card_y + 66
        for ln in wrap_text(st.get("desc", ""), card_w - 28, 13)[:3]:
            body += _text(nx - card_w / 2 + 12, dy, ln, 13, _c(pal.ink_2), "400")
            dy += 18.5
        if last:
            # 里程碑标注放到节点下方（轴线以下空白区），避开上卡底
            body += _text(nx, axis_y + 28, "★ 里程碑", 11, _c(pal.accent), "700", anchor="middle")
        # 节点到卡片的小竖线（贴齐卡底/卡顶，不留空隙）
        if up:
            # 上卡底 = axis_y - 148 + 124 = axis_y - 24
            body += _line_seg(nx, axis_y - 24, nx, axis_y - 9, _c(pal.line), w=1.5)
        else:
            body += _line_seg(nx, axis_y + 9, nx, axis_y + 22, _c(pal.line), w=1.5)
    total = c.get("total", "")
    if total:
        ty = CONTENT_BOTTOM - 46
        body += _card(MARGIN, ty, CONTENT_W, 34, fill=_c(pal.bg_alt), stroke=_c(pal.accent), sw=1, rx=0)
        body += _circle(MARGIN + 18, ty + 17, 5, fill=_c(pal.accent))
        body += _text(MARGIN + 32, ty + 22, total, 13, _c(pal.ink), "600")
    fn = c.get("footnote", "")
    if fn:
        body += _text(MARGIN, CONTENT_BOTTOM + 8, fn, 11, _c(pal.ink_3), "400")
    return body


def _quote(s: dict) -> str:
    pal = _pal()
    body = _page_head(s.get("kicker", "观点"), s.get("title", "设计取舍"), s.get("accent", "accent"))
    c = s.get("content", {})
    text = c.get("text", "")
    body += _rect(MARGIN, 230, 8, 150, fill=_c(_accent_color("accent")))
    lines = wrap_text(text, CONTENT_W - 120, 30)
    y = 290
    for ln in lines:
        body += _text(MARGIN + 40, y, ln, 30, _c(pal.ink), "700", family=FONT_SERIF)
        y += 50
    src = c.get("source", "")
    if src:
        body += _text(MARGIN + 40, y + 24, "—— " + src, 15, _c(pal.ink_3), "400")
    extra = c.get("extra", "")
    if extra:
        ey = 470
        for ln in wrap_text(extra, CONTENT_W - 100, 13.5):
            body += _text(MARGIN + 40, ey, ln, 13.5, _c(pal.ink_2), "400")
            ey += 22
    return body


def _takeaway(s: dict) -> str:
    """结论页：直角结论卡（编号纯红字）+ 底部"下一步"条，网格垂直居中填满内容区。"""
    pal = _pal()
    body = _page_head(s.get("title", "结论与下一步"), s.get("kicker", "TAKEAWAY"), s.get("accent", "accent"))
    c = s.get("content", {})
    pts = c.get("points", [])
    cols = 2
    cw = (CONTENT_W - 26) / cols
    y0 = CONTENT_TOP
    closing = c.get("closing", "")
    bar_y = CONTENT_BOTTOM - 42 if closing else CONTENT_BOTTOM - 8
    # 卡高自适应填满「页头→结语条」之间，避免底部大片空白（评审 P2：116→动态填充）
    rows_n = (len(pts[:4]) + cols - 1) // cols
    bar_area = bar_y - 18
    ch = min(170, max(116, int((bar_area - 16 - y0 - rows_n * 24) // rows_n))) if rows_n else 116
    grid_h = rows_n * (ch + 24) - 24
    grid_top = y0 + max(0, (bar_area - y0 - grid_h) // 2)
    for i, pt in enumerate(pts[:4]):
        r, cc = divmod(i, cols)
        x = MARGIN + cc * (cw + 26)
        y = grid_top + r * (ch + 24)
        body += _card(x, y, cw, ch, fill=_c(pal.bg_alt), stroke=_c(pal.line), rx=0, fid="sh-card")
        body += _rect(x, y, cw, 5, fill=_c(pal.accent))
        body += _text(x + 22, y + 34, f"{i+1}", 20, _c(pal.primary), "800", family=FONT_MONO)
        body += _rect(x + 22, y + 44, cw - 44, 1, fill=_c(pal.line))
        yy = y + 62
        for ln in wrap_text(pt, cw - 44, 14)[:3]:
            body += _text(x + 22, yy, ln, 14, _c(pal.ink), "400")
            yy += 21
    if closing:
        body += _card(MARGIN, bar_y, CONTENT_W, 32, fill=_c(pal.bg_alt), stroke=_c(pal.accent), sw=1, rx=0)
        body += _circle(MARGIN + 18, bar_y + 16, 5, fill=_c(pal.accent))
        body += _text(MARGIN + 32, bar_y + 21, closing, 14, _c(pal.ink), "600")
    return body


# ================================================================ 布局（v2.1 新增）
def _two_col(s: dict) -> str:
    """A/B 双栏对比：col_a / col_b{heading, bullets}，中间金色分隔。"""
    pal = _pal()
    body = _page_head(s.get("title", ""), s.get("kicker", ""), s.get("accent", ""))
    c = s.get("content", {})
    gap = 44
    cw = (CONTENT_W - gap) / 2
    x0, y0 = MARGIN, CONTENT_TOP + 6
    ch = CONTENT_BOTTOM - 40 - y0
    for ci, key in enumerate(("col_a", "col_b")):
        col = c.get(key, {})
        x = x0 + ci * (cw + gap)
        heading = col.get("heading", "")
        bullets = col.get("bullets", [])
        body += _card(x, y0, cw, ch, fill=_c(pal.card), stroke=_c(pal.line), rx=0, fid="sh-card")
        body += _rect(x, y0, cw, 5, fill=_c(pal.primary) if ci == 0 else _c(pal.accent))
        body += _text(x + 22, y0 + 40, heading, 17, _c(pal.ink), "700")
        body += _rect(x + 22, y0 + 54, 40, 3, fill=_c(pal.accent))
        by = y0 + 84
        for b in bullets[:6]:
            body += _circle(x + 30, by - 5, 4, fill=_c(pal.primary) if ci == 0 else _c(pal.accent))
            for ln in wrap_text(b, cw - 64, 13):
                body += _text(x + 46, by, ln, 13, _c(pal.ink_2), "400")
                by += 20
            by += 5
        # 中部金色分隔竖线
        if ci == 0:
            body += _rect(x + cw + 18, y0, 8, ch, fill=_c(pal.accent))
    figs = c.get("figures", [])
    if figs:
        fx, fy, fw, fh = _slot_rect("bottom")
        body += _fig_slot(fx, fy, fw, min(fh, CONTENT_BOTTOM - 40 - fy), figs[0], idx=0)
    fn = c.get("footnote", "")
    if fn:
        body += _text(MARGIN, CONTENT_BOTTOM + 8, fn, 11, _c(pal.ink_3), "400")
    return body


def _compare(s: dict) -> str:
    """A/B 判据矩阵（对标报奖 PPT 方案对比页）：
    左列 = 比较维度（编号 + 名称），右两列 = A/B 方案格。
    胜者格 = 浅蓝底 + 橙色描边 + "✓ 优"徽章；判据行可用 bar=accent 高亮。"""
    pal = _pal()
    body = _page_head(s.get("title", ""), s.get("kicker", ""), s.get("accent", ""))
    dims = s.get("content", {}).get("dimensions", [])
    x0, y0 = MARGIN, CONTENT_TOP + 6
    w = CONTENT_W
    gap = 14
    # 行数多时按内容区高度摊分行高，保证最后一行底部 ≤ CONTENT_BOTTOM（P0 修复）
    n = len(dims)
    rows_top = y0 + 40 + gap
    row_h = min(92, max(64, (CONTENT_BOTTOM - 8 - rows_top - (n - 1) * gap) // n))
    # 表头（A/B 表头对齐各自方案格左内边，评审 P2）
    body += _rect(x0, y0, w, 40, fill=_c(pal.primary_2))
    body += _text(x0 + 20, y0 + 26, "比较维度", 13, "#FFFFFF", "700")
    body += _text(x0 + w * 0.30 + 14, y0 + 26, "A 组", 13, "#FFFFFF", "700")
    body += _text(x0 + w * 0.635 + 14, y0 + 26, "B 组", 13, "#FFFFFF", "700")
    y = y0 + 40 + gap
    for i, d in enumerate(dims):
        body += _card(x0, y, w, row_h, fill=_c(pal.card), stroke=_c(pal.line), rx=0, fid="sh-node")
        # 维度块（左）
        num = f"0{i+1}"
        body += _text(x0 + 20, y + row_h * 0.44, num, 15, _c(pal.primary), "800", family=FONT_MONO)
        body += _text(x0 + 64, y + row_h * 0.44, d.get("name", ""), 14, _c(pal.ink), "700")
        sub = d.get("sub", "")
        if sub:
            for j, ln in enumerate(wrap_text(sub, w * 0.26 - 70, 11)[:1]):
                body += _text(x0 + 64, y + row_h * 0.72, ln, 11, _c(pal.ink_3), "400")
        # A / B 方案格（dual=判据行：两格同高亮 + 「双达标」徽章，评审 P2）
        dual = bool(d.get("dual"))
        for key, hx in (("a", 0.30), ("b", 0.635)):
            better = d.get("better", "") == key
            win = better or dual
            cell_x = x0 + w * hx
            cell_w = w * 0.335
            body += _card(cell_x, y + 12, cell_w, row_h - 24,
                          fill=_tint(pal.primary, 0.06) if win else _c(pal.card),
                          stroke=_c(pal.accent) if win else _c(pal.line),
                          sw=1.5 if win else 1, rx=0)
            val = d.get(key, "")
            tx = cell_x + 14
            for j, ln in enumerate(wrap_text(val, cell_w - 58, 13)[:3]):
                body += _text(tx, y + row_h / 2 - 22 + j * 17, ln, 13,
                              _c(pal.primary) if win else _c(pal.ink_2),
                              "700" if win else "400")
            if win:
                body += _text(cell_x + cell_w - 14, y + 24, "双达标" if dual else "✓ 优",
                              11.5, _c(pal.primary), "700", anchor="end")
        y += row_h + gap
    fn = s.get("content", {}).get("footnote", "")
    if fn:
        body += _text(MARGIN, CONTENT_BOTTOM + 8, fn, 11, _c(pal.ink_3), "400")
    return body


def _mindmap(s: dict) -> str:
    """思维导图：中心圆角节点 + elbow 折线 + 叶子 chips。SVG 只画几何，文字受检。"""
    pal = _pal()
    body = _page_head(s.get("title", ""), s.get("kicker", ""), s.get("accent", ""))
    c = s.get("content", {})
    center = c.get("center", "")
    branches = c.get("branches", [])
    cx, cy = MARGIN + 90, CONTENT_TOP + 210
    # 中心节点
    body += _card(MARGIN, CONTENT_TOP + 140, 180, 80, fill=_c(pal.primary), rx=0, fid="sh-lg")
    body += _text(MARGIN + 90, CONTENT_TOP + 184, center, 20, "#FFFFFF", "800", anchor="middle")
    # 分支：左侧 2 条，右侧其余
    left_n = 2
    n = len(branches)
    right_n = max(0, n - left_n)
    y_spans = [CONTENT_TOP + 70, CONTENT_TOP + 400]  # 左支起始
    y_span_r = [CONTENT_TOP + 30, CONTENT_TOP + 430] if right_n else []
    branch_w = 300
    for i, b in enumerate(branches):
        if i < left_n:
            side = "l"
            bx = MARGIN + 200
            by = y_spans[i]
            tx = MARGIN + 210
        else:
            side = "r"
            j = i - left_n
            bx = W - MARGIN - 300
            by = y_span_r[0] + j * (y_span_r[1] - y_span_r[0]) / max(right_n, 1)
            tx = bx + 10
        name = b.get("name", "")
        children = b.get("children", [])
        # elbow 折线（中心右/左边 → 分支节点）
        cex = MARGIN + 180 if side == "l" else MARGIN + 200
        if side == "l":
            body += _line_seg(MARGIN + 180, CONTENT_TOP + 180, MARGIN + 200, CONTENT_TOP + 180, _c(pal.line), w=2)
            body += _line_seg(MARGIN + 200, CONTENT_TOP + 180, MARGIN + 200, by + 22, _c(pal.line), w=2)
            body += _line_seg(MARGIN + 200, by + 22, bx + 10, by + 22, _c(pal.line), w=2)
        else:
            body += _line_seg(MARGIN + 260, CONTENT_TOP + 180, W - MARGIN - 300 - 20, CONTENT_TOP + 180, _c(pal.line), w=2)
            body += _line_seg(W - MARGIN - 300 - 20, CONTENT_TOP + 180, W - MARGIN - 300 - 20, by + 22, _c(pal.line), w=2)
            body += _line_seg(W - MARGIN - 300 - 20, by + 22, bx, by + 22, _c(pal.line), w=2)
        # 分支节点卡
        bh = 26 + 22 * max(len(children), 1)
        body += _card(bx, by, branch_w, bh, fill=_c(pal.bg_alt), stroke=_c(pal.line), rx=0, fid="sh-node")
        body += _text(tx, by + 24, name, 14, _c(pal.ink), "700")
        cy_leaf = by + 48
        for ch in children[:4]:
            body += _circle(tx + 6, cy_leaf - 4, 3, fill=_c(pal.accent))
            body += _text(tx + 18, cy_leaf, ch, 12, _c(pal.ink_2), "400")
            cy_leaf += 20
    figs = c.get("figures", [])
    if figs:
        fx, fy, fw, fh = _slot_rect("bottom")
        body += _fig_slot(fx, fy, fw, min(fh, CONTENT_BOTTOM - 40 - fy), figs[0], idx=0)
    return body


def _fig_table_text(s: dict) -> str:
    """图槽(左) + 要点(右上) + 原生表(右下)——一页三件套。"""
    pal = _pal()
    body = _page_head(s.get("title", ""), s.get("kicker", ""), s.get("accent", ""))
    c = s.get("content", {})
    figs = c.get("figures", [])
    # 图槽（左，约 45% 宽）
    lw = CONTENT_W * 0.45
    fx, fy = MARGIN, CONTENT_TOP + 6
    fh = CONTENT_BOTTOM - 40 - fy
    if figs:
        body += _fig_slot(fx, fy, lw, fh, figs[0], idx=0)
    rx = MARGIN + lw + 28
    rw = CONTENT_W - lw - 28
    # 右下：原生表（按行数算高，紧贴脚注上方，避免 fallback 溢出 bounds）
    cols = c.get("columns", [])
    rows = c.get("rows", [])
    row_h = 26
    fh2 = min(210, 38 + len(rows) * row_h) if rows else 0
    # 表底比面板底再收 8px：避免末行边框与浅底面板下缘贴合（评审 P2）
    fy2 = CONTENT_BOTTOM - 16 - fh2 if rows else CONTENT_BOTTOM - 8
    # 右上：intro + 要点 + 表格标题 + 表格，全部放进一张贯穿到底的浅底面板
    # （面板底=内容底，表格画在面板上 → 视觉上成组，不再悬浮）
    pts = c.get("points", [])
    intro = c.get("intro", "")
    card_top = CONTENT_TOP + 4
    card_bot = CONTENT_BOTTOM - 8
    body += _card(rx - 4, card_top, rw + 4, card_bot - card_top,
                  fill=_c(pal.bg_alt), stroke=_c(pal.line), rx=0, fid="sh-card")
    body += _rect(rx - 4, card_top, rw + 4, 4, fill=_c(pal.accent))
    yy = card_top + 20
    if intro:
        for ln in wrap_text(intro, rw - 24, 14):
            body += _text(rx + 16, yy, ln, 14, _c(pal.ink_2), "500")
            yy += 22
        yy += 10
    n_pts = min(len(pts), 4)
    if n_pts:
        pts_bot = (fy2 - 46) if (rows and cols) else (card_bot - 14)
        remain = max(0, pts_bot - yy)
        slot = max(34, remain / n_pts)
        max_body = 2 if slot >= 62 else 1
        for pt in pts[:n_pts]:
            body += _circle(rx + 20, yy + 15, 4, fill=_c(pal.accent))
            for ln in wrap_text(pt.get("lead", ""), rw - 52, 13.5)[:1]:
                body += _text(rx + 34, yy + 18, ln, 13.5, _c(pal.ink), "700")
            bly = yy + 42
            for ln in wrap_text(pt.get("body", ""), rw - 52, 13)[:max_body]:
                body += _text(rx + 34, bly, ln, 13, _c(pal.ink_3), "400")
                bly += 18
            yy += slot
    if rows and cols:
        cap = c.get("caption", "")
        if cap:
            body += _text(rx, fy2 - 16, cap, 13, _c(pal.primary_2), "600")
        meta = {
            "x": rx, "y": fy2, "width": rw, "height": fh2,
            "columns": cols, "rows": _native_rows(rows, c.get("highlight_row")),
            "header_rows": 1, "column_widths": [1.0] * len(cols) if cols else [],
            "style": _table_style(11),
            "strict_grid": True,
        }
        meta_xml = esc(json.dumps(meta, ensure_ascii=False))
        body += (f'<g data-pptx-replace-with="table" data-pptx-bounds="{int(rx)} {int(fy2)} {int(rw)} {int(fh2)}">'
                 f'<metadata type="application/json">{meta_xml}</metadata>'
                 f'{_table_fallback(rx, fy2, rw, fh2, cols, rows, c.get("highlight_row"))}'
                 f'</g>')
    fn = c.get("footnote", "")
    if fn:
        body += _text(MARGIN, CONTENT_BOTTOM + 8, fn, 11, _c(pal.ink_3), "400")
    return body


def _fig_points(s: dict) -> str:
    """问题弧线页（对标报奖 PPT 研究背景页）：
    ├─ 顶部"关键证据带"(verdict.stat 大数字 + 判断句)
    ├─ 中部"瓶颈矩阵"(points[]，2 列，每卡带 stat 数字徽章)
    ├─ 底部"本课题对策"条(solution，橙色，箭头从瓶颈指向对策)
    └─ 右侧竖条图槽(figures[] slot=right，待贴真实机制图)
    """
    pal = _pal()
    body = _page_head(s.get("title", ""), s.get("kicker", ""), s.get("accent", ""))
    c = s.get("content", {})
    figs = c.get("figures", [])
    pts = c.get("points", [])
    verdict = c.get("verdict", {}) if isinstance(c.get("verdict", {}), dict) else {}
    solution = c.get("solution", "")

    # ---- 图槽：优先右侧竖条（窄），把版面让给内容
    lx, lw = MARGIN, CONTENT_W
    if figs:
        fig = figs[0]
        slot = fig.get("slot", "right") if isinstance(fig, dict) else "right"
        if slot in ("left", "right"):
            fw = min(300, CONTENT_W * 0.28)
            fx = (W - MARGIN - fw) if slot == "right" else MARGIN
            fy = CONTENT_TOP
            fh = CONTENT_BOTTOM - 6 - CONTENT_TOP
            body += _fig_slot(fx, fy, fw, fh, fig, idx=0)
            lx = MARGIN if slot == "right" else MARGIN + fw + 26
            lw = CONTENT_W - fw - 26
    yy = CONTENT_TOP

    # ---- 1) 关键证据带（大数字 + 判断）
    if verdict:
        vh = 78
        body += _card(lx, yy, lw, vh, fill=_c(pal.bg_alt), stroke=_c(pal.line), rx=0, fid="sh-card")
        body += _rect(lx, yy, 6, vh, fill=_c(pal.accent))
        stat = str(verdict.get("stat", ""))
        stx = 0
        if stat:
            body += _text(lx + 26, yy + vh / 2 + 12, stat, 32, _c(pal.accent), "800")
            stx = 26 + max(70, len(stat) * 19)
        vtext = verdict.get("text", "")
        for j, ln in enumerate(wrap_text(vtext, lw - 46 - stx, 15.5)[:2]):
            body += _text(lx + 26 + stx, yy + 28 + j * 22, ln, 15.5, _c(pal.ink), "700")
        body += _text(lx + 26 + stx, yy + vh - 22, verdict.get("tag", ""), 11,
                      _c(pal.ink_3), "400") if verdict.get("tag") else ""
        yy += vh + 18

    # ---- 2) 瓶颈矩阵（2 列卡，stat 徽章 + lead + body）
    n = min(len(pts), 8)
    if n:
        cols = 2
        gap = 18
        cw = (lw - gap) / cols
        rows_n = (n + cols - 1) // cols
        bottom = CONTENT_BOTTOM - (60 if solution else 8)
        ch = min(150, (bottom - yy - gap * (rows_n - 1)) / rows_n)
        for i, pt in enumerate(pts[:n]):
            r, cc = divmod(i, cols)
            x = lx + cc * (cw + gap)
            y = yy + r * (ch + gap)
            body += _card(x, y, cw, ch, fill=_c(pal.card), stroke=_c(pal.line), rx=0, fid="sh-card")
            body += _rect(x, y, cw, 4, fill=_c(_accent_color(pt.get("bar", "primary"))))
            st = str(pt.get("stat", ""))
            if st:
                body += _text(x + cw - 22, y + 34, st, 21, _c(pal.primary), "800", anchor="end")
            lead = pt.get("lead", "")
            tyy = y + 30 if st else y + 44
            for j, ln in enumerate(wrap_text(lead, cw - (90 if st else 30), 14.5)[:1]):
                body += _text(x + 20, tyy, ln, 14.5, _c(pal.ink), "700")
                tyy += 19
            by = tyy + 6
            for ln in wrap_text(pt.get("body", ""), cw - 40, 13)[:3]:
                body += _text(x + 20, by, ln, 13, _c(pal.ink_2), "400")
                by += 18
            # 支撑数据行（细分隔线 + 小字，填实卡底）
            sub = pt.get("sub", "")
            if sub:
                body += _rect(x + 20, y + ch - 34, cw - 40, 1, fill=_c(pal.line))
                sy = y + ch - 21
                for ln in wrap_text(sub, cw - 40, 11)[:2]:
                    body += _text(x + 20, sy, ln, 11, _c(pal.ink_3), "400")
                    sy += 16
        yy += rows_n * (ch + gap) + gap

    # ---- 3) 本课题对策条（箭头从矩阵指向对策）
    if solution:
        sh = 52
        body += _arrow(lx + 30, yy - 10, lx + 30, yy + 2, _c(pal.accent), w=2)
        body += _card(lx, yy, lw, sh, fill=_c(pal.card), stroke=_c(pal.accent), sw=1, rx=0)
        body += _rect(lx, yy, 6, sh, fill=_c(pal.accent))
        body += _text(lx + 22, yy + sh / 2 + 5, "本课题对策", 13, _c(pal.accent), "700")
        sxx = lx + 22 + 100
        for j, ln in enumerate(wrap_text(solution, lw - 130, 13.5)[:2]):
            body += _text(sxx, yy + 27 + j * 20, ln, 13.5, _c(pal.ink), "600")
        yy += sh + 4

    fn = c.get("footnote", "")
    if fn:
        body += _text(MARGIN, CONTENT_BOTTOM + 8, fn, 11, _c(pal.ink_3), "400")
    return body


def _research(s: dict) -> str:
    """研究内容/方案：目标条 + N 列方案卡 + 底部图槽。"""
    pal = _pal()
    body = _page_head(s.get("title", ""), s.get("kicker", ""), s.get("accent", ""))
    c = s.get("content", {})
    objective = c.get("objective", "")
    cols = c.get("columns", [])
    figs = c.get("figures", [])
    y = CONTENT_TOP
    if objective:
        body += _rect(MARGIN, y - 4, 6, 34, fill=_c(pal.accent))
        for ln in wrap_text("目标：" + objective, CONTENT_W - 60, 14.5):
            body += _text(MARGIN + 22, y + 16, ln, 14.5, _c(pal.ink), "700")
            y += 22
        y += 16
    n = len(cols)
    if n:
        gap = 22
        cw = (CONTENT_W - gap * (n - 1)) / n
        ch = 200
        for i, col in enumerate(cols):
            x = MARGIN + i * (cw + gap)
            body += _card(x, y, cw, ch, fill=_c(pal.card), stroke=_c(pal.line), rx=0, fid="sh-node")
            body += _rect(x, y, cw, 4, fill=_c(pal.primary))
            body += _text(x + 16, y + 30, f"0{i+1}", 13, _c(pal.primary), "800", family=FONT_MONO)
            body += _text(x + 52, y + 31, col.get("phase", ""), 15, _c(pal.ink), "700")
            items = col.get("items", [])[:4]
            n_it = max(len(items), 1)
            it_gap = 8
            area_bot = y + ch - 12
            it_h = max(24, (area_bot - (y + 88) - it_gap * (n_it - 1)) / n_it)
            by = y + 88
            for it in items:
                tyy = by + max(20, (it_h - 18) / 2 + 14)
                body += _text(x + 24, tyy + 2, "✓", 13, _c(pal.primary), "700")
                for ln in wrap_text(it, cw - 42, 13)[:2]:
                    body += _text(x + 40, tyy, ln, 13, _c(pal.ink_2), "400")
                    tyy += 18
                by += it_h + it_gap
    fy = y + 220 if n else y
    if figs:
        fh = CONTENT_BOTTOM - 40 - fy
        body += _fig_slot(MARGIN, fy, CONTENT_W, fh, figs[0], idx=0)
    fn = c.get("footnote", "")
    if fn:
        body += _text(MARGIN, CONTENT_BOTTOM + 8, fn, 11, _c(pal.ink_3), "400")
    return body


def _route(s: dict) -> str:
    """技术路线图（克制版，单一红色 + 白卡，去掉多色带头）：
    顶部总目标条 → 阶段头（白卡 + 红序号/顶条）+ 段内子卡 → 阶段间递进箭头 →
    底部闭环/交付条。"""
    pal = _pal()
    body = _page_head(s.get("title", ""), s.get("kicker", ""), s.get("accent", ""))
    c = s.get("content", {})
    cols = c.get("columns", [])
    n = len(cols)
    y = CONTENT_TOP
    # 顶部总目标条（血红，白字）
    obj = c.get("objective", "")
    if obj:
        body += _rect(MARGIN, y - 6, CONTENT_W, 46, rx=0, fill=_c(pal.primary))
        body += _text(MARGIN + 16, y + 18, "总目标", 12, "#F5C6C6", "700")
        for ln in wrap_text(obj, CONTENT_W - 120, 15)[:1]:
            body += _text(MARGIN + 100, y + 19, ln, 15, "#FFFFFF", "700")
        y += 40 + 26
    gap = 40
    cw = (CONTENT_W - gap * (n - 1)) / n
    hdr_h = 56
    closing = c.get("closing", "")
    # 底部闭环条预留：closing 顶在 ending_y，卡片区在 closing 上方留 10px
    ending_y = (CONTENT_BOTTOM - 10 - 26) if closing else CONTENT_BOTTOM
    ch = ending_y - 14 - (y + hdr_h)
    for i, col in enumerate(cols):
        x = MARGIN + i * (cw + gap)
        # 阶段头（白卡 + 红顶条 + 红序号 + 黑标题）
        body += _card(x, y, cw, hdr_h, fill=_c(pal.card), stroke=_c(pal.line), rx=0, fid="sh-node")
        body += _rect(x, y, cw, 4, fill=_c(pal.primary))
        body += _text(x + 22, y + 25, f"{i+1}", 15, _c(pal.primary), "800", family=FONT_MONO)
        body += _text(x + 50, y + 23, col.get("phase", ""), 14.5, _c(pal.ink), "800")
        body += _text(x + 50, y + 41, col.get("sub", ""), 11.5, _c(pal.ink_3), "400")
        # 列面板（浅底轨道）：阶段头下 → 闭环条上，把子卡串成连续轨道，杜绝"列中空白"
        body += _rect(x, y + hdr_h + 10, cw, ending_y - 10 - (y + hdr_h + 10),
                      fill=_c(pal.bg_alt), rx=0)
        # 段内子卡：按列内轨道高度均分（卡高上限 92），剩余空间均匀垫入
        # 卡片之间与轨道两端（by = track_top + pad + i*step），杜绝"列底空白"。
        track_top = y + hdr_h + 10
        track_h = ending_y - 10 - track_top
        items = col.get("items", [])[:5]
        n_it = max(len(items), 1)
        it_gap = 12
        it_h = min(92, max(46, (track_h - 8 - it_gap * (n_it - 1)) / n_it))
        it_used = n_it * it_h + (n_it - 1) * it_gap
        pad = max(0, (track_h - 8 - it_used) / (n_it + 1))
        step = it_h + it_gap + pad
        by = track_top + pad
        for it in items:
            body += _card(x, by, cw, it_h, fill=_c(pal.card), stroke=_c(pal.line), rx=0, fid="sh-node")
            body += _rect(x, by, cw, 4, fill=_c(pal.primary))
            tyy = by + it_h / 2 + 5
            for ln in wrap_text(it, cw - 28, 13)[:2]:
                body += _text(x + 18, tyy, ln, 13, _c(pal.ink_2), "500")
                tyy += 17.5
            by += step
        # 阶段间递进箭头 + 动词标签
        if i < n - 1:
            ax = x + cw + gap / 2
            body += _arrow(ax - 12, y + hdr_h + ch / 2, ax + 12, y + hdr_h + ch / 2,
                           _c(pal.primary_2), w=2.5)
            step_v = col.get("next", "")
            if step_v:
                body += _text(ax, y + hdr_h + ch / 2 + 22, step_v, 12, _c(pal.ink_3), "600", anchor="middle")
    # 底部闭环/交付条
    if closing:
        cy0 = ending_y
        body += _card(MARGIN, cy0, CONTENT_W, 26, fill=_c(pal.bg_alt), stroke=_c(pal.accent), sw=1, rx=0)
        body += _circle(MARGIN + 16, cy0 + 13, 5, fill=_c(pal.accent))
        body += _text(MARGIN + 30, cy0 + 17, closing, 13.5, _c(pal.ink), "600")
    fn = c.get("footnote", "")
    if fn:
        body += _text(MARGIN, CONTENT_BOTTOM + 8, fn, 11, _c(pal.ink_3), "400")
    return body


def _profile(s: dict) -> str:
    """答辩人简介：头像圆占位 + 姓名/头衔 + 学术标签 + 数据徽章。"""
    pal = _pal()
    body = _page_head(s.get("title", "答辩人简介"), s.get("kicker", "PROFILE"), s.get("accent", ""))
    c = s.get("content", {})
    # 左侧头像占位（虚线圆 + 人形剪影；中心下移/半径缩小，顶部避开页头发丝线 y=118）
    ax, ay = MARGIN + 60, CONTENT_TOP + 66
    ar = 80
    body += _circle(ax, ay, ar, fill=_c(pal.bg_alt), stroke=_c(pal.primary_2), sw=2, opacity=1)
    body += _dashed(ax - ar, ay - ar, ar * 2, ar * 2, _c(pal.primary_2), sw=1.5, rx=ar)
    body += _circle(ax, ay - 20, 24, fill="none", stroke=_c(pal.ink_3), sw=3)
    body += _poly(f"{ax-30:.0f},{ay+58} {ax-14:.0f},{ay+24} {ax:.0f},{ay+40} "
                  f"{ax+14:.0f},{ay+24} {ax+30:.0f},{ay+58}",
                  fill=_c(pal.ink_3))
    body += _text(ax, ay + ar + 26, "照片占位", 11, _c(pal.ink_3), "400", anchor="middle")
    # 右侧信息
    rx = MARGIN + 220
    rw = CONTENT_W - 220
    body += _text(rx, CONTENT_TOP + 60, c.get("name", ""), 30, _c(pal.ink), "800")
    body += _text(rx, CONTENT_TOP + 92, c.get("title", ""), 14, _c(pal.primary_2), "600")
    body += _text(rx, CONTENT_TOP + 116, c.get("affil", ""), 13, _c(pal.ink_3), "400")
    # 学术标签（" · "分隔文本，去 pill chip）
    tags = c.get("tags", [])[:6]
    if tags:
        body += _text(rx, CONTENT_TOP + 140, " · ".join(tags), 12, _c(pal.ink_2), "500")
    # 数据徽章（卡片顶条统一红色）
    stats = c.get("stats", [])
    if stats:
        n = len(stats)
        gap = 24
        bw = min(210, (CONTENT_W - 220 - gap * (n - 1)) / n)
        by = CONTENT_TOP + 210
        for i, st in enumerate(stats):
            bx = MARGIN + 220 + i * (bw + gap)
            body += _card(bx, by, bw, 110, fill=_c(pal.bg_alt), stroke=_c(pal.line), rx=0, fid="sh-node")
            body += _rect(bx, by, bw, 3, fill=_c(pal.primary))
            body += _text(bx + bw / 2, by + 44, st.get("num", ""), 26, _c(pal.primary), "800", anchor="middle")
            body += _text(bx + bw / 2, by + 74, st.get("label", ""), 12, _c(pal.ink_2), "500", anchor="middle")
    # 教育/研究经历时间轴（底部左侧，填满版面）
    exp = c.get("experience", [])
    figs = c.get("figures", [])
    if exp or figs:
        ey0 = CONTENT_BOTTOM - 8 - 180
        eww = CONTENT_W - (420 if figs else 0) - 28
        if exp:
            body += _card(MARGIN, ey0, eww, 180, fill=_c(pal.bg_alt), stroke=_c(pal.line), rx=0, fid="sh-card")
            body += _text(MARGIN + 18, ey0 + 26, "教育 / 研究经历", 13, _c(pal.ink), "700")
            body += _rect(MARGIN + 18, ey0 + 34, 40, 3, fill=_c(pal.accent))
            ex0, ex1 = MARGIN + 22, MARGIN + eww - 22
            axy = ey0 + 78
            body += _rect(ex0, axy, ex1 - ex0, 3, fill=_c(pal.primary_2))
            n_e = len(exp)
            estep = (ex1 - ex0) / max(n_e, 1)
            for i, e in enumerate(exp):
                enx = ex0 + estep * (i + 0.5)
                body += _circle(enx, axy, 7, fill=_c(pal.accent) if i == 0 else _c(pal.primary),
                                stroke=_c(pal.bg), sw=2)
                period = e.get("period", "")
                ty = axy + 30
                # period 为占位（—/待填）时不渲染，role/org 上移贴节点，避免悬浮破折号
                if period and not any(m in period for m in ("待", "—", "××", "?")):
                    body += _text(enx, axy + 16, period, 10, _c(pal.primary_2), "700", anchor="middle")
                    ty = axy + 44
                body += _text(enx, ty, e.get("role", ""), 12, _c(pal.ink), "600", anchor="middle")
                body += _text(enx, ty + 20, e.get("org", ""), 11, _c(pal.ink_3), "400", anchor="middle")
        if figs:
            fx = MARGIN + eww + 28
            fw = CONTENT_W - eww - 28
            body += _fig_slot(fx, ey0, fw, 180, figs[0], idx=0)
    return body


# ================================================================ 调度
_RENDERERS = {
    "cover": _cover,
    "overview": _overview,
    "section": _section,
    "content": _content,
    "steps": _steps,
    "table": _table,
    "flow": _flow,
    "flow_table": _flow_table,
    "chart_table": _chart_table,
    "hex": _hex,
    "print3": _print3,
    "refs": _refs,
    "timeline": _timeline,
    "quote": _quote,
    "takeaway": _takeaway,
    "two_col": _two_col,
    "compare": _compare,
    "mindmap": _mindmap,
    "fig_table_text": _fig_table_text,
    "fig_points": _fig_points,
    "research": _research,
    "route": _route,
    "profile": _profile,
}

_PAGE_ROLE = {
    "cover": "cover",
    "overview": "toc",
    "section": "section",
    "content": "content",
    "steps": "content",
    "table": "content",
    "flow": "content",
    "flow_table": "content",
    "chart_table": "content",
    "hex": "content",
    "print3": "content",
    "refs": "ending",
    "timeline": "content",
    "quote": "content",
    "takeaway": "ending",
    "two_col": "content",
    "compare": "content",
    "mindmap": "content",
    "fig_table_text": "content",
    "fig_points": "content",
    "research": "content",
    "route": "content",
    "profile": "content",
}


def generate_slide(slide: dict, page_no: int = 1, total: int = 1) -> str:
    """单页 DeckPlan slide → 完整 SVG 页面字符串。"""
    layout = slide.get("layout", "content")
    render = _RENDERERS.get(layout)
    if render is None:
        raise ValueError(f"未知布局: {layout}")
    # 全屏白底
    body = _rect(0, 0, W, H, fill=_c(_pal().bg))
    body += render(slide)
    role = _PAGE_ROLE.get(layout, "content")
    # 深色整页（section）页脚用浅色，保证对比度
    footer_color = "#C9D4E2" if role == "section" else None
    body += _footer(page_no, total, footer_color)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" data-pptx-page-role="{role}" '
        f'font-family="{esc(FONT_CJK)}">'
        f'{_filters()}'
        f'{body}'
        f'</svg>'
    )


def generate_deck(deck_plan: dict, out_dir: str) -> List[str]:
    """整个 DeckPlan → 写入 out_dir/svg_output/slide_NN.svg，返回路径列表。"""
    global THEME, _FOOTER_LEFT
    name = deck_plan.get("theme", "academic")
    THEME = _THEMES.get(name, ACADEMIC)
    # 页脚左下 = 课题短名（截断），绝不放个人姓名
    footer_left = (deck_plan.get("footer", "") or deck_plan.get("deck_title", "")).strip()
    footer_left = footer_left.replace("\n", " ").replace("汇报人", "").strip()
    if len(footer_left) > 24:
        footer_left = footer_left[:24] + "…"
    _FOOTER_LEFT = footer_left
    svg_dir = os.path.join(out_dir, "svg_output")
    os.makedirs(svg_dir, exist_ok=True)
    slides = deck_plan.get("slides", [])
    paths = []
    for i, s in enumerate(slides):
        svg = generate_slide(s, i + 1, len(slides))
        p = os.path.join(svg_dir, f"slide_{i+1:02d}.svg")
        with open(p, "w", encoding="utf-8") as f:
            f.write(svg)
        paths.append(p)
    return paths
