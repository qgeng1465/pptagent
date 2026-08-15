"""
fused-pipeline · 数据契约
=========================
管线的三个阶段的 JSON 契约，全部是纯 Python dict（可 JSON 序列化），
方便：跨语言复用、LLM 输出校验、后续 LoRA 数据集直接吃这个格式。

阶段：
  Stage1  ContentGraph   —— LLM 把输入文字提炼成结构化内容
  Stage2  DeckPlan       —— LLM + 规则 把内容编排成逐页 PPT 计划
  Stage3  (生成器)        —— 把 DeckPlan 渲染成 .pptx（见 generator.py）

设计原则：Schema 只描述"内容与意图"，不描述"像素"。
具体视觉（颜色/字号/阴影）由 design/tokens.py 的 Theme 决定。
"""

from __future__ import annotations

import json
from typing import Any, List, Optional, Dict


# ================================================================ Stage1
# 提炼结果。忠实原文，不夸大；学术科普的关键是"把复杂讲准"。
CONTENT_GRAPH_SCHEMA = {
    "title": "str",
    "subtitle": "str(可选)",
    "genre": "str: 学术科普|文献解读|研究计划|综述|其他",
    "sections": [
        {
            "id": "str 如 s1",
            "title": "str",
            "key_points": ["str，每句一个要点，可独立成条"],
            "tables": [  # 可选，最多 1-2 张
                {
                    "caption": "str",
                    "columns": ["列名"],
                    "rows": [["单元格"]],   # 行数 2-8，单元格短句
                }
            ],
            "concepts": ["str，本节的抽象概念/关键词，用于思维导图分支"],
            "takeaway": "str(可选) 本节金句",
        }
    ],
    "comparisons": [  # 可选，对比表（方法/数据/观点对照）
        {
            "caption": "str",
            "rows": [{"name": "条目", "values": ["列值"], "verdict": "最佳/注意/..."}],
        }
    ],
    "timeline": [  # 可选，演化/流程（用于时间轴或流程型思维导图）
        {"stage": "str", "desc": "str", "year": "str(可选)"}
    ],
    "takeaways": ["str，全片结论，2-4 条"],
}

# ================================================================ Stage2
# 每页的布局类型。生成器按类型走不同的渲染分支。
LAYOUTS = {
    "cover",        # 封面：居中大标题 + 信息块
    "section",      # 章节过渡：Part 编号 + 标题 + 描述（深藏蓝整页）
    "overview",     # 目录页：Part 编号行（述职风格）
    "content",      # 要点页：标题 + 密集分栏要点卡
    "steps",        # 竖向编号步骤（打印三步 / 合成流程）
    "table",        # 数据表页：原生表格（蓝表头）
    "flow",         # 横向流程示意图 + 下方要点
    "flow_table",   # 流程示意图（上）+ 表格（下）
    "chart_table",  # 原生柱状图（左）+ 数据表（右）
    "hex",          # 六边形肝小叶示意图 + 分区标注
    "print3",       # 三步打印示意图 + 参数要点
    "refs",         # 参考文献：文献首页占位框
    "timeline",     # 时间轴/流程页
    "quote",        # 金句页：大字引用
    "takeaway",     # 结论页：2×2 结论卡 + 下一步
    # ---- v2.1 升级新增 ----
    "two_col",      # A/B 双栏对比：col_a / col_b{heading, bullets}
    "compare",      # 维度对比表：dimensions[{name, a, b, better}]
    "mindmap",      # 思维导图：center + branches[{name, children}]
    "fig_table_text",  # 图槽(左) + 要点(右上) + 原生表(右下)
    "fig_points",      # 图槽(顶/侧) + 密集要点
    "research",        # 研究内容/方案：三列方案卡 + 目标条 + 图槽
    "route",           # 技术路线图：科学问题→研究方案→预期成果
    "profile",         # 答辩人简介：头像占位 + 标签 + 数据徽章
}

DECK_PLAN_SCHEMA = {
    "slide_size": "str: 16:9(默认) | 4:3",
    "deck_title": "str",
    "theme": "str: academic | science-pop(默认 academic)",
    "slides": [
        {
            "id": "str 如 slide_01",
            "layout": f"str ∈ {sorted(LAYOUTS)}",
            "title": "str",
            "kicker": "str(可选) 页眉小标签，如 '背景 · 02'",
            "accent": "str(可选) 本页强调色语义: primary|teal|accent|danger",
            "content": "dict，按 layout 语义填充，见下方说明",
            "notes": "str(可选) 演讲者备注",
        }
    ],
}

# 各 layout 的 content 字段说明（供 LLM 与生成器共同遵循）
CONTENT_CONTRACT = {
    "cover": {"subtitle": "str", "meta": "str 如 作者/日期/单位", "headline": "str(可选) 一句话钩子"},
    "section": {"index": "str 如 01", "desc": "str(可选) 本节一句话"},
    "overview": {"items": ["str，目录条目，6±2 条"]},
    "content": {
        "intro": "str(可选) 一段引入",
        "points": [{"lead": "str 要点标题", "body": "str 一两句解释"}],
        "footnote": "str(可选)",
    },
    "steps": {
        "steps": [{"no": "str 如 01", "title": "str", "desc": "str", "tags": ["str(可选)"]}],
        "footnote": "str(可选)",
    },
    "table": {
        "caption": "str",
        "columns": ["str"],
        "rows": [["str"]],
        "highlight_row": "int(可选) 从 0 开始的高亮行",
        "footnote": "str(可选) 数据来源/说明",
    },
    "flow": {
        "steps": [{"no": "str", "label": "str", "sub": "str"}],
        "notes_title": "str(可选)",
        "notes": ["str"],
        "footnote": "str(可选)",
    },
    "flow_table": {
        "steps": [{"no": "str", "label": "str", "sub": "str"}],
        "caption": "str",
        "columns": ["str"],
        "rows": [["str"]],
        "footnote": "str(可选)",
    },
    "chart_table": {
        "data": [["标签", "数值", "标注(可选)"]],
        "target": "float(可选) 目标参考线",
        "highlight": "int(可选) 高亮柱下标",
        "unit": "str(可选) 单位",
        "caption": "str",
        "columns": ["str"],
        "rows": [["str"]],
        "highlight_row": "int(可选)",
        "footnote": "str(可选)",
    },
    "hex": {
        "items": [{"label": "str", "body": "str", "bar": "str(可选) primary|primary_2|accent|teal"}],
        "footnote": "str(可选)",
    },
    "print3": {
        "notes_title": "str(可选)",
        "notes": ["str"],
        "footnote": "str(可选)",
    },
    "refs": {
        "items": [{"label": "str", "note": "str(可选)"}],
    },
    "timeline": {
        "stages": [{"stage": "str", "days": "str(可选)", "desc": "str"}],
        "total": "str(可选) 底部合计条",
        "footnote": "str(可选)",
    },
    "quote": {"text": "str", "source": "str(可选)", "extra": "str(可选)"},
    "takeaway": {
        "points": ["str，2-4 条"],
        "closing": "str(可选) 收尾一句",
    },
    # ---- v2.1 新增 ----
    "two_col": {
        "col_a": {"heading": "str", "bullets": ["str"]},
        "col_b": {"heading": "str", "bullets": ["str"]},
    },
    "compare": {
        "dimensions": [{"name": "str", "sub": "str(可选)", "a": "str", "b": "str",
                        "better": "str a|b 优选项"}],
        "footnote": "str(可选)",
    },
    "mindmap": {
        "center": "str",
        "branches": [{"name": "str", "children": ["str"]}],
    },
    "fig_table_text": {
        "intro": "str(可选)",
        "points": [{"lead": "str 要点标题", "body": "str 一两句解释"}],
        "caption": "str(可选)",
        "columns": ["str"],
        "rows": [["str"]],
        "highlight_row": "int(可选)",
        "footnote": "str(可选)",
    },
    "fig_points": {
        "verdict": "dict(可选) 关键证据带 {stat, text, tag}",
        "points": [{"lead": "str 要点标题", "body": "str 一两句解释",
                    "stat": "str(可选) 大数字徽章", "bar": "str(可选) primary|accent"}],
        "solution": "str(可选) 本课题对策一句话",
        "footnote": "str(可选)",
    },
    "research": {
        "objective": "str(可选) 一句研究目标",
        "columns": [
            {"phase": "str 如 研究内容一", "items": ["str 要点"]}
        ],
        "footnote": "str(可选)",
    },
    "route": {
        "objective": "str(可选) 顶部总目标条",
        "columns": [
            {"phase": "str 如 关键科学问题", "sub": "str(可选) 阶段副题",
             "next": "str(可选) 递进动词标签", "items": ["str 要点"]}
        ],
        "closing": "str(可选) 底部闭环/交付条",
        "footnote": "str(可选)",
    },
    "profile": {
        "name": "str",
        "title": "str(可选) 头衔/职称",
        "affil": "str(可选) 单位",
        "tags": ["str(可选) 学术标签"],
        "stats": [{"num": "str 如 42", "label": "str 如 一作论文"}],
        "experience": [{"period": "str 如 2021-2026", "role": "str 如 博士研究生",
                        "org": "str(可选) 单位"}],
    },
}

# ---- 图槽契约（所有 content 类布局通用，可选）----
# 决策优先级：src 可解析 → 真实 <image>；embed(data-URI) → 嵌入；否则 → 虚线占位（图留空）。
FIGURES_SPEC = [
    {
        "slot": "str: full|hero|left|right|top|bottom|thumb",
        "aspect": "str(可选) 16:9|4:3|1:1|auto",
        "label": "str(可选) 图号，如 '图 1'",
        "caption": "str(可选) 图注一句话",
        "src": "str(可选) 相对路径 images/xxx.png",
        "embed": "str(可选) data:image/png;base64,...",
        "want_chart": "str(可选) 联网图表参考搜索词",
        "query": "str(可选) 联网搜索词（缺省用 title+caption）",
        "diagram": "str|dict(可选) 'hex' 画六边形肝小叶；{'type':'process','steps':[{'label','sub','tag'}]} 画工艺流程",
    }
]

# 给 content 类布局统一注入 figures[] 槽（避免逐个手写）
_FIG_INJECT = {
    "content", "steps", "flow", "flow_table", "chart_table", "hex", "print3",
    "timeline", "takeaway", "two_col", "compare", "mindmap",
    "fig_table_text", "fig_points", "research", "route", "profile",
}
for _lay in _FIG_INJECT:
    CONTENT_CONTRACT.setdefault(_lay, {})["figures"] = FIGURES_SPEC


# ================================================================ 校验工具
def _type_tag(spec: str) -> str:
    """从类型描述里提取类型 token。如 "str(可选) 演讲者备注" → "str"；
    "dict，按 layout 语义" → "dict"；"list，2-8 条" → "list"。"""
    return spec.split(",")[0].split("(")[0].strip()


def _check(node: Any, spec: Any, path: str, errors: List[str]) -> None:
    if isinstance(spec, dict):
        if not isinstance(node, dict):
            errors.append(f"{path}: 应为 dict，实为 {type(node).__name__}")
            return
        for key, sub in spec.items():
            # 可选性由 value 描述里的 "(可选)" 决定（key 不携带标记）
            optional = isinstance(sub, str) and "(可选)" in sub
            if key not in node:
                if not optional:
                    errors.append(f"{path}: 缺少必需字段 '{key}'")
                continue
            _check(node[key], sub, f"{path}.{key}", errors)
    elif isinstance(spec, list):
        if not isinstance(node, list):
            errors.append(f"{path}: 应为 list，实为 {type(node).__name__}")
            return
        for i, item in enumerate(node):
            if item is None:
                continue
            _check(item, spec[0], f"{path}[{i}]", errors)
    elif isinstance(spec, str):
        tag = _type_tag(spec)
        if tag == "str":
            if not isinstance(node, str):
                errors.append(f"{path}: 应为 str，实为 {type(node).__name__}")
        elif tag == "int":
            if not isinstance(node, int):
                errors.append(f"{path}: 应为 int，实为 {type(node).__name__}")
        elif tag == "dict":
            if not isinstance(node, dict):
                errors.append(f"{path}: 应为 dict，实为 {type(node).__name__}")
        elif tag == "list":
            if not isinstance(node, list):
                errors.append(f"{path}: 应为 list，实为 {type(node).__name__}")


def validate_content_graph(obj: dict) -> List[str]:
    errors: List[str] = []
    _check(obj, CONTENT_GRAPH_SCHEMA, "content", errors)
    return errors


def validate_deck_plan(obj: dict) -> List[str]:
    errors: List[str] = []
    _check(obj, DECK_PLAN_SCHEMA, "deck", errors)
    for i, s in enumerate(obj.get("slides", [])):
        lay = s.get("layout")
        if lay not in LAYOUTS:
            errors.append(f"slides[{i}].layout='{lay}' 不在 {sorted(LAYOUTS)}")
    return errors


def dump(obj: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
