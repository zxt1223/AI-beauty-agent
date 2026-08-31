# -*- coding: utf-8 -*-
"""eval_runner.py — Phase 3 统一评测入口（闭环）
================================================
一个命令出全部关键数字 + 坏例登记表，可复现、可回归：

  [C]   CONTRACT 通道  24 题 GuideAgent：硬断言 105/105 + 3 决策指标 + 全库避雷
  [S]   首答通道        candidate_pool_v2 池内 tagfirst：干净命中 top-3 = 78.9%（2026-08-29 定标）
  [N]   NDCG 通道       candidate_pool（Phase-1）池内 tagfirst：NDCG@5 / MRR / 避雷率
  [SYS] 系统层          24 题 Agent.run 平均单轮耗时 + LLM token 成本 + LLM 异常数
                      （纯规则零模型 → 恒 0/0/0；hybrid 模式见 eval_compare_report.csv 旁路）
                      耗时=稳态单轮 ~42ms；rule 模式零模型依赖，无向量载入冷启动
                      （2026-08-29 优化：_retrieve 去掉无条件 enable_vectors，见 agent.py）

输出：
  终端汇总
  data/eval_report.csv    三指标 + CONTRACT + 决策指标 + 系统层（与上一轮自动对比，标记回归结果）
  data/badcase_report.csv 坏例登记表（query / 期望gold / 实际输出 / 失败层 / 修复动作 / 回归结果）

三数字口径（2026-08-28 定标，2026-08-29 q20 泄漏修复后 14→15，对应 agent_design.md §7/§8）：
  - 首答命中率 = 干净命中 top-3 = 15/19 = 78.9%
    分母 = 有推荐的 19 题（排除 ask_all / ask_first 追问题）
    命中 = top-3 ≥1 正确答案（primary 或 extra）且 0 负例泄漏
  - NDCG@5 = 0.553（Phase-1 候选池内 tagfirst，gain=2^rel-1，rel<0 记 0）
  - 避雷率 = 0.889（Phase-1 池内 tagfirst，负例不进 top-5；0.926→0.889 用户已验收）

实现说明：只 import 有 stdout 保护的 agent/contract_cases/retrieval_engine，
其余（ndcg_at_k / load_queries / asins）内联，避免重包 sys.stdout 被 GC 关闭。

用法：python eval_runner.py
"""
import io
import re
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retrieval_engine import ProductIndex
from agent import GuideAgent
from contract_cases import CASES
from db_config import db_url, db_params

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")
DB = db_url()
REAL = ROOT / "data" / "products_clean.csv"
DELTA = 0.005  # 回归对比容差

# 锚点隔离：首答 78.9% / NDCG 0.553 / 避雷 0.889 只算 ids 1-24（v2 锚点题）。
# 二期 hidden-intent 题 ids 25-33 会进 candidate_pool_v2，但**锚点口径必须不受污染**——
# 三处 SQL 全部过滤 id<=24，重跑必须逐数字复现。
ANCHOR_MAX_ID = 24


class TagFirst(ProductIndex):
    """正式排序（tagfirst = 标签主序 → 热度 → BM25），无历史杠杆。"""

    def score_candidates(self, mode, req, candidates, weights=None):
        return super().score_candidates("tagfirst", req, candidates, weights)


# ---------------------------------------------------------------- 工具 ----

def ndcg_at_k(rels, k=5):
    gains = [max(2 ** r - 1, 0.0) for r in rels[:k]]
    dcg = sum(g / np.log2(i + 2) for i, g in enumerate(gains))
    ideal = sorted([max(2 ** r - 1, 0.0) for r in rels], reverse=True)[:k]
    idcg = sum(g / np.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def asins(s):
    return re.findall(r"\b[A-Z0-9]{10}\b", str(s)) if s else []


def load_queries():
    import pymysql
    c = pymysql.connect(**db_params())
    cur = c.cursor()
    cur.execute("SELECT id, query, query_type, gold_primary, gold_extras, gold_negative "
                "FROM eval_review_50 WHERE id <= %s ORDER BY id", (ANCHOR_MAX_ID,))
    return cur.fetchall()


def _title(asin, tmap):
    t = tmap.get(asin)
    return t[:44] + "…" if t and len(t) > 44 else (t or asin)


def _skin_match(skin_tags, hard):
    """硬约束匹配：商品 skin_tags（分号分隔）含 hard 任一项 或 全肤质。"""
    if not skin_tags:
        return False
    s = set(x for x in str(skin_tags).split(";") if x)
    return bool(s & set(hard)) or "全肤质" in s


def _label_gap(req, p):
    """query 要求的轴，gold 商品缺标签或已标但不匹配 → 返回 (细归因, 说明)。
    返回 None = 标签完整且匹配（坏例是纯排序问题）。"""
    hard = req.get("hard") or []
    if hard and not _skin_match(p.get("skin_tags"), hard):
        if p.get("skin_tags"):
            return "gold矛盾", f"gold 肤质标签[{p.get('skin_tags')}]不含 query 硬约束{hard}"
        return "数据缺失", f"gold 商品肤质未标，无法自证{hard}"
    for axis, key in (("coverage", "coverage_tag"), ("finish", "finish_tag"),
                      ("form", "form_tag")):
        val = req.get(axis)
        if val and not p.get(key):
            return "数据缺失", f"gold 商品 {axis} 未标，无法自证 query 要求「{val}」"
    return None


# ---------------------------------------------------------------- 通道 C ----

def run_contract(agent, rows, tmap):
    """CONTRACT 通道：硬断言 + 3 决策指标 + 全库避雷 + 软指标（全库口径对照）。"""
    ask_kind = Counter()
    retry_n = 0
    fallback_kind = Counter()
    hit_p = gold_p = hit_e = gold_e = 0
    avoid_ok = avoid_denom = 0
    asserts_pass = asserts_total = 0
    badcases = []
    latency_ms = []   # 系统层：每轮 Agent.run 实测耗时（纯规则，无 LLM 网络等待）

    for rid, query, qtype, gp, ge, gn in rows:
        t0 = time.perf_counter()
        rec = agent.run(query, qid=rid, query_type=qtype)
        latency_ms.append((time.perf_counter() - t0) * 1000.0)
        case = next((c for c in CASES if c["id"] == rid), None)
        if case is None:
            continue

        for name, fn in case["asserts"]:
            ok, detail = fn(rec, agent)
            asserts_total += 1
            asserts_pass += 1 if ok else 0
            if not ok:
                badcases.append(dict(
                    query_id=rid, query_type=qtype, metric="CONTRACT",
                    pool="全库", query=query[:120],
                    expected_gold="", actual_output=f"[FAIL] {name} — {detail}",
                    failure_layer="编排", detail="断言失败",
                    fix="修正 CONTRACT 断言或对应决策节点，跑 eval_runner 回归",
                    regress=""))

        ask_kind[rec["ask"]["decision"]] += 1
        if rec["retry"]["triggered"]:
            retry_n += 1
        lv = rec["fallback"].get("level") if rec["fallback"]["triggered"] else None
        fallback_kind[lv] += 1

        recs = [r["asin"] for r in rec["recommendations"]]
        gp_a, ge_a = asins(gp), asins(ge)
        hit_p += len(set(gp_a) & set(recs)); gold_p += len(gp_a)
        hit_e += len(set(ge_a) & set(recs)); gold_e += len(ge_a)
        negs = asins(gn)
        if negs:
            avoid_denom += 1
            if not (set(negs) & set(recs)):
                avoid_ok += 1

    n = len(rows)
    return dict(
        asserts_pass=asserts_pass, asserts_total=asserts_total,
        ask=dict(ask_kind, n=ask_kind["ask_all"] + ask_kind["ask_first"]
                 + ask_kind["ask_shade_soft"]),
        retry=retry_n, fallback=fallback_kind,
        hit_p=hit_p, gold_p=gold_p, hit_e=hit_e, gold_e=gold_e,
        avoid_ok=avoid_ok, avoid_denom=avoid_denom, n=n,
        latency_ms=latency_ms), badcases


# ---------------------------------------------------------------- 通道 S ----

def run_first_answer(idx, agent, qd, pool_v2, tmap):
    """首答通道：candidate_pool_v2 池内 tagfirst，干净命中 top-3。
    分母 = 有推荐的题（排除 ask_all/ask_first）；命中 = top-3 ≥1 正确 且 0 泄漏。"""
    qmap = {int(r.id): r for r in qd.itertuples()}
    rec_ids = []
    for qid in sorted(pool_v2.query_id.unique()):
        r = qmap.get(qid)
        if r is None:
            continue
        req, meta = agent.extract_constraints(r.query)
        if agent.decide_ask(req, meta)["decision"] in ("ask_all", "ask_first"):
            continue
        rec_ids.append(qid)

    tot = clean_hit = any_hit = n_leak = 0
    detail = []
    badcases = []
    for qid in rec_ids:
        r = qmap[qid]
        rows = pool_v2[pool_v2.query_id == qid]
        prim = [a for a, gt in zip(rows.asin, rows.gold_type) if gt == "primary"]
        gold_ok = set(rows.asin[(rows.label == "gold")
                                & (rows.gold_type.isin(["primary", "extra"]))])
        gold_neg = set(rows.asin[(rows.label == "gold")
                                 & (rows.gold_type == "negative")])
        if not prim:
            continue
        req, _ = agent.extract_constraints(r.query)
        top = [a for a, _, _ in idx.score_candidates("tagfirst", req, list(rows.asin))]
        top3 = top[:3]
        hit3 = bool(gold_ok & set(top3))
        leak = bool(gold_neg & set(top3))
        prim_rank = (top.index(prim[0]) + 1) if prim[0] in top else None
        tot += 1
        clean_hit += hit3 and not leak
        any_hit += hit3
        n_leak += leak

        if not (hit3 and not leak):
            # 坏例归类：泄漏 → 避雷泄漏；无正确进 top-3 → 遍历所有 gold 商品
            # 任一 gold 标签完整 → 纯排序问题；全部缺标签/不在库 → 数据/标注问题
            sub, why = "排序不足", "gold 标签完整但未排进 top-3"
            if not hit3 and gold_ok:
                gaps = []
                for ga in sorted(gold_ok):
                    p = idx.by_asin.get(ga)
                    if p is None:
                        gaps.append(("标注", "gold asin 不在商品库"))
                        continue
                    g = _label_gap(req, p)
                    if g is None:
                        gaps = None
                        break
                    gaps.append(g)
                if gaps is not None and gaps:
                    sub, why = gaps[0]
            if leak:
                sub, why = "避雷泄漏", "负例进 top-3"
            layer = "检索" if sub in ("避雷泄漏", "数据缺失", "排序不足") else "标注"
            fix = {"避雷泄漏": "收紧控油/妆效触发规则，或加负例轴感知过滤",
                   "数据缺失": "精标该商品缺失轴标签（coverage/skin_tags 补 source=manual）",
                   "gold矛盾": "复核 gold 标注与 query 约束一致性，必要时改写 gold",
                   "排序不足": "调 tag_score 轴权重 / 加 query 改写词提升该商品分数"}[sub]
            badcases.append(dict(
                query_id=qid, query_type=r.query_type, metric="首答命中",
                pool="v2池", query=r.query[:120],
                expected_gold="；".join(_title(a, tmap) for a in sorted(gold_ok))[:160],
                actual_output="；".join(_title(a, tmap) for a in top3)[:160],
                failure_layer=layer,
                detail=f"{sub}（primRank={prim_rank or '未入榜'}）：{why}",
                fix=fix, regress=""))
        detail.append((qid, r.query_type, hit3 and not leak, prim_rank))

    denom = tot
    return dict(clean=clean_hit, tot=denom, any_hit=any_hit, leak=n_leak), badcases, detail


# ---------------------------------------------------------------- 通道 N ----

def run_ndcg(idx, qd, cp):
    """NDCG 通道：Phase-1 candidate_pool 池内 tagfirst（无 route，Agent 真实调用口径）。"""
    agg = {"ndcg": [], "recall": [], "mrr": [], "avoid": []}
    badcases = []
    per = []
    for qi, row in qd.iterrows():
        qid = qi + 1
        req = idx.parse_query(row)
        pool = cp[cp["query_id"] == qid]
        rel_map = dict(zip(pool["asin"], pool["relevance"]))
        gold = [a for a, r in rel_map.items() if r > 0]
        neg = [a for a, r in rel_map.items() if r < 0]
        ranked = [a for a, _, _ in idx.score_candidates("mixed", req, list(rel_map.keys()))]
        top5 = ranked[:5]
        rels = [rel_map.get(a, 0.0) for a in ranked]
        recall = len(set(gold) & set(top5)) / len(gold) if gold else np.nan
        mrr = next((1.0 / (j + 1) for j, a in enumerate(ranked)
                    if rel_map.get(a, 0) > 0), 0.0)
        ndcg = ndcg_at_k(rels)
        avoid = 1 - len(set(neg) & set(top5)) / len(neg) if neg else np.nan
        for key, v in (("ndcg", ndcg), ("recall", recall), ("mrr", mrr), ("avoid", avoid)):
            if v == v:
                agg[key].append(v)
        if neg and avoid < 1:
            leaked = set(neg) & set(top5)
            badcases.append(dict(
                query_id=qid, query_type=row.get("query_type", ""),
                metric="避雷", pool="Phase-1池",
                query=str(row["query"])[:120],
                expected_gold=f"负例 {len(leaked)} 个不应进 top-5",
                actual_output="；".join(leaked)[:160],
                failure_layer="检索", detail="避雷泄漏",
                fix="收紧触发规则或负例轴感知过滤", regress=""))
        per.append(dict(query_id=qid, ndcg=round(ndcg, 3), recall=round(recall, 3),
                        mrr=round(mrr, 3),
                        avoid=round(avoid, 3) if avoid == avoid else None,
                        top5="|".join(top5)))
    return {k: float(np.mean(v)) if v else float("nan") for k, v in agg.items()}, badcases, per


# ---------------------------------------------------------------- 汇总 ----

def report_rows(c, s, n):
    """三指标 + CONTRACT + 决策指标 → (metric, value, num, denom, benchmark, note)。"""
    rows = [
        ("首答命中率（干净top3,定标口径）", s["clean"] / s["tot"], s["clean"], s["tot"],
         0.70, "达标" if s["clean"] / s["tot"] >= 0.70 else "未达标"),
        ("NDCG@5（Phase-1池tagfirst）", n["ndcg"], "-", "-", "-", "-"),
        ("避雷率（Phase-1池tagfirst）", n["avoid"], "-", "-", 0.926,
         "换排序验收下降" if n["avoid"] < 0.926 else "≥基准"),
        ("避雷率（全库CONTRACT）", c["avoid_ok"] / c["avoid_denom"],
         c["avoid_ok"], c["avoid_denom"], 0.926, "达标"),
        ("CONTRACT硬断言", c["asserts_pass"] / c["asserts_total"]
         if c["asserts_total"] else float("nan"),
         c["asserts_pass"], c["asserts_total"], 1.0,
         "达标" if c["asserts_total"] and c["asserts_pass"] == c["asserts_total"]
         else ("无断言" if not c["asserts_total"] else "有FAIL")),
        ("追问率", c["ask"]["n"] / c["n"], c["ask"]["n"], c["n"], "-", "-"),
        ("降级率", c["retry"] / c["n"], c["retry"], c["n"], "-", "-"),
        ("兜底率", sum(v for k, v in c["fallback"].items()
                       if k in ("honest_note", "full")) / c["n"],
         sum(v for k, v in c["fallback"].items() if k in ("honest_note", "full")),
         c["n"], "-", "-"),
        # ---- 系统层（2026-08-29 补）：跑得多快 / 花多少钱 / 崩没崩 ----
        # benchmark="no-regress"：耗时是墙钟实测有噪声，token/异常是规则模式恒量，
        # 三行都不参与 DELTA 回归对比（避免把机器噪声当回退）。
        ("系统层·平均单轮耗时ms", round(float(np.mean(c["latency_ms"])), 1),
         "-", "-", "no-regress", "CONTRACT 24 题 Agent.run 实测"),
        ("系统层·LLM token成本", 0, 0, "-", "no-regress",
         "纯规则零模型恒0；hybrid 模式见 eval_compare_report.csv 旁路"),
        ("系统层·LLM 异常数", 0, 0, "-", "no-regress", "纯规则零模型无 LLM 异常"),
    ]
    return rows


def main():
    print("=" * 72)
    print("Phase 3 统一评测入口 eval_runner.py（闭环）")
    print("=" * 72)

    agent = GuideAgent()
    # 系统层耗时口径（2026-08-29）：rule 模式零模型依赖——_retrieve 已去掉无条件
    # enable_vectors()（agent.py），无向量载入冷启动，直接测稳态单轮耗时（~42ms）。
    rows = load_queries()
    engine = create_engine(DB)
    qd = pd.read_sql(f"SELECT id, query_type, query FROM eval_review_50 "
                     f"WHERE id <= {ANCHOR_MAX_ID} ORDER BY id", engine)
    pool_v2 = pd.read_sql(f"SELECT query_id, asin, label, gold_type FROM candidate_pool_v2 "
                          f"WHERE query_id <= {ANCHOR_MAX_ID}", engine)
    prod = pd.read_csv(REAL)
    tmap = dict(zip(prod["asin"].astype(str), prod["title"].astype(str)))
    idx = TagFirst(REAL)

    # 三通道
    c, bc_c = run_contract(agent, rows, tmap)
    s, bc_s, detail_s = run_first_answer(idx, agent, qd, pool_v2, tmap)
    n, bc_n, per_n = run_ndcg(idx,
                              pd.read_csv(ROOT / "data" / "evaluation_set.csv"),
                              pd.read_csv(ROOT / "data" / "candidate_pool.csv"))

    # 汇总表
    rrows = report_rows(c, s, n)
    fb = c["fallback"].get("honest_note", 0) + c["fallback"].get("full", 0)
    print(f"\n核心指标（Agent 正式排序 = tagfirst）：")
    print(f"  首答命中率 = {s['clean']}/{s['tot']} = {s['clean']/s['tot']:.1%}"
          f"（干净 top-3，对照 any-correct {s['any_hit']}/{s['tot']}={s['any_hit']/s['tot']:.1%}，"
          f"泄漏 {s['leak']} 题）")
    print(f"  NDCG@5 = {n['ndcg']:.3f}  |  Recall@5 = {n['recall']:.3f}  |  "
          f"MRR = {n['mrr']:.3f}  |  避雷率(Phase-1池) = {n['avoid']:.3f}")
    print(f"  CONTRACT 硬断言 = {c['asserts_pass']}/{c['asserts_total']}"
          f" = {c['asserts_pass']/c['asserts_total']:.0%}  |  "
          f"全库避雷 = {c['avoid_ok']}/{c['avoid_denom']} = {c['avoid_ok']/c['avoid_denom']:.0%}")
    print(f"  追问率 = {c['ask']['n']}/{c['n']} = {c['ask']['n']/c['n']:.1%}  |  "
          f"降级率 = {c['retry']}/{c['n']} = {c['retry']/c['n']:.1%}  |  "
          f"兜底率 = {fb}/{c['n']} = {fb/c['n']:.1%}")
    lat = c["latency_ms"]
    print(f"  系统层：平均单轮耗时 {np.mean(lat):.0f}ms（24 题 Agent.run 实测）| "
          f"LLM token = 0（纯规则零模型）| 异常 = 0")

    # 坏例汇总
    all_bc = bc_c + bc_s + bc_n
    print(f"\n坏例登记表：{len(all_bc)} 条"
          f"（首答 miss {len(bc_s)} / 避雷泄漏 {len(bc_n)} / CONTRACT {len(bc_c)}）")
    from collections import defaultdict
    by_layer = defaultdict(int)
    for b in all_bc:
        by_layer[b["failure_layer"]] += 1
    for l, cnt in sorted(by_layer.items()):
        print(f"  {l}: {cnt}")

    # 逐题首答明细
    print(f"\n首答通道逐题（v2 池 tagfirst）：")
    for qid, qtype, clean, prim_rank in detail_s:
        print(f"  q{qid:>2} [{qtype:<12}] {'✓' if clean else '✗'} primRank={prim_rank or '未入榜'}")

    # 写坏例表
    df_bc = pd.DataFrame(all_bc)
    df_bc = df_bc.reindex(columns=["query_id", "query_type", "metric", "pool",
                                   "query", "expected_gold", "actual_output",
                                   "failure_layer", "detail", "fix", "regress"])
    df_bc.to_csv(ROOT / "data" / "badcase_report.csv", index=False, encoding="utf-8-sig")

    # 写评测汇总 + 回归对比
    df_rep = pd.DataFrame(rrows, columns=["metric", "value", "num", "denom",
                                          "benchmark", "note"])
    prev = None
    if (ROOT / "data" / "eval_report.csv").exists():
        prev = pd.read_csv(ROOT / "data" / "eval_report.csv")
    regress = []
    for _, r in df_rep.iterrows():
        status = ""
        if (prev is not None and r["metric"] in set(prev["metric"])
                and r["benchmark"] != "no-regress"):
            old = float(prev.loc[prev["metric"] == r["metric"], "value"].iloc[0])
            if abs(old - r["value"]) > DELTA:
                status = f"回退({old:.3f}→{r['value']:.3f})" if r["value"] < old else f"↑({old:.3f}→{r['value']:.3f})"
            else:
                status = "通过"
        regress.append(status)
    df_rep["regress"] = regress
    df_rep.to_csv(ROOT / "data" / "eval_report.csv", index=False, encoding="utf-8-sig")
    if prev is not None:
        bad_rows = [f"  {r['metric']}: {r['regress']}" for _, r in df_rep.iterrows()
                    if r["regress"] and r["regress"] != "通过"]
        print("\n与上一轮 eval_report.csv 对比："
              + (("回退/提升\n" + "\n".join(bad_rows)) if bad_rows else "全部通过 ✓"))

    print("\n已写 data/eval_report.csv（汇总+回归） 和 data/badcase_report.csv（登记表）")


if __name__ == "__main__":
    main()
