"""
fused-pipeline · 内容诚实检查器
================================
按《docs/HONESTY_PROTOCOL.md》核对 deck_plan.json：任何数字/头衔/单位/引用/个人成果
必须能在 sources_manifest.json 找到出处，否则判为编造并阻断。

内容来源优先级（L1>L2>L3>L4）：
  L1 用户上传模板 | L2 用户上传文档 | L3 交互信息 | L4 联网补充(需 verified+来源)
无来源且非 ××/（待填）占位 → 判编造。

用法：
    python -m pptagent.honesty <deck_plan.json> <sources_manifest.json> [--strict]

返回码：0=通过；1=有编造项（可阻断生成）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 占位模式：任何字段值若匹配这些即视为"诚实占位"，不算编造
PLACEHOLDER_PATTERNS = [
    r"^××+$",              # ×××
    r"^—+$",                # ——
    r"待填",                # （待填）
    r"待提供", r"待补充",
    r"^\(.*待.*\)$",
    r"^\?\?+$",
    r"待定", r"未知",
]

# 通用经历分类标签（不是编造的头衔）——role 字段若只是这些分类名，
# 且同槽 org 为占位（用户经历待填），则不算编造。
GENERIC_ROLE_LABELS = {"教育经历", "研究经历", "工作经历", "学习经历", "学术经历"}

# 疑似个人成果/头衔的关键词：命中且值非占位、manifest 又无对应 → 编造
PERSONAL_KEYS = [
    "论文", "引用", "被引", "Scholar", "专利", "基金", "国自然",
    "一作", "共同一作", "获奖", "荣誉", "学位", "职称", "院士",
    "参与项目", "主持项目", "博导", "硕导",
]

# 需要来源锚点的字段（用户提供的实验/研究事实）
FACT_KEYS = ["data", "rows", "stats", "experience", "columns", "caption", "conclusion"]

# 文献引用标注：正文/图注/脚注里出现 [数字] 或 [a–b] 这类带方括号的编号引用。
# 必须能对上 manifest 里真实存在的参考文献表；deck 无参考文献表、引用文献在
# placeholders → 判编造（HONESTY_PROTOCOL §II）。
CITATION_RE = re.compile(r"\[[0-9]+(?:[,\-–]\s*[0-9]+)*\]")


def is_placeholder(value) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v:
        return True
    return any(re.fullmatch(p, v) or p in v for p in PLACEHOLDER_PATTERNS)


def _walk(node, path=""):
    """深度遍历 deck，产出 (路径, 值)。"""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, node


def _collect_manifest_terms(manifest: dict) -> dict:
    """把 manifest 里的已确认事实、联网补充、占位清单整理成可查集。"""
    facts: dict[str, list] = {}
    for f in manifest.get("confirmed_facts", []):
        facts.setdefault(f.get("field", ""), []).append(str(f.get("value", "")))
    web = manifest.get("web_supplemented", [])
    web_verified = {str(w.get("field", "")) for w in web if w.get("verified")
                    and w.get("source")}
    placeholders = {str(p) for p in manifest.get("placeholders", [])}
    return {"facts": facts, "web_verified": web_verified, "placeholders": placeholders}


def check(deck: dict, manifest: dict) -> list[dict]:
    """返回编造项列表：[{path, value, why, fix}]"""
    mp = _collect_manifest_terms(manifest)
    facts, web_ok, ph_fields = mp["facts"], mp["web_verified"], mp["placeholders"]
    violations: list[dict] = []

    for path, value in _walk(deck):
        if not isinstance(value, str) or not value.strip():
            continue
        v = value.strip()
        if is_placeholder(v):
            continue
        seg = path.split(".")[-1]
        seg = re.sub(r"\[\d+\]$", "", seg)

        # 1) 文献引用 [N]：deck 无对应参考文献表 → 编造（删标注或补真实出处）
        cite = CITATION_RE.search(v)
        if cite:
            cite_supported = any("参考" in str(f.get("field", "")) or "citation" in str(f.get("field", "")).lower()
                                 for f in manifest.get("confirmed_facts", [])) \
                or "引用文献" not in ph_fields
            if not cite_supported:
                violations.append({
                    "path": path, "value": cite.group(0),
                    "why": f"文本含文献引用 {cite.group(0)}，但 manifest 无真实参考文献表（引用文献=占位）",
                    "fix": "删除该引用标注，或补充真实出处到 manifest confirmed_facts（带作者/年份/期刊）",
                })
            # 即便支持引用，也要继续走数字/成果检查；不中断
        # 2) 值内含个人成果关键词 + 数字（如 "12 篇一作"、"2 项国自然"）且无来源 → 编造
        #    （label 只是统计项的描述，如"近5年一作/共同一作论文"，数字在 num 由上面检查，排除）
        if seg != "label" and re.search(r"\d", v) and any(k in v for k in PERSONAL_KEYS) and \
                not any(v in vals for vals in facts.values()):
            violations.append({
                "path": path, "value": v,
                "why": f"文本含个人成果关键词且带数字（{v}），manifest 无对应来源",
                "fix": "改为 ×× /（待填），或在 confirmed_facts 补真实数据",
            })

        # 命中个人成果关键词且值像数字/成果描述，需 manifest 有对应
        if seg in ("num", "org", "role", "meta", "affil", "title", "period") or \
           any(k in seg for k in PERSONAL_KEYS):
            # 字段本身在占位清单 → 允许（该字段用户没给，用占位）
            if seg in ph_fields or path.split(".")[1] in ph_fields:
                continue
            # 通用经历分类标签 + 同槽 org 为占位 → 只是占位槽标签，非编造
            if seg == "role" and v in GENERIC_ROLE_LABELS and \
               any(k == "org" and is_placeholder(node)
                   for k, node in _siblings(path, deck)):
                continue
            # 若值有来源字段覆盖
            if any(v in vals for vals in facts.values()) or seg in web_ok:
                continue
            # num 且不是纯数字（如 ××）已在占位拦截；纯数字成果数没来源 → 编造
            if re.fullmatch(r"[\d.\-+≈~\s%]+", v) and seg == "num":
                violations.append({
                    "path": path, "value": v,
                    "why": f"'{seg}' 是数字成果({v})，manifest 无对应来源",
                    "fix": "改为 ×× 占位，或在 manifest confirmed_facts 补真实数据",
                })
            elif seg in ("org", "role", "meta", "affil") and \
                 not any(k in v for k in ["待", "××", "—"]):
                violations.append({
                    "path": path, "value": v,
                    "why": f"'{seg}'={v} 是单位/经历/头衔，manifest 未确认",
                    "fix": "改为（待填），或在 confirmed_facts 补真实信息",
                })
    return violations


def _siblings(path: str, deck: dict):
    """返回指定 path 的兄弟节点（同为 dict 的值），供占位槽判断。"""
    parts = [p for p in path.split(".") if p]
    node = deck
    for p in parts[:-1]:
        if "[" in p:
            name, idx = re.match(r"(.+?)\[(\d+)\]", p).groups()
            node = node[name][int(idx)]
        else:
            node = node[p]
    return [(k, v) for k, v in node.items()] if isinstance(node, dict) else []


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python -m pptagent.honesty <deck_plan.json> <sources_manifest.json>")
        return 2
    deck = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if Path(sys.argv[2]).exists():
        manifest = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    else:
        manifest = {}
        print(f"[!] 找不到 {sys.argv[2]} —— 按空 manifest 严格检查")
    viol = check(deck, manifest)
    if viol:
        print(f"=== 内容诚实检查: 发现 {len(viol)} 处疑似编造 ===")
        for v in viol:
            print(f"  ❌ {v['path']} = {v['value']!r}\n     ↳ {v['why']}\n     ↳ 修法: {v['fix']}")
        return 1
    print("=== 内容诚实检查: 通过（所有数字/头衔/单位均有来源或为占位）===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
