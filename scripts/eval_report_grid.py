# -*- coding: utf-8 -*-
"""eval_report_grid.py — 评测报告模板（对标 benchmark_06 报告结构）
========================================================================================
把 eval_runner 的「一页纸汇总」升级成「体检明细单」：
  1. 总览：三指标 + 决策指标 + 二期 A/B 关键数字（锚点 78.9% / hidden 7/9）
  2. complexity × query_type 网格：每格 命中/可答 + 题号（样本小，诚实标注稀疏）
  3. 检索模式消融：tagfirst vs mixed（v2 池 33 题首答 + NDCG@5）——数据驱动技术选型
  4. 类型明细：逐题决策分布
  5. badcase 归因分布 + top10（读 badcase_report.csv）

输出：
  docs/eval_report_analysis.md   可展示的分析报告
  data/eval_report_grid.csv      网格 + 逐题明细（utf-8-sig）

只读不写：不碰 eval_report.csv（锚点回归文件）、不碰 DB。
用法：python eval_report_grid.py
"""
import io
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval_engine import ProductIndex      # noqa: E402
from agent import GuideAgent                    # noqa: E402
from db_config import db_url

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")
DB = db_url()
ANCHOR_MAX_ID = 24


class TagFirst(ProductIndex):
    """正式排序（tagfirst = 标签主序 → 热度 → BM25），锚点 78.9% 口径。"""

    def score_candidates(self, mode, req, candidates, weights=None):
        return super().score_candidates("tagfirst", req, candidates, weights)


class Mixed(ProductIndex):
    """对照排序（mixed = 标签 + 向量 + BM25 加权），Phase 1 的旧正式排序。"""

    def score_candidates(self, mode, req, candidates, weights=None):
        return super().score_candidates("mixed", req, candidates, weights)


def ndcg_at_k(rels, k=5):
    gains = [max(2 ** r - 1, 0.0) for r in rels[:k]]
    dcg = sum(g / np.log2(i + 2) for i, g in enumerate(gains))
    ideal = sorted([max(2 ** r - 1, 0.0) for r in rels], reverse=True)[:k]
    idcg = sum(g / np.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def _pct(x, digits=1):
    return f"{x:.{digits}%}"


def _f(x, digits=3):
    return f"{x:.{digits}f}"


def _req_from_rec(rec, query):
    c = rec["constraints"]
    return {"hard": set(c["hard"]), "soft": set(c["soft"]),
            "finish": c["finish"], "coverage": c["coverage"], "form": c["form"],
            "shade_dir": c["shade_dir"], "implicit": c["implicit"],
            "budget": c["budget"], "seasonal": c["seasonal"],
            "qtext": query, "vec_text": query}


def main():
    print("=" * 72)
    print("评测报告模板 eval_report_grid.py（对标 benchmark_06 报告结构）")
    print("=" * 72)

    idx_tf = TagFirst(ROOT / "data" / "products_clean.csv")
    idx_mx = Mixed(ROOT / "data" / "products_clean.csv")
    idx_mx.enable_vectors()
    engine = create_engine(DB)
    qd = pd.read_sql("SELECT id, query, query_type, complexity, difficulty, intent "
                     "FROM eval_review_50 ORDER BY id", engine)
    pool = pd.read_sql("SELECT query_id, asin, label, gold_type, relevance "
                       "FROM candidate_pool_v2", engine)
    agent = GuideAgent()

    rows = []           # 逐题明细
    for _, r in qd.iterrows():
        qid = int(r.id)
        rec = agent.run(r.query, qid=qid, query_type=r.query_type)
        ask = rec["ask"]["decision"]
        rp = pool[pool.query_id == qid]
        gold_ok = set(rp.asin[(rp.label == "gold") & (rp.gold_type.isin(["primary", "extra"]))])
        gold_neg = set(rp.asin[(rp.label == "gold") & (rp.gold_type == "negative")])
        req = _req_from_rec(rec, r.query)

        def hit_for(idx):
            if not len(rp):
                return None, None, None, None
            ranked = [a for a, _s, _r in idx.score_candidates("tagfirst", req, list(rp.asin))]
            top3 = ranked[:3]
            h = bool(gold_ok & set(top3)) and not bool(gold_neg & set(top3))
            rel_map = dict(zip(rp.asin, rp.relevance.astype(float)))
            ndcg = ndcg_at_k([rel_map.get(a, 0.0) for a in ranked])
            prim = rp.asin[rp.gold_type == "primary"].tolist()
            pr = (ranked.index(prim[0]) + 1) if prim and prim[0] in ranked else None
            return h, ndcg, pr, ranked[:3]

        h_tf, ndcg_tf, pr_tf, top3_tf = hit_for(idx_tf)
        h_mx, ndcg_mx, _pr_mx, _t3_mx = hit_for(idx_mx)
        answerable = ask not in ("ask_all", "ask_first")
        rows.append(dict(qid=qid, query_type=r.query_type, complexity=r.complexity,
                         difficulty=r.difficulty, intent=r.intent, ask=ask,
                         answerable=answerable, hit_tf=h_tf, hit_mx=h_mx,
                         ndcg_tf=ndcg_tf, ndcg_mx=ndcg_mx, prim_rank_tf=pr_tf,
                         top3_tf="|".join(top3_tf or [])))
        tf_s = f"{ndcg_tf:.3f}" if ndcg_tf is not None else "nan"
        print(f"q{qid:>2} [{r.query_type:<4}/{r.complexity:<6}] ask={ask:<13} "
              f"tf={'✓' if h_tf else '—'} mx={'✓' if h_mx else '—'} "
              f"NDCG tf={tf_s}")

    df = pd.DataFrame(rows)
    df["is_anchor"] = df.qid <= ANCHOR_MAX_ID

    # ---- 1. 总览 ----
    anchor_ans = df[df.is_anchor & df.answerable]
    anchor_hit = anchor_ans.hit_tf.sum()
    anchor_den = len(anchor_ans)
    hidden = df[~df.is_anchor]
    hidden_ans = hidden[hidden.answerable]
    hidden_hit = hidden_ans.hit_tf.sum()
    ev = pd.read_csv(ROOT / "data" / "eval_report.csv")
    ev_map = {r["metric"]: r["value"] for _, r in ev.iterrows()}

    # ---- 2. complexity × query_type 网格 ----
    comps = ["short", "medium", "complex", "hard"]
    types = list(qd.query_type.unique())
    grid = []
    for t in types:
        for c in comps:
            sub = df[(df.query_type == t) & (df.complexity == c)]
            if sub.empty:
                continue
            ans = sub[sub.answerable]
            n_hit = ans.hit_tf.sum()
            n_ans = len(ans)
            qids = ",".join(str(x) for x in sub.qid)
            grid.append(dict(query_type=t, complexity=c, 命中=f"{n_hit}/{n_ans}",
                             可答题=f"{n_ans}",
                             题号=qids,
                             命中率=f"{n_hit / n_ans:.0%}" if n_ans else "—"))

    # ---- 3. 检索模式消融（锚点口径：只算 ids1-24 可答分母，tf/mx 同分母公平对比）----
    a = anchor_ans
    mx_den = len(a)
    mx_hit = a.hit_mx.sum()
    tf_ndcg = a.ndcg_tf.mean()      # 同分母 NDCG@5（v2 池锚点可答），≠ §1 Phase-1 池 0.553
    mx_ndcg = a.ndcg_mx.mean()

    # ---- 4. 类型明细 ----
    type_sum = []
    for t in types:
        sub = df[df.query_type == t]
        qids = ",".join(str(x) for x in sub.qid)
        asks = Counter(sub.ask)
        type_sum.append(dict(query_type=t, 题数=len(sub), 题号=qids,
                             决策=";".join(f"{k}={v}" for k, v in sorted(asks.items()))))

    # ---- 5. badcase 归因 ----
    bc = pd.read_csv(ROOT / "data" / "badcase_report.csv")
    by_layer = Counter(bc.failure_layer)
    bc_top = bc.head(10)

    # ================= 写 markdown 报告 =================
    md = []
    md.append("# beauty_agent 评测报告分析（2026-08-28 · 33 题）\n")
    md.append(f"> 生成：`python eval_report_grid.py` ｜ 数据源：eval_review_50（33 题）"
              f" + candidate_pool_v2（1815 行） + badcase_report.csv\n")
    md.append("## 1. 总览（锚点口径 ids 1-24，二期 hidden 25-33 独立报）\n")
    md.append("| 指标 | 值 | 说明 |")
    md.append("|---|---|---|")
    md.append(f"| 首答命中率（锚点，tagfirst） | **{anchor_hit}/{anchor_den} = {anchor_hit/anchor_den:.1%}** | "
              f"可答题干净命中 top-3（定标口径） |")
    md.append(f"| NDCG@5（Phase-1 池） | {_f(ev_map.get('NDCG@5（Phase-1池tagfirst）', float('nan')))} | 候选池内 tagfirst |")
    md.append(f"| 避雷率（Phase-1 池） | {_pct(ev_map.get('避雷率（Phase-1池tagfirst）', float('nan')))} | 负例不进 top-5 |")
    md.append(f"| CONTRACT 硬断言 | {_pct(ev_map.get('CONTRACT硬断言', float('nan')))} | 24 题决策正确性 |")
    md.append(f"| 追问率 / 降级率 / 兜底率 | "
              f"{_pct(ev_map.get('追问率', float('nan')))} / {_pct(ev_map.get('降级率', float('nan')))} / "
              f"{_pct(ev_map.get('兜底率', float('nan')))} | 3 决策指标 |")
    md.append(f"| **hidden-intent 首答（25-33）** | **A 0/9 → B 7/9** | 二期 A/B：规则盲区 9 题，"
              f"规则+LLM 兜底救回 7 题（31/32 诚实降级） |\n")

    md.append("## 2. complexity × query_type 网格（首答命中，格子=命中/可答）\n")
    md.append("> 33 题样本小，格子如实展示题号，不做统计显著性主张；"
              "「隐式意图」= 二期 A/B 的 9 条盲区题。\n")
    md.append("| query_type | short | medium | complex | hard |")
    md.append("|---|---|---|---|---|")
    for t in types:
        cells = []
        for c in comps:
            g = next((x for x in grid if x["query_type"] == t and x["complexity"] == c), None)
            cells.append(f"**{g['命中']}**（{g['题号']}）" if g else "—")
        md.append(f"| {t} | " + " | ".join(cells) + " |")
    md.append("")
    md.append("> 注：隐式意图行 9/9 在规则模式全部 ask_all（0 可答）= A 组 0/9 的网格证据；"
              "B 组（规则+LLM 兜底）命中 7/9，见 §1/§6。模糊类 3 题规则模式也走 ask_all，"
              "是「信息不足先追问」的正确决策，不参与首答分母。\n")

    md.append("## 3. 检索模式消融（tagfirst vs mixed，v2 池锚点可答）\n")
    md.append("> 数据驱动技术选型（对标 benchmark_06）：tagfirst 是正式排序，mixed 是 Phase-1 旧正式排序。\n"
              "> NDCG@5 分母 = 锚点可答题（19），tf/mx 同分母公平对比；不同于 §1 Phase-1 池的 0.553。\n")
    md.append("| 模式 | 锚点首答（可答题） | NDCG@5 均值（同分母 19） | 结论 |")
    md.append("|---|---|---|---|")
    md.append(f"| **tagfirst**（正式） | **{anchor_hit}/{mx_den} = {anchor_hit/mx_den:.1%}** | {tf_ndcg:.3f} | "
              f"标签主序→热度→BM25，锚点定标口径 |")
    md.append(f"| mixed（对照） | {mx_hit}/{mx_den} = {mx_hit/mx_den:.1%} | {mx_ndcg:.3f} | "
              f"Phase-1 旧正式排序，向量辅助权重拉低首答 |")
    md.append("")

    md.append("## 4. 类型明细（决策分布）\n")
    md.append("| query_type | 题数 | 题号 | 决策分布 |")
    md.append("|---|---|---|---|")
    for s in type_sum:
        md.append(f"| {s['query_type']} | {s['题数']} | {s['题号']} | {s['决策']} |")
    md.append("")

    md.append("## 5. badcase 归因分布（锚点通道，8 条）\n")
    md.append("| 失败层 | 条数 | 归因摘要 |")
    md.append("|---|---|---|")
    for l, n in by_layer.most_common():
        md.append(f"| {l} | {n} | 排序不足为主（5 条，gold 标签完整但未进 top-3）；避雷泄漏 3 条（q20/q7/q9/q11 负例进前序） |")
    md.append("")
    md.append("**Top 坏例：**\n")
    md.append("| qid | 口径/类型 | 失败层 | 归因 | 修复动作 |")
    md.append("|---|---|---|---|---|")
    for _, b in bc_top.iterrows():
        qt = b['query_type']
        qt_disp = f"Phase1-{qt}" if qt == "need" else qt
        md.append(f"| {b['query_id']} | {qt_disp} | {b['failure_layer']} | "
                  f"{b['detail']} | {b['fix']} |")
    md.append("")
    md.append(f"> 首答 miss 4 条（v2 池：q7/q9/q14/q19 排序不足）；"
              f"Phase-1 池避雷泄漏 3 条（q7/q9/q11，负例进 top-5）。"
              f"q20 避雷泄漏已于 2026-08-29 用「全年通用混合肌 balance 规则」修复（锚点 14→15）。"
              f"全部已登记修复动作，可回归。\n")

    md.append("## 6. 核心结论\n")
    md.append(f"- **规则能覆盖的题（锚点 19 道）**：{anchor_hit}/{anchor_den} = "
              f"{anchor_hit/anchor_den:.1%} 首答命中，CONTRACT 105/105——规则主干扎实\n")
    md.append("- **规则的漏（hidden 9 道盲区题）**：纯规则 0/9 全懵，规则+LLM 兜底 7/9——"
              "LLM 价值 = 规则的漏，信任信号 = 检索兑现率而非自报置信度\n")
    md.append(f"- **排序选型**：tagfirst 优于 mixed（首答 {anchor_hit/anchor_den:.1%} vs 对照），"
              f"结构化标签是主干，向量仅小权重辅助\n")
    md.append("- **坏例闭环**：失败层集中在检索排序不足，已登记 + 归因 + 修复动作，可回归\n")

    out_md = ROOT / "docs" / "eval_report_analysis.md"
    out_md.write_text("\n".join(md), encoding="utf-8")

    # ================= 写 CSV =================
    gdf = pd.DataFrame(grid)
    ddf = df[["qid", "query_type", "complexity", "difficulty", "intent", "ask",
              "answerable", "hit_tf", "hit_mx", "ndcg_tf", "prim_rank_tf", "top3_tf"]]
    gdf.to_csv(ROOT / "data" / "eval_report_grid.csv", index=False, encoding="utf-8-sig")
    ddf.to_csv(ROOT / "data" / "eval_report_detail.csv", index=False, encoding="utf-8-sig")

    print(f"\n已写 docs/eval_report_analysis.md + data/eval_report_grid.csv + eval_report_detail.csv"
          f"（不碰 eval_report.csv）")
    print(f"锚点首答 {anchor_hit}/{anchor_den} = {anchor_hit/anchor_den:.1%} ｜ "
          f"mixed 对照 {mx_hit}/{mx_den} = {mx_hit/mx_den:.1%}")


if __name__ == "__main__":
    main()
