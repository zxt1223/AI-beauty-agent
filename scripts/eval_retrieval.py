# -*- coding: utf-8 -*-
"""
Phase 1 检索层 · 评测 runner（候选池内，对标 C4 + benchmark06）
=================================================
对 11 条 need query × candidate_pool（金标准 + 50 负候选），
跑四模式消融 → Recall@5 / MRR / NDCG@5 / 避雷准确率。

模式定义（段 B 四模式）：
  bm25  ：纯 BM25（手写，title+brand 英文文档）
  tag   ：纯标签分（知识分层：肤质/妆效/遮盖/质地/色号 + 隐式意图 + 置信度降权）
  vec   ：纯向量（bge-small-en-v1.5，query 用 v13 query_rewrite 注入隐式关键词）
  mixed ：BM25 + 标签 + 热度 + 向量 + 动态路由偏置（完整引擎）

NDCG 在候选池内计算（避免全库排序虚高）：
  - gain = 2^rel - 1（rel 1.0 primary / 0.8 extra；negative/候选 取 0）
  - IDCG 用候选池内 rel>0 商品理想排序
避雷准确率 = 1 - negative 商品进入 top5 的比例（仅对有 negative 的 query）。
"""
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from retrieval_engine import ProductIndex

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")
MODES = ["bm25", "tag", "vec", "mixed"]


def ndcg_at_k(rels, k=5):
    """rels: 已排序的相关度序列。gain=2^rel-1，rel<0 记 0。"""
    gains = [max(2 ** r - 1, 0.0) for r in rels[:k]]
    dcg = sum(g / np.log2(i + 2) for i, g in enumerate(gains))
    ideal = sorted([max(2 ** r - 1, 0.0) for r in rels], reverse=True)[:k]
    idcg = sum(g / np.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def main():
    idx = ProductIndex()
    idx.enable_vectors()  # 段 B：加载 bge + 商品文档向量化（首次需下载模型）
    qd = pd.read_csv(ROOT / "data" / "evaluation_set.csv")
    cp = pd.read_csv(ROOT / "data" / "candidate_pool.csv")

    results = {m: {"ndcg": [], "recall": [], "mrr": [], "avoid": []} for m in MODES}
    detail_rows = []

    for qi, row in qd.iterrows():
        qid = qi + 1
        req = idx.parse_query(row)
        route = idx.route_query(row["query"])
        pool = cp[cp["query_id"] == qid]
        rel_map = dict(zip(pool["asin"], pool["relevance"]))
        gold = [a for a, r in rel_map.items() if r > 0]
        neg = [a for a, r in rel_map.items() if r < 0]

        for mode in MODES:
            scored = idx.score_candidates(mode, req, list(rel_map.keys()))
            if mode == "mixed":
                scored = idx.apply_route(route, req, scored)
            ranked = [a for a, _, _ in scored]
            top5 = ranked[:5]
            rels = [rel_map.get(a, 0.0) for a in ranked]

            n_hit = len(set(gold) & set(top5))
            recall = n_hit / len(gold) if gold else np.nan
            mrr = 0.0
            for j, a in enumerate(ranked):
                if rel_map.get(a, 0) > 0:
                    mrr = 1.0 / (j + 1)
                    break
            ndcg = ndcg_at_k(rels)
            avoid = 1 - len(set(neg) & set(top5)) / len(neg) if neg else np.nan

            results[mode]["ndcg"].append(ndcg)
            results[mode]["recall"].append(recall)
            results[mode]["mrr"].append(mrr)
            results[mode]["avoid"].append(avoid)
            detail_rows.append({"query_id": qid, "mode": mode, "route": route,
                                "ndcg@5": round(ndcg, 3), "recall@5": round(recall, 3),
                                "mrr": round(mrr, 3),
                                "avoid": round(avoid, 3) if avoid == avoid else None,
                                "top5": "|".join(top5)})

    # ---- 汇总对比表 ----
    print("=" * 64)
    print("候选池内检索消融（11 query × candidate_pool）")
    print("=" * 64)
    header = f"{'模式':<8}{'NDCG@5':>10}{'Recall@5':>10}{'MRR':>8}{'避雷准确率':>10}"
    print(header)
    print("-" * len(header))
    for mode in MODES:
        r = results[mode]
        def mean(xs):
            xs = [x for x in xs if x == x]
            return f"{np.mean(xs):.3f}" if xs else "  -  "
        print(f"{mode:<8}{mean(r['ndcg']):>10}{mean(r['recall']):>10}{mean(r['mrr']):>8}{mean(r['avoid']):>10}")
    print("-" * len(header))

    # ---- 每 query 明细（badcase 分析用） ----
    print("\n每 query 明细（mixed 模式，route + top5）：")
    for _, d in pd.DataFrame(detail_rows).sort_values(["query_id", "mode"]).iterrows():
        if d["mode"] == "mixed":
            print(f"  q{int(d['query_id']):>2} [{d['route']}] NDCG {d['ndcg@5']:.3f} "
                  f"Recall {d['recall@5']:.3f} MRR {d['mrr']:.3f} 避雷 {d['avoid'] if d['avoid']==d['avoid'] else '-'}")
            print(f"       top5: {d['top5']}")

    # 存明细 CSV（badcase 登记表素材）
    pd.DataFrame(detail_rows).to_csv(ROOT / "data" / "retrieval_ablation.csv",
                                     index=False, encoding="utf-8-sig")
    print("\n明细已存 data/retrieval_ablation.csv")


if __name__ == "__main__":
    main()
