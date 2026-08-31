# -*- coding: utf-8 -*-
"""candidate_pool_v2 — 为 24 个 v2 题按 Phase 1 同规则建候选池（不碰 Phase-1 的 candidate_pool）

忠实复刻 build_candidate_pool.py 的方法学（可解释采样）：
  1. gold 全量入池（gold_primary 1.0 / gold_extras 0.8 / gold_negative -1.0）
  2. 负候选两层采样：
     - 难例 hard：命中 Query 至少一个显式标签轴（肤质/妆效/遮盖/质地/色号），上限 15/50（30%）
     - 简单例 random：不匹配随机商品，补足到 50
  3. 幂等：CSV + MySQL 均可重跑（if_exists=replace），seed=42 固定可复现

与 Phase-1 的唯一差异（方法学等价）：
  Phase-1 的轴来自 evaluation_set.csv 的结构化标签列（skin_label 等）；v2 的 24 题在
  eval_review_50 只有 query 原文，所以轴从 Agent 的 extract_constraints(query) 推导——
  这是项目自己的约束抽取器，口径与 Agent 检索完全一致。

输出：data/candidate_pool_v2.csv + MySQL beauty_agent.candidate_pool_v2
"""
import io
import random
import re
import sys
from pathlib import Path

import pandas as pd
import sqlalchemy.types
from sqlalchemy import create_engine, text

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import GuideAgent
from db_config import db_params

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")
_db = db_params()
USER, PWD, HOST, PORT = _db["user"], _db["password"], _db["host"], _db["port"]
DB = "beauty_agent"
N_POOL = 50          # 每 query 负候选数（对标 C4，与 Phase-1 一致）
N_HARD_MAX = 15      # 难例上限（30%）
SEED = 42            # 与 Phase-1 同一 seed


def split_tags(v):
    if v is None or pd.isna(v) or not str(v).strip():
        return []
    return [x.strip() for x in str(v).split(";") if x.strip()]


def gold_asins(v):
    """v2 的 gold 列是完整标注块（商品名（价格·评分/评论数）🔗ASIN [五轴标注]），
    用 eval_agent.asins 同款正则提取 asin（`\b[A-Z0-9]{10}\b`），不按 `;` 切。"""
    if v is None or pd.isna(v) or not str(v).strip():
        return []
    return re.findall(r"\b[A-Z0-9]{10}\b", str(v))


def v2_axes(req, p):
    """商品 p 命中 query（extract_constraints 的 req）的哪些显式标签轴 → 难例判定。

    与 build_candidate_pool.match_axes 等价，只是轴从 req 推导而非结构化列：
    肤质 / 妆效 / 遮盖 / 质地 / 色号。"""
    axes = []
    p_skins = set(split_tags(p.get("skin_tags")))
    q_skins = (req["hard"] | req["soft"]) - {"全肤质"}
    if q_skins and (p_skins & q_skins):
        axes.append("肤质")
    if req["finish"] and p.get("finish_tag") == req["finish"]:
        axes.append("妆效")
    if req["coverage"] and p.get("coverage_tag") == req["coverage"]:
        axes.append("遮盖")
    if req["form"] and p.get("form_tag") == req["form"]:
        axes.append("质地")
    if req["shade_dir"]:
        shades = set(split_tags(p.get("shade_tag")))
        if (req["shade_dir"] == "fair" and "白皙" in shades) or \
           (req["shade_dir"] == "dark" and "深色" in shades):
            axes.append("色号")
    return axes


def main():
    # ---- 读 v2 24 题 + 商品库 ----
    engine = create_engine(f"mysql+pymysql://{USER}:{PWD}@{HOST}:{PORT}/{DB}?charset=utf8mb4")
    with engine.connect() as c:
        qd = pd.read_sql("SELECT id, query, gold_primary, gold_extras, gold_negative FROM eval_review_50 ORDER BY id", c)
    df = pd.read_csv(ROOT / "data" / "products_clean.csv").fillna("")
    pool = df.to_dict("records")
    by_asin = {p["parent_asin"]: p for p in pool}
    agent = GuideAgent()
    rng = random.Random(SEED)

    rows, stats = [], []
    for r in qd.itertuples():
        qid = int(r.id)
        req, meta = agent.extract_constraints(r.query)

        gold = {"primary": [], "extra": [], "negative": []}
        for col, gtype, rel in [("gold_primary", "primary", 1.0),
                                ("gold_extras", "extra", 0.8),
                                ("gold_negative", "negative", -1.0)]:
            for a in gold_asins(getattr(r, col)):
                gold[gtype].append((a, rel))
        taken = set(a for lst in gold.values() for a, _ in lst)
        # 只保留商品库内存在的 gold（不存在无法评分，如实报告不静默丢弃）
        missing = [a for a in taken if a not in by_asin]
        for gtype in gold:
            gold[gtype] = [(a, rel) for a, rel in gold[gtype] if a in by_asin]
        if missing:
            print(f"  !! q{qid} gold 不在商品库（已跳过，不参与评分）: {missing}")

        # 金标准行（同一 (query_id, asin) 去重：q21 的 Estee 粉饼同时出现在 extras 和 negative，
        # 保留 primary→extra→negative 顺序的第一个，池内评分不看 gold_type 不冲突）
        seen_gold = set()
        for gtype, lst in gold.items():
            for a, rel in lst:
                if (qid, a) in seen_gold:
                    continue
                seen_gold.add((qid, a))
                rows.append({"query_id": qid, "asin": a, "label": "gold", "gold_type": gtype,
                             "relevance": rel, "pool_type": "gold", "matched_axes": ""})

        # 负候选：难例 / 随机 分层（与 Phase-1 同 seed 同规则）
        rest = [p for p in pool if p["parent_asin"] not in taken]
        hard = [(p, v2_axes(req, p)) for p in rest if v2_axes(req, p)]
        rng.shuffle(hard)
        picked_hard = hard[:N_HARD_MAX]
        hard_asins = set(p["parent_asin"] for p, _ in picked_hard)
        remain = [p for p in rest if p["parent_asin"] not in hard_asins]
        rng.shuffle(remain)
        picked_rand = remain[: max(0, N_POOL - len(picked_hard))]

        for p, axes in picked_hard:
            rows.append({"query_id": qid, "asin": p["parent_asin"], "label": "candidate",
                         "gold_type": "", "relevance": 0.0, "pool_type": "hard",
                         "matched_axes": ";".join(axes)})
        for p in picked_rand:
            rows.append({"query_id": qid, "asin": p["parent_asin"], "label": "candidate",
                         "gold_type": "", "relevance": 0.0, "pool_type": "random", "matched_axes": ""})

        stats.append({"query_id": qid, "qtype": r.query, "gold_n": len(taken),
                      "hard_n": len(picked_hard), "random_n": len(picked_rand),
                      "pool_size": len(taken) + N_POOL})

    cp = pd.DataFrame(rows, columns=["query_id", "asin", "label", "gold_type",
                                     "relevance", "pool_type", "matched_axes"])
    cp.to_csv(ROOT / "data" / "candidate_pool_v2.csv", index=False, encoding="utf-8-sig")

    cp.to_sql("candidate_pool_v2", engine, if_exists="replace", index=False, dtype={
        "asin": sqlalchemy.types.VARCHAR(20),
        "gold_type": sqlalchemy.types.VARCHAR(20),
        "pool_type": sqlalchemy.types.VARCHAR(20),
        "matched_axes": sqlalchemy.types.VARCHAR(100),
    })
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE candidate_pool_v2 ADD PRIMARY KEY (query_id, asin)"))

    st = pd.DataFrame(stats)
    print(f"candidate_pool_v2: {len(qd)} 个 v2 query × 各 {N_POOL} 负候选 + 金标准")
    print(f"总行数: {len(cp)}（gold {int((cp['label']=='gold').sum())} / candidate {int((cp['label']=='candidate').sum())}）")
    print("\n每 query 明细：")
    for _, s in st.iterrows():
        print(f"  q{int(s['query_id'])}: 池 {int(s['pool_size'])} = gold {int(s['gold_n'])}"
              f" + 难例 {int(s['hard_n'])} + 随机 {int(s['random_n'])}")
    n_hard, n_rand = st["hard_n"].sum(), st["random_n"].sum()
    print(f"\n难例占比: {n_hard / (n_hard + n_rand) * 100:.1f}%（上限 30%）")
    print("已落库 candidate_pool_v2（主键 query_id+asin）✅")


if __name__ == "__main__":
    main()
