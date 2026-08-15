"""
fused-pipeline · 设计系统 tokens
================================
学术 PPT 的统一设计语言。所有颜色/字体/间距/阴影/圆角都从这里取，
生成器与 QA 共用这一份 token，保证"一套设计、处处一致"。

设计取向：学术 + 科普。
- 主色为偏冷的学术蓝，辅以暖琥珀色做强调（科学传播的"锚点色"）。
- 无 AI 生图，视觉表达靠色块/几何/排版/符号撑起版面。
- 所有长度单位 = EMU 的整数倍（python-pptx 内部用 EMU，1pt = 12700 EMU）。
"""

from dataclasses import dataclass, field, asdict
from typing import Optional

# ---------------------------------------------------------------- 长度换算
PT = 12700          # 1 pt (英式点) 的 EMU 数
IN = 914400         # 1 inch
CM = 360000         # 1 cm

def pt(v: float) -> int:
    """pt → EMU。生成器里所有布局都用 int EMU，避免亚像素误差。"""
    return int(round(v * PT))


# ---------------------------------------------------------------- 颜色
@dataclass(frozen=True)
class Color:
    r: int
    g: int
    b: int
    a: int = 100           # 0-100，100=不透明

    @classmethod
    def hex(cls, h: str, a: int = 100) -> "Color":
        h = h.lstrip("#")
        assert len(h) == 6, f"hex 必须是 6 位，收到 {h}"
        return cls(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)

    def hexstr(self) -> str:
        return f"{self.r:02X}{self.g:02X}{self.b:02X}"

    def with_alpha(self, a: int) -> "Color":
        return Color(self.r, self.g, self.b, a)


@dataclass(frozen=True)
class Palette:
    """学术风配色板。名字语义化，生成器按语义取色，不要直接写死 hex。"""
    bg: Color = Color.hex("FFFFFF")            # 页底
    bg_alt: Color = Color.hex("F6F8FA")        # 交替区块底
    ink: Color = Color.hex("20262E")           # 主文字（近黑偏冷）
    ink_2: Color = Color.hex("48536B")         # 次级文字
    ink_3: Color = Color.hex("8A94A6")         # 弱化文字/注释
    line: Color = Color.hex("DCE2EA")          # 发丝线/边框
    primary: Color = Color.hex("1B3A5C")       # 学术深蓝（标题/强调块）
    primary_2: Color = Color.hex("2A6F97")     # 主蓝（图表/次级强调）
    deep: Color = Color.hex("002060")          # 深藏青（章节页底/暗块）
    accent: Color = Color.hex("E9A13B")        # 暖琥珀（锚点强调/结论）
    teal: Color = Color.hex("2A9D8F")          # 青绿（对比分支/正向）
    danger: Color = Color.hex("C24A55")        # 警示/反向
    white: Color = Color.hex("FFFFFF")
    # 图表系列色（顶刊系 + 主色对齐）：原生图表 point_colors / 手绘 fallback 共用。
    # 克制 ≤4 色：蓝 + 橙 + 灰蓝；红/绿只留给语义强调（danger/teal），不进图表循环色。
    chart_colors: tuple = (
        Color.hex("4472C4"),  # 主蓝
        Color.hex("ED7D31"),  # 橙（强调）
        Color.hex("8FA2B8"),  # 灰蓝（弱化/第三系列）
    )

    def chart(self, i: int) -> Color:
        c = self.chart_colors
        return c[i % len(c)] if c else self.primary_2

    # 语义别名（生成器从这里取，便于统一改风格）
    @property
    def section_band(self) -> Color:
        return self.primary

    @property
    def card(self) -> Color:
        return Color.hex("FFFFFF")

    @property
    def card_shadow(self) -> Color:
        return Color.hex("20262E", a=22)


# ---------------------------------------------------------------- 字体
# 中文优先，西文（拉丁/数字）用对应 sans/mono 组合。PPTX 里存字体名，
# 打开方机器负责渲染，所以写常见字体名即可。
# 组会模板的字体是"等线/DengXian"（微软雅黑仅页码），故把等线放第一：
# Windows+Office 必有等线 → 成品默认就是模板字体；预览(本机无等线)回退 Noto/YaHei。
FONT_CJK = ["DengXian", "等线", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC", "sans-serif"]
FONT_LATIN = ["Inter", "Helvetica Neue", "Arial", "sans-serif"]
FONT_MONO = ["Consolas", "JetBrains Mono", "Menlo", "monospace"]
FONT_SERIF = ["Source Han Serif SC", "Noto Serif CJK SC", "Songti SC", "Georgia", "serif"]


@dataclass(frozen=True)
class FontSpec:
    """一句话: 标题用 FONT_TITLE，正文用 FONT_BODY，数字/代码用 FONT_NUM。"""
    name: str
    size_pt: float
    color: Color
    bold: bool = False
    italic: bool = False

    def as_pptx(self):
        return {
            "name": self.name,
            "size": self.size_pt,
            "bold": self.bold,
            "italic": self.italic,
            "color": self.color.hexstr(),
        }


# ---------------------------------------------------------------- 字号层级
@dataclass(frozen=True)
class TypeScale:
    cover_title: float = 40
    cover_subtitle: float = 18
    section_title: float = 30
    slide_title: float = 22
    kicker: float = 11                 # 页眉小标签
    body: float = 14
    body_sm: float = 12
    caption: float = 10
    table_header: float = 12
    table_cell: float = 11
    footnote: float = 9


# ---------------------------------------------------------------- 布局 / 间距
@dataclass(frozen=True)
class Layout:
    slide_w: float = 13.333            # 16:9，英寸
    slide_h: float = 7.5
    margin: float = 0.55               # 页面四周留白（英寸）
    gutter: float = 0.25               # 模块间距
    card_gap: float = 0.2              # 卡片间距
    base: int = 4                      # 4pt 网格

    @property
    def content_w(self) -> float:
        return self.slide_w - 2 * self.margin

    @property
    def content_h(self) -> float:
        return self.slide_h - 2 * self.margin


# ---------------------------------------------------------------- 阴影
# python-pptx 原生阴影能力有限，这里用直接注入 outerShdw XML 的方式，
# 语义化为 shadow-sm / md / lg。dist/blur 单位 = EMU，dir 单位 = 1/60000 度。
@dataclass(frozen=True)
class Shadow:
    name: str
    blur_pt: float       # blurRad
    dist_pt: float       # dist
    dir_deg: int = 90    # 默认向下
    alpha: int = 30      # 0-100
    color: Color = Color.hex("20262E")

    def emu(self, v: float) -> int:
        return int(round(v * PT))

    def to_xml(self) -> str:
        # 用在 <a:effectLst> 里。alpha 是 alpha 通道值的 1000 倍（60000=0% alpha，100000=100%）
        alpha_val = 100000 - self.alpha * 1000
        return (
            f'<a:outerShdw blurRad="{self.emu(self.blur_pt)}" '
            f'dist="{self.emu(self.dist_pt)}" dir="{self.dir_deg * 60000}" '
            f'rotWithShape="0">'
            f'<a:srgbClr val="{self.color.hexstr()}"><a:alpha val="{alpha_val}"/></a:srgbClr>'
            f'</a:outerShdw>'
        )

    def as_pptx(self):
        return {"inherit": False, "xml": self.to_xml()}


SHADOW_NONE = None
SHADOW_SM = Shadow("sm", blur_pt=4, dist_pt=2, alpha=22)
SHADOW_MD = Shadow("md", blur_pt=8, dist_pt=3, alpha=28)
SHADOW_LG = Shadow("lg", blur_pt=12, dist_pt=5, alpha=35)


# ---------------------------------------------------------------- 圆角
ROUND_SM = 4 * PT      # pt→EMU 的圆角半径
ROUND_MD = 8 * PT
ROUND_LG = 12 * PT


# ---------------------------------------------------------------- 主题聚合
@dataclass(frozen=True)
class Theme:
    """一个主题 = 配色 + 字体 + 类型 + 布局 + 阴影策略。换风格只换这里。"""
    name: str = "academic"
    palette: Palette = Palette()
    type: TypeScale = TypeScale()
    layout: Layout = Layout()
    font_heading: str = "Microsoft YaHei"
    font_body: str = "Microsoft YaHei"
    font_num: str = "Consolas"

    # 各场景阴影策略
    shadow_card: Optional[Shadow] = SHADOW_MD
    shadow_node: Optional[Shadow] = SHADOW_SM
    shadow_panel: Optional[Shadow] = SHADOW_NONE

    def snapshot(self) -> dict:
        """供 QA/日志使用的中立结构。"""
        return {
            "name": self.name,
            "palette": asdict(self.palette),
            "layout": asdict(self.layout),
        }


ACADEMIC = Theme()
# 备选：暖色科普主题（science-pop），偏亮、更活泼
SCIENCE_POP = Theme(
    name="science-pop",
    palette=Palette(
        primary=Color.hex("0F4C5C"),
        primary_2=Color.hex("1B7F8E"),
        accent=Color.hex("FFB703"),
        teal=Color.hex("2A9D8F"),
        chart_colors=(Color.hex("1B7F8E"), Color.hex("FFB703"),
                      Color.hex("8FA2B8")),
    ),
)
# 学术深蓝主题：图表系列对齐主色系
ACADEMIC = Theme(
    palette=Palette(
        chart_colors=(Color.hex("2A6F97"), Color.hex("E9A13B"),
                      Color.hex("8A94A6")),
    ),
)


# ---------------------------------------------------------------- 红色学术主题
# 底版：用户模板《组会模板.pptx》（5 页，2026-08-04 提供）。解析出的设计语言：
#   · 封面大标题 = 血红 #C00000 粗体；作者/日期 = 深蓝灰 #293F59
#   · 暗块/内容页装饰 = 深藏青 #002060；页码 = 亮蓝 #0099FF
#   · 白底、无阴影、无花活 —— 组会模板观感：克制、学院、内容优先。
# 2026-08-06 v0.5：从 Office 蓝改成血红主色，去掉 AI 味（chips/金下划线/点阵/多色带头）。
TSINGHUA = Theme(
    name="tsinghua",
    palette=Palette(
        bg=Color.hex("FFFFFF"),           # 白底（模板 lt1）
        bg_alt=Color.hex("F5F6F8"),       # 浅中性灰（卡片/交替区块底）
        ink=Color.hex("262A33"),          # 主文字（近黑）
        ink_2=Color.hex("44546A"),        # 次级文字（模板 dk2 深灰蓝）
        ink_3=Color.hex("8FA2B8"),        # 弱化文字/注释
        line=Color.hex("DDE1E6"),         # 发丝线
        primary=Color.hex("C00000"),      # ★ 血红（模板封面标题色，锚点主色）
        primary_2=Color.hex("293F59"),    # 深蓝灰（模板副文本色）
        deep=Color.hex("002060"),         # 深藏青（模板暗块 → 章节页底）
        accent=Color.hex("C00000"),       # 强调 = 同一血红（结论/关键数字/优）
        teal=Color.hex("4E5D6C"),         # 中性蓝灰（对比分支，克制不用绿）
        danger=Color.hex("9C2B2B"),       # 深红（警示/反向）
        white=Color.hex("FFFFFF"),
        chart_colors=(Color.hex("C00000"), Color.hex("293F59"),
                      Color.hex("8FA2B8")),   # 图表：锚红 + 深蓝灰 + 灰
    ),
    type=TypeScale(cover_title=42, slide_title=23, body=14),
    font_heading="Microsoft YaHei",
    font_body="Microsoft YaHei",
    font_num="Consolas",
    shadow_card=None,      # 去阴影 = 去 AI 味（模板本身无阴影）
    shadow_node=None,
    shadow_panel=None,
)
