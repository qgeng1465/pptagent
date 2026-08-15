"""
fused-pipeline · 学术规划辅助
============================
Step 2（DeckPlan 计划）的规则层。改编自 paper-ppt-agent 的
page_type_budget：给定总页数与内容类型，给出学术 PPT 的页面结构预算，
避免"通篇 content 页"的平铺。

纯函数，供 LLM（Claude Code）规划时参考，也供 QA 校验页数结构。
"""

from __future__ import annotations

from typing import List, Dict


# ---------------------------------------------------------------- 页面预算
# 学术 PPT 的结构惯例：cover → (section × N) → content/表格/图谱 → takeaway
BUDGET_TABLE = [
    # (页数下限, cover, section, content, 数据页(table/compare/mindmap/timeline), ending)
    (6,   1, 1, 2, 1, 1),
    (8,   1, 2, 2, 2, 1),
    (11,  1, 3, 3, 3, 1),
    (16,  1, 4, 5, 5, 1),
    (21,  1, 5, 7, 6, 1),
]


def academic_budget(total_pages: int) -> Dict[str, int]:
    """给定总页数 → 各类页面的建议数量。"""
    budget = None
    for lo, cover, sec, content, data, ending in BUDGET_TABLE:
        if total_pages >= lo:
            budget = {"cover": cover, "section": sec,
                      "content": content, "data": data, "ending": ending}
    if budget is None:
        budget = {"cover": 1, "section": 1, "content": 1, "data": 1, "ending": 1}
    return budget


def budget_guidance(content_type: str, requested_pages: int = 0) -> str:
    """给 LLM 的规划提示文本。"""
    pages = requested_pages or {
        "学术科普": 12, "文献解读": 12, "研究计划": 14, "综述": 16,
    }.get(content_type, 12)
    b = academic_budget(pages)
    return (
        f"[学术页面预算] 目标约 {pages} 页。建议结构：封面×{b['cover']}，"
        f"章节过渡×{b['section']}，正文内容×{b['content']}，"
        f"数据页(表格/对比/思维导图/时间轴)×{b['data']}，结语×{b['ending']}。"
        f"注意：数据页占比不应低于 25%；每 3-5 页插一个章节过渡；"
        f"每张内容页尽量含 figures[] 图槽或表格，避免纯文字。"
    )


# ---------------------------------------------------------------- 报奖/申报预算
# 国自然（面上/优青/杰青）答辩 & 申报结构：封面 + 答辩人 + 章节 + 背景 +
# 研究内容 + 技术路线 + 数据表 + 研究基础 + 结语。
NSFC_BUDGET_TABLE = [
    # (页数下限, cover, profile, section, background, research, route, table, foundation, takeaway)
    (12, 1, 1, 1, 2, 2, 2, 2, 1, 1),
    (16, 1, 1, 2, 3, 3, 3, 3, 1, 1),
    (20, 1, 1, 2, 4, 4, 4, 3, 1, 1),
]


def nsfc_budget(total_pages: int) -> Dict[str, int]:
    """报奖/基金申报结构预算。"""
    budget = None
    for lo, cover, profile, sec, bg, research, route, table, foundation, ending in NSFC_BUDGET_TABLE:
        if total_pages >= lo:
            budget = {"cover": cover, "profile": profile, "section": sec,
                      "background": bg, "research": research, "route": route,
                      "table": table, "foundation": foundation, "takeaway": ending}
    return budget or {"cover": 1, "profile": 1, "section": 1, "background": 1,
                      "research": 1, "route": 1, "table": 1, "foundation": 1, "takeaway": 1}


def budget_guidance_nsfc(content_type: str, requested_pages: int = 0) -> str:
    """报奖/申报类的规划提示（研究计划/申报书走这套）。"""
    pages = requested_pages or {"研究计划": 16, "申报书": 18}.get(content_type, 16)
    b = nsfc_budget(pages)
    return (
        f"[报奖结构预算] 目标约 {pages} 页。建议结构：封面×{b['cover']}，"
        f"答辩人简介×{b['profile']}，章节过渡×{b['section']}，"
        f"研究背景×{b['background']}，研究内容/方案×{b['research']}，"
        f"技术路线图×{b['route']}，数据表×{b['table']}，研究基础×{b['foundation']}，"
        f"结语×{b['takeaway']}。注意：技术路线图/研究方案页占比不应低于 25%；"
        f"每张研究页用 figures[] 图槽 + 表格 + 文字三件套，避免纯文字堆砌。"
    )


# ---------------------------------------------------------------- 结构校验
def check_deck_structure(deck_plan: dict) -> List[str]:
    """对 DeckPlan 做学术结构层面的检查（区别于 schema 的字段校验）。"""
    issues: List[str] = []
    slides = deck_plan.get("slides", [])
    if not slides:
        return ["DeckPlan 没有页面"]
    n = len(slides)
    if slides[0].get("layout") != "cover":
        issues.append("第 1 页应为 cover")
    if slides[-1].get("layout") not in ("takeaway", "appendix"):
        issues.append("最后一页应为 takeaway 或 appendix")

    data_layouts = {"table", "compare", "mindmap", "timeline", "chart_table",
                    "flow_table", "fig_table_text", "route", "research"}
    n_data = sum(1 for s in slides if s.get("layout") in data_layouts)
    if n >= 8 and n_data / n < 0.2:
        issues.append(f"数据页占比 {n_data}/{n} 偏低（<20%），建议把可比内容改为表格/图谱/图表")

    # 纯文字页守卫：content 类页无图槽、无表且要点过多 → 信息密度低
    text_like = {"content", "two_col", "fig_points", "steps", "quote", "takeaway"}
    for i, s in enumerate(slides):
        lay = s.get("layout")
        c = s.get("content", {})
        if lay not in text_like:
            continue
        figs = c.get("figures") if isinstance(c, dict) else None
        has_fig = bool(figs)
        has_tbl = bool(c.get("columns")) or bool(c.get("rows"))
        n_pts = len(c.get("points", []))
        if not has_fig and not has_tbl and n_pts > 5:
            issues.append(
                f"slide[{i}] 纯文字页（无图槽、无表格）要点 {n_pts}>5：信息密度低，"
                f"建议拆出表格或加 figures[] 图槽（图留空占位）")

    n_section = sum(1 for s in slides if s.get("layout") == "section")
    if n >= 10 and n_section < 2:
        issues.append("内容较多但章节过渡页过少（<2），建议增加 section 页")

    # 每页要点密度检查
    for i, s in enumerate(slides):
        lay = s.get("layout")
        c = s.get("content", {})
        if lay == "content":
            pts = c.get("points", [])
            if len(pts) > 6:
                issues.append(f"slide[{i}] content 要点过多（{len(pts)}>6），会溢出")
            for pt in pts:
                if len(pt.get("lead", "")) > 20:
                    issues.append(f"slide[{i}] 要点标题过长: '{pt.get('lead')[:22]}…'")
        if lay == "table":
            rows = c.get("rows", [])
            cols = c.get("columns", [])
            if len(cols) > 7:
                issues.append(f"slide[{i}] 表格列过多（{len(cols)}>7），会挤压")
            if len(rows) > 9:
                issues.append(f"slide[{i}] 表格行过多（{len(rows)}>9），会溢出")
    return issues


def summarize_structure(deck_plan: dict) -> str:
    """一行摘要，方便给用户看结构。"""
    from collections import Counter
    cnt = Counter(s.get("layout", "?") for s in deck_plan.get("slides", []))
    parts = " · ".join(f"{k}×{v}" for k, v in sorted(cnt.items()))
    return f"{len(deck_plan.get('slides', []))} 页 | {parts}"
