# -*- coding: utf-8 -*-
"""
Phase 2 导购 Agent 评测 runner（24 题 CONTRACT 跑批）
=======================================================
对 eval_review_50 全 24 题跑 GuideAgent，输出：
  - 硬断言通过率（24 题 × contract_cases 断言，目标 100%）
  - 3 决策指标：追问率 / 降级率 / 兜底率
  - 软指标：首答命中率（gold asin 进推荐）、避雷率（gold_negative 不进推荐）
CONTRACT 断言测决策正确性，不依赖排序质量；软指标对齐 Phase 1 口径（避雷 ≥0.926）。
"""
import io
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

sys.path.insert(0, str(Path(__file__).resolve().parent))

from contract_cases import CASES
from agent import GuideAgent
from db_config import db_params

DB = db_params()

ASK_KINDS = ("ask_all", "ask_first", "ask_shade_soft")


def _to_float(x):
    try:
        f = float(str(x).replace("$", "").strip())
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def asins(s):
    """从 gold 字段提取 🔗 后的 asin（gold_primary/extras/negative 都嵌 🔗asin）。"""
    if not s:
        return []
    return re.findall(r"\b[A-Z0-9]{10}\b", str(s))


def load_queries():
    import pymysql
    c = pymysql.connect(**DB)
    cur = c.cursor()
    cur.execute("SELECT id, query, query_type, gold_primary, gold_extras, gold_negative "
                "FROM eval_review_50 ORDER BY id")
    return cur.fetchall()


def main():
    agent = GuideAgent()
    rows = load_queries()

    # 逐题跑 + 应用 CONTRACT 断言
    assert_stats = Counter()          # 断言名 -> (通过, 总数) 累计
    per_case = []
    ask_kind = Counter()
    retry_n = 0
    fallback_kind = Counter()
    hit_n = 0; gold_n = 0          # 首答命中：只对 gold_primary 计数（对齐 Plan 定义）
    hit_e = 0; gold_e = 0          # extras 命中：补充参考
    avoid_ok = 0; avoid_denom = 0

    for rid, query, qtype, gp, ge, gn in rows:
        rec = agent.run(query, qid=rid, query_type=qtype)
        case = next((c for c in CASES if c["id"] == rid), None)
        if case is None:
            print(f"警告: id={rid} 无 CONTRACT 用例")
            continue

        passed = fails = 0
        case_failures = []
        for name, fn in case["asserts"]:
            ok, detail = fn(rec, agent)
            assert_stats[f"{rid}:{name}"] += 1
            if ok:
                passed += 1
            else:
                fails += 1
                case_failures.append(f"    [FAIL] {name} — {detail}")
        per_case.append((rid, passed, fails, case_failures))

        # 3 决策指标
        ask_kind[rec["ask"]["decision"]] += 1
        if rec["retry"]["triggered"]:
            retry_n += 1
        lv = rec["fallback"].get("level") if rec["fallback"]["triggered"] else None
        fallback_kind[lv] += 1

        # 软指标
        recs = [r["asin"] for r in rec["recommendations"]]
        gp_asins = asins(gp); ge_asins = asins(ge)
        hit_n += len(set(gp_asins) & set(recs))
        gold_n += len(gp_asins)
        hit_e += len(set(ge_asins) & set(recs))
        gold_e += len(ge_asins)
        negs = asins(gn)
        if negs:
            avoid_denom += 1
            if not (set(negs) & set(recs)):
                avoid_ok += 1

    # ---------------- 汇总 ----------------
    n_cases = len(per_case)
    total_a = sum(f for _, _, f, _ in per_case)
    total_p = sum(p for _, p, _, _ in per_case)
    print("=" * 68)
    print("Phase 2 导购 Agent 评测（24 题 CONTRACT）")
    print("=" * 68)

    # 硬断言通过率
    print(f"\n硬断言通过率: {total_p}/{total_p + total_a} 条通过 "
          f"（{total_p / (total_p + total_a):.1%}）" if (total_p + total_a) else "无断言")
    for rid, p, f, cf in per_case:
        mark = "OK " if f == 0 else "✗"
        print(f"  [{mark}] id={rid:>2} {p}/{p + f}")
        for line in cf:
            print(line)

    # 3 决策指标
    n = len(rows)
    ask_total = sum(ask_kind[k] for k in ASK_KINDS)
    fb_total = sum(v for k, v in fallback_kind.items() if k in ("honest_note", "full"))
    print("\n3 决策指标:")
    print(f"  追问率 = {ask_total}/{n} = {ask_total / n:.1%}"
          f"   （ask_all={ask_kind['ask_all']} ask_first={ask_kind['ask_first']} "
          f"ask_shade_soft={ask_kind['ask_shade_soft']}）")
    print(f"  降级率 = {retry_n}/{n} = {retry_n / n:.1%}（改写重试触发数）")
    print(f"  兜底率 = {fb_total}/{n} = {fb_total / n:.1%}"
          f"   （honest_note={fallback_kind['honest_note']} full={fallback_kind['full']}）")

    # 软指标
    hit_rate = hit_n / gold_n if gold_n else float("nan")
    ext_rate = hit_e / gold_e if gold_e else float("nan")
    avoid_rate = avoid_ok / avoid_denom if avoid_denom else float("nan")
    print("\n软指标（对齐 Phase 1 口径，不判定）:")
    print(f"  首答命中 = {hit_n}/{gold_n} gold_primary asin 进推荐 = {hit_rate:.1%}"
          f"（全库 top-3，严于候选池 Recall@5 0.606）")
    print(f"  备选命中 = {hit_e}/{gold_e} extras asin 进推荐 = {ext_rate:.1%}（补充参考）")
    print(f"  避雷率   = {avoid_ok}/{avoid_denom} 题负面集不进推荐 = {avoid_rate:.1%}"
          f"（Phase 1 基准 0.926）")


if __name__ == "__main__":
    main()
