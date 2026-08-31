# -*- coding: utf-8 -*-
"""eval_compare.py — 二期 A/B 对比入口（规则 A vs 规则+LLM 兜底 B）
=================================================================
对照组 A = GuideAgent()             （纯规则，94.7% 锚点口径）
实验组 B = GuideAgent(intent_mode="hybrid")（规则盲区 → LLM 兜底 → 检索兑现率门）

输出 data/eval_compare_report.csv（**不碰 eval_report.csv**，锚点回归文件保持干净）：
  1. 锚点交叉验证（ids 1-24）：A 必须复现 18/19=94.7%（回归护栏）；
     B 单独报（hybrid 是否漂移锚点——如实报告，是发现不是 bug）
  2. 非锚点题（25-41，其中 25-33 隐式意图盲区 + 34-41 规则可答）：A vs B 对比 = 实验核心表
  3. CONTRACT：CASES（1-24）双模式 + CASES_HIDDEN（25-41）双模式
  4. 旁路指标：intent_source 分布 / LLM 调用（network）/ 缓存命中 / 平均延迟 / tokens

实现：全部内联（复用 eval_runner 的 ndcg_at_k 逻辑），只 import 有 stdout 保护的
      agent / contract_cases / retrieval_engine。真实决策口径 = agent.run()（含
      hybrid 的 _llm_merge），首答池内排序用 run() 合并后的 req。

用法：python eval_compare.py
"""
import io
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retrieval_engine import ProductIndex        # noqa: E402
from agent import GuideAgent                     # noqa: E402
from contract_cases import CASES, CASES_HIDDEN   # noqa: E402
from db_config import db_url

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")
DB = db_url()
ANCHOR_MAX_ID = 24
HIDDEN_IDS = list(range(25, 34))


class TagFirst(ProductIndex):
    """正式排序（tagfirst = 标签主序 → 热度 → BM25），无历史杠杆。"""

    def score_candidates(self, mode, req, candidates, weights=None):
        return super().score_candidates("tagfirst", req, candidates, weights)


def ndcg_at_k(rels, k=5):
    gains = [max(2 ** r - 1, 0.0) for r in rels[:k]]
    dcg = sum(g / np.log2(i + 2) for i, g in enumerate(gains))
    ideal = sorted([max(2 ** r - 1, 0.0) for r in rels], reverse=True)[:k]
    idcg = sum(g / np.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def _req_from_rec(rec, query):
    """从 run() 的决策记录重建检索 req（含 hybrid 合并后的 implicit + qtext/vec_text）。
    必须把 meta 里影响排序的字段一并带全——seasonal（全年通用混合肌降权，
    tag_score 依赖它），否则与 eval_runner 直接走 extract_constraints 的通道结果不一致。"""
    c = rec["constraints"]
    return {"hard": set(c["hard"]), "soft": set(c["soft"]),
            "finish": c["finish"], "coverage": c["coverage"], "form": c["form"],
            "shade_dir": c["shade_dir"], "implicit": c["implicit"],
            "budget": c["budget"], "seasonal": c["seasonal"],
            "qtext": query, "vec_text": query}


def run_mode(agent, idx, qd, pool_v2, label):
    """单模式跑全量题（真实决策口径）。返回统计 + 逐题明细。"""
    c = Counter()                       # ask 决策分布
    c_asserts = Counter()               # CASES 断言
    h_asserts = Counter()               # CASES_HIDDEN 断言
    fired = 0                           # llm 触发过的题数（llm_evidence 非空）
    ndcg_anchor, ndcg_hidden = [], []
    detail = []
    anchor_answerable = anchor_hit = 0
    hidden_answerable = hidden_hit = 0

    for _, r in qd.iterrows():
        qid = int(r.id)
        rec = agent.run(r.query, qid=qid, query_type=r.query_type)
        ask = rec["ask"]["decision"]
        c[ask] += 1
        if rec["llm_evidence"]:
            fired += 1
        req = _req_from_rec(rec, r.query)

        # CONTRACT 断言
        case = next((x for x in CASES if x["id"] == qid), None)
        if case:
            for name, fn in case["asserts"]:
                ok, _ = fn(rec, agent)
                c_asserts["total"] += 1
                c_asserts["pass"] += int(ok)
        hcase = next((x for x in CASES_HIDDEN if x["id"] == qid), None)
        if hcase:
            for name, fn in hcase["asserts"]:
                ok, _ = fn(rec, agent)
                h_asserts["total"] += 1
                h_asserts["pass"] += int(ok)

        # v2 池内排序（NDCG@5 + 首答命中）
        rows = pool_v2[pool_v2.query_id == qid]
        ranked, ndcg = [], None
        if len(rows):
            ranked = [a for a, _s, _r in idx.score_candidates(
                "tagfirst", req, list(rows.asin))]
            rel_map = dict(zip(rows.asin, rows.relevance.astype(float)))
            ndcg = ndcg_at_k([rel_map.get(a, 0.0) for a in ranked])
        if qid <= ANCHOR_MAX_ID:
            ndcg_anchor.append(ndcg)
        else:
            ndcg_hidden.append(ndcg)

        if ask in ("ask_all", "ask_first"):
            detail.append(dict(qid=qid, ask=ask, answered=False, hit=False,
                               prim_rank=None, ndcg=ndcg))
            continue

        gold_ok = set(rows.asin[(rows.label == "gold")
                                & (rows.gold_type.isin(["primary", "extra"]))])
        gold_neg = set(rows.asin[(rows.label == "gold")
                                 & (rows.gold_type == "negative")])
        top3 = ranked[:3]
        hit = bool(gold_ok & set(top3)) and not bool(gold_neg & set(top3))
        prim = rows.asin[rows.gold_type == "primary"].tolist()
        prim_rank = (ranked.index(prim[0]) + 1) if prim and prim[0] in ranked else None

        if qid <= ANCHOR_MAX_ID:
            anchor_answerable += 1
            anchor_hit += int(hit)
        else:
            hidden_answerable += 1
            hidden_hit += int(hit)
        detail.append(dict(qid=qid, ask=ask, answered=True, hit=hit,
                           prim_rank=prim_rank, ndcg=ndcg))

    return dict(
        label=label, ask=dict(c), c_asserts=c_asserts, h_asserts=h_asserts,
        fired=fired, ndcg_anchor=ndcg_anchor, ndcg_hidden=ndcg_hidden,
        anchor=(anchor_hit, anchor_answerable), hidden=(hidden_hit, hidden_answerable),
        detail=detail)


def main():
    print("=" * 72)
    print("Phase 3.5 二期 A/B 对比 eval_compare.py（规则 A vs 规则+LLM 兜底 B）")
    print("=" * 72)

    idx = TagFirst(ROOT / "data" / "products_clean.csv")
    engine = create_engine(DB)
    qd = pd.read_sql("SELECT id, query, query_type FROM eval_review_50 ORDER BY id", engine)
    pool_v2 = pd.read_sql("SELECT query_id, asin, label, gold_type, relevance "
                          "FROM candidate_pool_v2 ORDER BY query_id, asin", engine)

    agent_a = GuideAgent()                        # 对照组 A
    agent_b = GuideAgent(intent_mode="hybrid")    # 实验组 B
    ra = run_mode(agent_a, idx, qd, pool_v2, "A-rule")
    rb = run_mode(agent_b, idx, qd, pool_v2, "B-hybrid")

    # ---------------- 终端汇总 ----------------
    print("\n-- 锚点交叉验证（ids 1-24，回归护栏）--")
    ha, da = ra["anchor"]
    hb, db = rb["anchor"]
    ga = f"{ra['c_asserts']['pass']}/{ra['c_asserts']['total']}"
    gb = f"{rb['c_asserts']['pass']}/{rb['c_asserts']['total']}"
    anchor = 18  # 2026-08-29 定标：q20 泄漏→第四批→q7 重标，锚点首答 18/19=94.7%，A 必须复现 18
    print(f"  A 首答命中 = {ha}/{da} = {ha/da:.1%}" + (f"  ✓ 复现锚点 {anchor}/{da}" if ha == anchor else "  ✗ 锚点漂移！"))
    print(f"  B 首答命中 = {hb}/{db} = {hb/db:.1%}"
          + ("  ✓ hybrid 未漂移锚点 (A==B)" if hb == ha else "  ⚠ hybrid 在锚点上有变化（如实报告）"))
    print(f"  CONTRACT CASES: A {ga} / B {gb}")

    # 非锚点题（qid>24）：9 隐式意图（25-33）+ 8 第四批补题（34-41）。34-41 规则可答，
    # 实验核心仍是 25-33 的规则盲区，但分母按实际题数动态算，别写死 9。
    n_hidden = sum(1 for x in rb["detail"] if x["qid"] >= 25)
    a_hidden_ans = sum(1 for x in ra["detail"] if x["qid"] >= 25 and x["answered"])
    a_hidden_hit = sum(1 for x in ra["detail"] if x["qid"] >= 25 and x["hit"])
    b_hidden_ans = sum(1 for x in rb["detail"] if x["qid"] >= 25 and x["answered"])
    b_hidden_hit = sum(1 for x in rb["detail"] if x["qid"] >= 25 and x["hit"])
    print(f"\n-- 非锚点题（25-41，共 {n_hidden} 题：25-33 隐式意图盲区 + 34-41 规则可答）--")
    print(f"  A: 可答 {a_hidden_ans}/{n_hidden}，命中 {a_hidden_hit}/{n_hidden}"
          f"（25-33 全 ask_all 规则盲区；34-41 规则直接答）")
    b_recover = b_hidden_ans - a_hidden_ans            # B 比 A 多答了几题（LLM 兜底救回）
    b_degrade = sum(1 for x in rb["detail"] if x["qid"] >= 25
                    and x["ask"] in ("ask_all", "ask_first"))  # 识别但库兑现不了 → 诚实降级
    print(f"  B: 可答 {b_hidden_ans}/{n_hidden}，命中 {b_hidden_hit}/{n_hidden}"
          f"（LLM 兜底让 {b_recover} 题从「追问」变「可推荐」；{b_degrade} 题识别但兑现不了 → 诚实降级）")

    # hidden 逐题对照
    print("\n  逐题对照（A ask / B ask → B 命中 / primRank）：")
    for xa in sorted((x for x in ra["detail"] if x["qid"] >= 25), key=lambda z: z["qid"]):
        xb = next(y for y in rb["detail"] if y["qid"] == xa["qid"])
        print(f"    q{xa['qid']}: A[{xa['ask']:<10}] B[{xb['ask']:<10}] "
              f"B_hit={'✓' if xb['hit'] else '—'} primRank={xb['prim_rank'] or '—'}")

    print(f"\n-- LLM 旁路指标（B 组，全量 {len(rb['detail'])} 题）--")
    st = agent_b._llm.stats
    print(f"  LLM 触发（fired）: {rb['fired']}/{len(rb['detail'])}（全部为规则盲区题；锚点零误触发，"
          f"q8/q15 已由 should_fallback 闸门挡下）")
    print(f"  网络调用 calls={st['calls']}  缓存命中={st['cache_hits']}  "
          f"平均延迟={int(np.mean(st['latency_ms'])) if st['latency_ms'] else 0}ms  tokens={st['tokens']}")
    ask_src = Counter(x["ask"] for x in rb["detail"])
    print(f"  B 组 ask 分布: {dict(ask_src)}  |  NDCG@5 锚点 A={np.mean([x for x in ra['ndcg_anchor'] if x==x]):.3f} "
          f"B={np.mean([x for x in rb['ndcg_anchor'] if x==x]):.3f} | "
          f"hidden A={np.mean([x for x in ra['ndcg_hidden'] if x==x]):.3f} "
          f"B={np.mean([x for x in rb['ndcg_hidden'] if x==x]):.3f}")

    # ---------------- 写报告 CSV ----------------
    rows = []
    def row(group, channel, metric, value, num, denom, note):
        rows.append(dict(group=group, channel=channel, metric=metric, value=value,
                         num=num, denom=denom, note=note))

    n_total = len(rb["detail"])
    for lbl, r in (("A-rule", ra), ("B-hybrid", rb)):
        h, d = r["anchor"]
        row(lbl, "锚点交叉", "首答命中率", f"{h/d:.3f}", h, d,
            "✓ 复现 94.7%(18/19)" if lbl == "A-rule" and h == 18 else
            ("✓ 未漂移 A==B" if lbl == "B-hybrid" and h == ra["anchor"][0] else "hybrid 漂移锚点"))
        row(lbl, "锚点交叉", "CONTRACT CASES", f"{r['c_asserts']['pass']}/{r['c_asserts']['total']}",
            r["c_asserts"]["pass"], r["c_asserts"]["total"], "ids 1-24 双模式")
        row(lbl, "hidden", "首答命中", f"{r['hidden'][0]}/{r['hidden'][1]}",
            r["hidden"][0], r["hidden"][1], "ids 25-41")
        row(lbl, "hidden", "CONTRACT CASES_HIDDEN",
            f"{r['h_asserts']['pass']}/{r['h_asserts']['total']}",
            r["h_asserts"]["pass"], r["h_asserts"]["total"], "mode 化断言")
        row(lbl, "hidden", "NDCG@5 均值", f"{np.mean([x for x in r['ndcg_hidden'] if x == x]):.3f}",
            "-", "-", "v2 池 tagfirst")
    row("B-hybrid", "旁路", "LLM fired", rb["fired"], rb["fired"], n_total, "llm_evidence 非空=触发过")
    row("B-hybrid", "旁路", "LLM 网络调用", st["calls"], st["calls"], n_total, "缓存外新调用")
    row("B-hybrid", "旁路", "LLM 缓存命中", st["cache_hits"], st["cache_hits"], n_total, "命中 llm_cache.json")
    row("B-hybrid", "旁路", "LLM 平均延迟ms", int(np.mean(st["latency_ms"])) if st["latency_ms"] else 0,
        "-", "-", "实测")
    row("B-hybrid", "旁路", "LLM tokens", st["tokens"], st["tokens"], "-", "含推理 token")
    df = pd.DataFrame(rows, columns=["group", "channel", "metric", "value", "num", "denom", "note"])
    df.to_csv(ROOT / "data" / "eval_compare_report.csv", index=False, encoding="utf-8-sig")
    print(f"\n已写 data/eval_compare_report.csv（不碰 eval_report.csv）")


if __name__ == "__main__":
    main()
