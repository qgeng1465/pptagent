"""
fused-pipeline · 风格库（在线 few-shot 的"联网 finetune"）
=========================================================
"微调"在联网场景 = 不用改权重，而是把**认可过的 DeckPlan 存成风格种子**，
每次规划新 deck 前检索最匹配的种子作为参考注入。这就是 DeepSeek 官方推荐的
prompt engineering + few-shot 路线，替代权重微调（LORA_PLAN.md 已改向）。

种子文件（styles/<name>.json）：
    {
      "name": "transformer-10p",
      "approved": false,              # 是否经用户打磨认可
      "added": "2026-08-04",
      "meta": {
        "genre": "学术科普",          # 学术科普|文献解读|研究计划|综述|其他
        "topic_keywords": ["Transformer", "注意力", "NLP"],
        "pages": 10,
        "layout_signature": {"cover": 1, "section": 3, ...},  # 自动算
        "style_notes": ["配色 academic 深蓝+琥珀", "表格为主"],
        "user_notes": "用户点评记录"
      },
      "deck_plan": { ... 打磨后的 DeckPlan ... }
    }

CLI：
    python -m pptagent.styles list
    python -m pptagent.styles add <name> <deck_plan.json> [--genre G]
            [--keywords "a,b,c"] [--notes "..."] [--overwrite]
    python -m pptagent.styles approve <name> [<deck_plan.json>]
    python -m pptagent.styles pick <content_graph.json> [--genre G] [--k 1]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# styles/ 目录固定放仓库根（与 README/SKILL 引用一致）
STYLES_DIR = Path(__file__).resolve().parents[1] / "styles"


# ================================================================ 存取
def styles_dir() -> Path:
    STYLES_DIR.mkdir(parents=True, exist_ok=True)
    return STYLES_DIR


def seed_path(name: str) -> Path:
    return styles_dir() / f"{name}.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_seeds() -> List[dict]:
    """返回所有种子的 meta 摘要（不含 deck_plan，避免刷屏）。"""
    out = []
    if not STYLES_DIR.exists():
        return out
    for p in sorted(STYLES_DIR.glob("*.json")):
        try:
            seed = _load(p)
        except (json.JSONDecodeError, OSError):
            continue
        meta = dict(seed.get("meta", {}))
        meta["name"] = seed.get("name", p.stem)
        meta["approved"] = seed.get("approved", False)
        meta["path"] = str(p)
        out.append(meta)
    return out


def compute_signature(deck_plan: dict) -> Dict[str, int]:
    cnt = Counter(s.get("layout", "?") for s in deck_plan.get("slides", []))
    return {k: v for k, v in sorted(cnt.items())}


def add_seed(name: str, deck_plan: dict, genre: str = "",
             keywords: List[str] = None, notes: str = "",
             overwrite: bool = False) -> Path:
    """存一个新种子（或覆盖同名）。deck_plan 会做基本结构校验。"""
    from .schema import validate_deck_plan
    errors = validate_deck_plan(deck_plan)
    if errors:
        raise ValueError("DeckPlan 校验失败，不能存为种子:\n" + "\n".join(errors[:10]))

    path = seed_path(name)
    if path.exists() and not overwrite:
        raise FileExistsError(f"种子已存在: {path}（用 --overwrite 覆盖）")

    seed = {
        "name": name,
        "approved": False,   # 新种子默认未认可；approve 后置 True
        "added": "",          # 由外部打时间戳（脚本内不取系统时间）
        "meta": {
            "genre": genre or deck_plan.get("theme", "academic"),
            "topic_keywords": [k.strip() for k in (keywords or []) if k.strip()],
            "pages": len(deck_plan.get("slides", [])),
            "layout_signature": compute_signature(deck_plan),
            "style_notes": [],
            "user_notes": notes,
        },
        "deck_plan": deck_plan,
    }
    path.write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def approve_seed(name: str, deck_plan: Optional[dict] = None) -> Path:
    """把种子标记为'用户认可'。可选传入打磨后的 deck_plan 替换。"""
    path = seed_path(name)
    if not path.exists():
        raise FileNotFoundError(f"种子不存在: {path}")
    seed = _load(path)
    if deck_plan is not None:
        from .schema import validate_deck_plan
        errors = validate_deck_plan(deck_plan)
        if errors:
            raise ValueError("DeckPlan 校验失败:\n" + "\n".join(errors[:10]))
        seed["deck_plan"] = deck_plan
        seed["meta"]["pages"] = len(deck_plan.get("slides", []))
        seed["meta"]["layout_signature"] = compute_signature(deck_plan)
    seed["approved"] = True
    path.write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ================================================================ 检索
def _query_text_from_content_graph(cg: dict) -> str:
    """把 ContentGraph 拼成可检索文本（标题+章节+要点+结论）。"""
    parts = [cg.get("title", ""), cg.get("subtitle", "")]
    for s in cg.get("sections", []):
        parts.append(s.get("title", ""))
        parts.extend(s.get("key_points", [])[:6])
        parts.extend(s.get("concepts", []))
    parts.extend(cg.get("takeaways", []))
    return " ".join(str(p) for p in parts if p)


def _bigrams(text: str, n: int = 2) -> set:
    """2-gram 集合，用于无分词依赖的中文重叠打分。"""
    s = "".join(ch for ch in text if not ch.isspace())
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def _score(seed: dict, query_text: str, qb: set) -> Tuple[float, List[str]]:
    """种子与 query 的相关性分数。关键词命中 2 分，主题词 2-gram 重叠加权。"""
    meta = seed.get("meta", {})
    score = 0.0
    hits = []
    for w in meta.get("topic_keywords", []):
        if w and w in query_text:
            score += 2.0
            hits.append(w)
    title = seed.get("deck_plan", {}).get("deck_title", "")
    tb = _bigrams(title)
    overlap = len(qb & tb)
    score += overlap * 0.1
    return score, hits


def pick_seed(query_text: str, genre: str = "", k: int = 1) -> List[dict]:
    """检索最匹配的风格种子。返回 [ {seed_meta, score, hits}, ... ]，无匹配返回空。"""
    seeds = []
    for meta in list_seeds():
        seed = _load(Path(meta["path"]))
        if genre and seed.get("meta", {}).get("genre") != genre:
            continue
        seeds.append(seed)

    if not seeds:
        return []

    qb = _bigrams(query_text)
    scored = [(_score(s, query_text, qb), s) for s in seeds]
    scored.sort(key=lambda x: x[0][0], reverse=True)

    out = []
    for (score, hits), seed in scored[:max(k, 1)]:
        meta = dict(seed.get("meta", {}))
        meta["name"] = seed.get("name")
        meta["approved"] = seed.get("approved", False)
        out.append({
            "name": seed.get("name"),
            "score": round(score, 2),
            "hits": hits,
            "approved": seed.get("approved", False),
            "layout_signature": meta.get("layout_signature", {}),
            "style_notes": meta.get("style_notes", []),
            "user_notes": meta.get("user_notes", ""),
            "path": seed_path(seed.get("name", "?")),
            "deck_plan": seed.get("deck_plan"),   # 供注入参考
        })
    return out


# ================================================================ CLI
def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(prog="python -m pptagent.styles",
                                 description="风格库：认可样例 → 风格种子 → 规划参考")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出所有种子")
    p_list.add_argument("--json", action="store_true", dest="as_json")

    p_add = sub.add_parser("add", help="存一个新种子")
    p_add.add_argument("name")
    p_add.add_argument("deck_plan", help="DeckPlan JSON 路径")
    p_add.add_argument("--genre", default="")
    p_add.add_argument("--keywords", default="", help="逗号分隔主题关键词")
    p_add.add_argument("--notes", default="")
    p_add.add_argument("--overwrite", action="store_true")

    p_app = sub.add_parser("approve", help="标记种子为用户认可（可选替换打磨后 DeckPlan）")
    p_app.add_argument("name")
    p_app.add_argument("deck_plan", nargs="?", default=None)

    p_pick = sub.add_parser("pick", help="给 ContentGraph 找最匹配的风格种子")
    p_pick.add_argument("content_graph", help="ContentGraph JSON 路径")
    p_pick.add_argument("--genre", default="")
    p_pick.add_argument("-k", type=int, default=1)

    args = ap.parse_args(argv)

    if args.cmd == "list":
        metas = list_seeds()
        if args.as_json:
            print(json.dumps(metas, ensure_ascii=False, indent=2))
        elif not metas:
            print("风格库为空。用 `add` 存第一个种子（打磨认可的 DeckPlan）。")
        else:
            for m in metas:
                mark = "✓认可" if m["approved"] else "·未认可"
                sig = m.get("layout_signature", {})
                sig_s = " ".join(f"{k}×{v}" for k, v in sig.items())
                print(f"[{mark}] {m['name']}  · {m.get('genre', '-')} · "
                      f"{m.get('pages', 0)}页 | {sig_s} | kw={m.get('topic_keywords', [])}")
        return 0

    if args.cmd == "add":
        plan = json.loads(Path(args.deck_plan).read_text(encoding="utf-8"))
        path = add_seed(args.name, plan,
                        genre=args.genre,
                        keywords=[k for k in args.keywords.split(",") if k.strip()],
                        notes=args.notes,
                        overwrite=args.overwrite)
        print(f"已存种子: {path}（未认可，打磨后 approve）")
        return 0

    if args.cmd == "approve":
        plan = None
        if args.deck_plan:
            plan = json.loads(Path(args.deck_plan).read_text(encoding="utf-8"))
        path = approve_seed(args.name, plan)
        print(f"✓ 种子已标记认可: {path}")
        return 0

    if args.cmd == "pick":
        cg = json.loads(Path(args.content_graph).read_text(encoding="utf-8"))
        query = _query_text_from_content_graph(cg)
        results = pick_seed(query, genre=args.genre, k=args.k)
        if not results:
            print("无匹配风格种子。可：先用 add 存一个基线，或手动参考 styles/ 目录。")
            return 1
        for r in results:
            mark = "✓认可" if r["approved"] else "·未认可"
            print(f"[{mark}] {r['name']}  score={r['score']}  hits={r['hits']}")
            print(f"       布局: " + " ".join(f"{k}×{v}" for k, v in r["layout_signature"].items()))
            if r["style_notes"]:
                print(f"       风格: {'; '.join(r['style_notes'])}")
            if r["user_notes"]:
                print(f"       点评: {r['user_notes']}")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
