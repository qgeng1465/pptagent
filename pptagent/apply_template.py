"""
fused-pipeline · 套用用户模板主题
=================================
把转换器输出的 PPTX 里的 theme1.xml 替换成用户模板（组会模板）的主题，
让成品的"主题身份"（字体方案 等线、主题色引用）来自用户模板。

注意：模板的红色身份（标题 #C00000、作者行 #293F59、页码 #0099FF）是硬编码在
模板幻灯片 XML 里的，不在 theme1.xml（其 accent1 实为默认 Office 蓝 #4472C4）。
所以本步骤主要贡献 = 字体方案（等线）与主题槽；版面配色由 svggen 的设计 token
（tsinghua 主题，血红主色）保证与模板视觉一致。

用法：
    python -m pptagent.apply_template <输出.pptx> <模板.pptx> [--out 新路径]
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path


def _template_theme(template_pptx: str) -> tuple[str, bytes]:
    """返回 (theme文件名, 内容)。模板可能多主题，取第一个。"""
    with zipfile.ZipFile(template_pptx) as tz:
        theme_names = [n for n in tz.namelist() if n.startswith("ppt/theme/") and n.endswith(".xml")]
        if not theme_names:
            raise FileNotFoundError(f"模板里没有主题: {template_pptx}")
        return theme_names[0], tz.read(theme_names[0])


def theme_accent1(template_pptx: str) -> str | None:
    """读取模板主题的 accent1 强调色（用于在日志里报告真实身份色）。"""
    import re
    _, theme = _template_theme(template_pptx)
    m = re.search(rb'clrScheme.*?<a:accent1>\s*<a:srgbClr val="([0-9A-Fa-f]{6})"', theme, re.S)
    return m.group(1).decode().upper() if m else None


def apply_template(pptx_path: str, template_pptx: str, out_path: str | None = None) -> str:
    pptx_path = str(pptx_path)
    out_path = out_path or pptx_path
    # 读取模板的主题（可能多主题，取第一个）
    _, template_theme = _template_theme(template_pptx)
    # 重写输出，替换主题部分
    tmp = out_path + ".tmp"
    replaced = 0
    with zipfile.ZipFile(pptx_path) as zin:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.startswith("ppt/theme/") and item.filename.endswith(".xml"):
                    data = template_theme
                    replaced += 1
                zout.writestr(item, data)
    shutil.move(tmp, out_path)
    if replaced == 0:
        raise RuntimeError("输出 PPTX 里没有找到主题部分")
    return out_path


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="把用户模板主题嵌入输出 PPTX")
    ap.add_argument("pptx")
    ap.add_argument("template")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    p = apply_template(args.pptx, args.template, args.out)
    print(f"已套用模板主题 -> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
