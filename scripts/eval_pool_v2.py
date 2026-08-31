# -*- coding: utf-8 -*-
"""eval_pool_v2 — v2 候选池内首答命中率（对齐 Phase 1 池内评测方法学）

口径：首答命中率 = 在候选池内、检索 top-3 命中 gold_primary 的比例。
  - 分母 = 有推荐题的 v2 query（排除 ask_all/ask_first 追问题；ask_shade_soft 会推荐，计入）
  - 池子 = candidate_pool_v2（gold + 15难例 + 35随机，seed=42，与 Phase-1 同规则）
  - 评分 = mixed（Agent 现状，含向量）与 TagFirst+②（候选排序改进）
  - 另报 top-5 命中与「任一 gold 命中」作对照；逐题报告池子构成保证透明

用法：python eval_pool_v2.py
"""
import io
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval_engine import ProductIndex
from agent import GuideAgent
from db_config import db_url

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")
DB = db_url()


class TagFirst(ProductIndex):
    """标签优先排序 → 已落正式（2026-08-28）：retrieval_engine.ProductIndex 的
    mode="tagfirst" 分支（标签主序 → 热度 → BM25）。本类保留两个曾试过的
    可选杠杆（hard_score 硬约束加分 / axes_tie 证据并列）作为历史测量记录，
    均已验证「不提升数字」（11/19=57.9% 纹丝不动），Agent 线上不启用。"""
    def __init__(self, csv, hard_score=False, axes_tie=False):
        super().__init__(csv)
        self.hard_score = hard_score
        self.axes_tie = axes_tie

    def score_candidates(self, mode, req, candidates, weights=None):
        if not self.hard_score and not self.axes_tie:
            return super().score_candidates("tagfirst", req, candidates, weights)
        # ---- 历史杠杆测量路径（默认关，仅 eval_pool_v2 变体开关用）----
        rows = []
        for a in candidates:
            p = self.by_asin.get(a)
            if p is None:
                continue
            t, rs = self.tag_score(req, p)
            if t == float("-inf"):
                continue
            if self.hard_score and req["hard"]:
                p_skins = set(s for s in str(p.get("skin_tags") or "").split(";") if s)
                for h in req["hard"]:
                    if p_skins & {h, "全肤质"}:
                        t += 1
                        if f"硬约束{h}" not in rs:
                            rs.append(f"硬约束{h}")
            rows.append((a, t, rs))
        if self.axes_tie:
            rows.sort(key=lambda x: (x[1], len(set(x[2])),
                                     self.heat_score(self.by_asin[x[0]]),
                                     self.bm25.score(req["qtext"], self.index_of[x[0]])),
                      reverse=True)
        else:
            rows.sort(key=lambda x: (x[1], self.heat_score(self.by_asin[x[0]]),
                                     self.bm25.score(req["qtext"], self.index_of[x[0]])),
                      reverse=True)
        return [(a, float(t), rs) for a, t, rs in rows]


def main():
    engine = create_engine(DB)
    pool = pd.read_sql("SELECT query_id, asin, label, gold_type FROM candidate_pool_v2", engine)
    qd = pd.read_sql("SELECT id, query_type, query FROM eval_review_50", engine)
    qmap = {int(r.id): r for r in qd.itertuples()}

    agent = GuideAgent()
    # 2026-08-28：②精标已落真库（products_clean.csv 含 4 肤质+14 遮瑕），
    # 对照组的 mixed 与 TagFirst 都读真库（原 products_clean_sim.csv 模拟文件退役）
    idx_mixed = ProductIndex(ROOT / "data" / "products_clean.csv")
    idx_mixed.enable_vectors()
    REAL = ROOT / "data" / "products_clean.csv"
    variants = [
        ("mixed  (现状)", idx_mixed),
        ("TagFirst（正式排序）", TagFirst(REAL)),
        ("TagFirst+硬约束加分(杠杆,未提升)", TagFirst(REAL, hard_score=True)),
        ("TagFirst+硬约束+证据并列(杠杆,未提升)", TagFirst(REAL, hard_score=True, axes_tie=True)),
    ]

    print("=== v2 候选池内 top-3 / top-5 命中（gold_primary；分母=有推荐的题）===\n")
    # 有推荐题 = decide_ask 决策 ∈ {no_ask, ask_shade_soft}（追问题无推荐，不计首答）
    rec_ids = []
    for qid in sorted(pool.query_id.unique()):
        r = qmap.get(qid)
        if r is None:
            continue
        req, meta = agent.extract_constraints(r.query)
        ask = agent.decide_ask(req, meta)
        if ask["decision"] in ("ask_all", "ask_first"):
            continue
        rec_ids.append(qid)

    for label, idx in variants:
        tot = h3 = h5 = h3_any = n_leak = 0
        detail = []
        for qid in rec_ids:
            r = qmap[qid]
            rows = pool[pool.query_id == qid]
            prim = [a for a, gt in zip(rows.asin, rows.gold_type) if gt == "primary"]
            # 正确答案 = primary + extras（negative 是要避雷的，进 top-3 是避雷失败不是命中）
            gold_ok = set(rows.asin[(rows.label == "gold") & (rows.gold_type.isin(["primary", "extra"]))])
            gold_neg = set(rows.asin[(rows.label == "gold") & (rows.gold_type == "negative")])
            if not prim:
                continue
            req, _ = agent.extract_constraints(r.query)
            scored = idx.score_candidates("mixed", req, list(rows.asin))
            top = [a for a, _, _ in scored]
            hit3 = prim[0] in top[:3]
            hit5 = prim[0] in top[:5]
            hitany = bool(gold_ok & set(top[:3]))
            neg_leak = bool(gold_neg & set(top[:3]))
            tot += 1
            h3 += hit3; h5 += hit5; h3_any += hitany
            n_leak += neg_leak
            detail.append((qid, r.query_type, hit3, hit5, hitany,
                           (top.index(prim[0]) + 1) if prim[0] in top else None))
        print(f"── {label}（分母 {tot} 题）──")
        print(f"  首答命中 top-3: {h3}/{tot} = {h3/tot:.1%}  | top-5: {h5}/{tot} = {h5/tot:.1%}"
              f"  | 正确答案(primary+extras)进top-3: {h3_any}/{tot} = {h3_any/tot:.1%}"
              f"  | 负例泄漏top-3: {n_leak}")
        print("  逐题（id 类型 top3 top5 命中正确答案 primRank）：")
        for d in detail:
            print(f"    q{d[0]:>2} [{d[1]:<12}] top3={'✓' if d[2] else '✗'} "
                  f"top5={'✓' if d[3] else '✗'} 命中={'✓' if d[4] else '✗'} "
                  f"primRank={d[5] if d[5] else '未入榜'}")
        print()


if __name__ == "__main__":
    main()
