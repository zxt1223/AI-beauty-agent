# -*- coding: utf-8 -*-
"""
D3 评测集 · 第三步：避雷集构建 v2（「意图相反」重构，用户主导）
=================================================================
方向（用户复核结论）：避雷 = 用户意图的相反面，避雷商品必须能从可见标签自证
「为什么不该推它」。旧版用「评论缺陷证据」反推，商品自带标签（肤质/妆效）与 query
意图对不上（如要色号却给混油标签商品）→ 人工复核判 1 分。

v2 重构（对标 benchmark03 avoid_target 的"负偏好"语义）：
  ① 意图相反标签匹配（主）——直接找「带相反标签」的商品
       要高遮瑕 → 避 coverage_tag=轻遮瑕
       要白皙   → 避 shade_tag=深色（标题色号桶）
       要深色   → 避 shade_tag=白皙
       要控油   → 避 finish_tag=水光/光泽（出油感妆效）
       色号意图中性（只要"合适的色号"）→ 色号轴不避（无"相反"可定义）
  ② 缺陷证据兜底（次）——无相反标签的属性轴，退回评论缺陷证据
       避雷防刺激 → 闷痘/刺激 证据商品
       持妆       → 脱妆 证据商品
       质地肤感   → 卡粉 证据商品
  ③ 排序：标签相反命中(强信号，每轴+5) + 缺陷加权证据分；同品牌限 1；排除 primary/extra

输入  : data/evaluation_set.csv + products_clean.csv（含 shade_tag）+ product_defect_evidence.csv
输出  : 更新 evaluation_set.csv（gold_negative + gold_negative_reason）
"""
import io
import sys
import re
from pathlib import Path

import pandas as pd

from defect_consensus import consensus_axes, parse_scores

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")
ES = ROOT / "data" / "evaluation_set.csv"
DEFECT_EV = ROOT / "data" / "product_defect_evidence.csv"

# 色号方向识别（排除习语/语境误判：fair share、light coverage 不是色号）
SHADE_LIGHT = re.compile(
    r"\b(pale|ivory|porcelain|ghost|white|alabaster)\b|"
    r"\bfair\b(?!\s+share)|\blight\b(?=\s+(?:shade|skin|foundation|color|colour))|"
    r"very light", re.I)
SHADE_DARK = re.compile(r"\b(dark|deep|tan|mahogany|ebony|rich)\b", re.I)

# 意图轴 → 缺陷证据兜底（无相反标签的轴）
DEFECT_RISK = {
    "避雷防刺激": ["闷痘", "刺激"],
    "持妆": ["脱妆"],
    "质地肤感": ["卡粉"],
}


def anti_tags_for(intent, coverage_label, query_text):
    """意图 → 相反标签匹配规则列表 [(reason, 商品属性→bool)]"""
    rules = []
    for it in str(intent).split(";"):
        if it == "遮盖力" and str(coverage_label) in ("full", "medium"):
            rules.append(("轻遮瑕(意图相反)", lambda p: p.get("coverage_tag") == "轻遮瑕"))
        elif it == "色号":
            q = str(query_text).lower()
            if SHADE_LIGHT.search(q):
                rules.append(("色号偏深(意图相反)",
                              lambda p: "深色" in str(p.get("shade_tag") or "").split(";")))
            elif SHADE_DARK.search(q):
                rules.append(("色号偏白(意图相反)",
                              lambda p: "白皙" in str(p.get("shade_tag") or "").split(";")))
            # 中性（"合适的色号"）→ 色号不避
        elif it == "控油":
            rules.append(("水光/光泽(出油感)", lambda p: p.get("finish_tag") in ("水光", "光泽")))
    return rules


def defect_risks_for(intent):
    return {d for it in str(intent).split(";") if it in DEFECT_RISK for d in DEFECT_RISK[it]}


def main():
    es = pd.read_csv(ES)
    df = pd.read_csv(ROOT / "data" / "products_clean.csv")
    df["shade_tag"] = df.get("shade_tag", pd.Series("", index=df.index)).fillna("")
    prod = df.set_index("parent_asin")
    ev = pd.read_csv(DEFECT_EV)
    ev_idx = ev.set_index("parent_asin")

    # 预计算每商品的缺陷轴加权分（兜底用）—— 只认 70% 负面共识的硬规则轴（用户定标 2026-08-28）
    def defect_score(pa, risk):
        if pa not in ev_idx.index:
            return 0.0
        row = ev_idx.loc[pa]
        axes = consensus_axes(row["defect_scores"], row["n_neg_reviews"]) & set(risk)
        if not axes:
            return 0.0
        parts = parse_scores(row["defect_scores"])
        return sum(parts[d] for d in axes)

    rows_new = []
    for _, r in es.iterrows():
        rules = anti_tags_for(r["intent"], r["coverage_label"], r["query"])
        drisk = defect_risks_for(r["intent"])
        exclude = set()
        for col in ["gold_primary", "gold_extras"]:
            for a in str(r[col]).split(";"):
                if a:
                    exclude.add(a)

        cands = []
        for pa, p in prod.iterrows():
            if pa in exclude:
                continue
            anti_hit = [name for name, fn in rules if fn(p)]
            dscore = defect_score(pa, drisk)
            score = len(anti_hit) * 5 + dscore
            if score > 0:
                reasons = []
                if anti_hit:
                    reasons.extend(anti_hit)
                if dscore > 0:
                    # 只列达标 70% 共识的硬规则轴（与 defect_score 同口径）
                    hax = consensus_axes(ev_idx.loc[pa, "defect_scores"],
                                         ev_idx.loc[pa, "n_neg_reviews"]) & set(drisk)
                    reasons.append(";".join(f"{d}证据(共识>70%)" for d in hax))
                cands.append((pa, score, ";".join(reasons)))
        cands.sort(key=lambda x: -x[1])

        # 同品牌多样性去重 + 排除无证据理由的兜底噪声
        picked, seen_brand = [], set()
        for pa, sc, rs in cands:
            if not rs:
                continue
            b = str(prod.loc[pa, "brand"] if pa in prod.index else "").lower()
            if b and b in seen_brand:
                continue
            picked.append((pa, rs))
            seen_brand.add(b)
            if len(picked) >= 3:
                break

        gold_neg = ";".join(a for a, _ in picked)
        reason = ";".join(f"{a}[{ax}]" for a, ax in picked)
        rows_new.append((gold_neg, reason))

    es["gold_negative"] = [g for g, _ in rows_new]
    es["gold_negative_reason"] = [rs for _, rs in rows_new]
    es["n_gold"] = es["gold_primary"].fillna("").ne("").astype(int) + \
        es["gold_extras"].fillna("").str.split(";").apply(lambda x: sum(1 for a in x if a)) + \
        es["gold_negative"].fillna("").str.split(";").apply(lambda x: sum(1 for a in x if a))
    es.to_csv(ES, index=False, encoding="utf-8-sig")

    # 明细展示
    print(f"=== 各 query 新避雷集（意图相反）===")
    for i, r in es.iterrows():
        rules = anti_tags_for(r["intent"], r["coverage_label"], r["query"])
        drisk = defect_risks_for(r["intent"])
        anti_desc = [n for n, _ in rules] or ["-"]
        g = str(r["gold_negative"]) if pd.notna(r["gold_negative"]) else ""
        rs = str(r["gold_negative_reason"]) if pd.notna(r["gold_negative_reason"]) else ""
        titles = []
        for a in g.split(";"):
            if a and a in prod.index:
                p = prod.loc[a]
                tags = ";".join(x for x in [p.get("coverage_tag"), p.get("finish_tag"), p.get("shade_tag")]
                                if isinstance(x, str) and x)
                titles.append(f"{str(p['title'])[:32]} [{tags}]")
        print(f"id={i+1}  意图相反={anti_desc} 缺陷兜底={sorted(drisk) or '-'}")
        print(f"    query: {str(r['query'])[:55]}")
        print(f"    avoid: {' || '.join(titles) if titles else '(无匹配)'}")
        if rs:
            print(f"    理由: {rs}")
    n_neg = es["gold_negative"].fillna("").str.split(";").apply(
        lambda x: sum(1 for a in x if a)).sum()
    print(f"\nnegative 商品总数: {n_neg}")


if __name__ == "__main__":
    main()
