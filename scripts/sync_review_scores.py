# -*- coding: utf-8 -*-
"""
人工复核一致性率 · 录库 + 计算（Phase 0 / Phase 3 闭环衔接）
=================================================
用户在 docs/eval_review_checklist.md 打完分后，把分数填进
`data/review_scores.csv`（模板：query_id / primary_ok / extras_ok / negative_ok，
1-6 分，不打留空），本脚本：
  1. 校验分数合法性
  2. 写回 MySQL eval_review_50（primary_ok / extras_ok / negative_ok）
  3. 算一致性率：4/5 分算一致，排除 6 无法判断 → 分列 + 整体

一致性率 = 打 4/5 分的判断数 ÷ 排除 6 后的总判断数（金标准自动生成有数字背书）
"""
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from db_config import db_params

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")
SCORES = ROOT / "data" / "review_scores.csv"
_db = db_params()
USER, PWD, HOST, PORT = _db["user"], _db["password"], _db["host"], _db["port"]
DB = "beauty_agent"
COLUMNS = ["primary_ok", "extras_ok", "negative_ok"]


def make_template():
    """首次运行：从 eval_review_50 生成空白模板（不覆盖已有分数）。"""
    if SCORES.exists():
        return
    engine = create_engine(f"mysql+pymysql://{USER}:{PWD}@{HOST}:{PORT}/{DB}?charset=utf8mb4")
    with engine.connect() as c:
        ids = [r[0] for r in c.execute(text("SELECT id FROM eval_review_50 ORDER BY id"))]
    pd.DataFrame({k: [None] * len(ids) for k in ["query_id", *COLUMNS]},
                 index=range(len(ids))).assign(query_id=ids).to_csv(
        SCORES, index=False, encoding="utf-8-sig")
    print(f"已生成打分模板：{SCORES}（用 Excel 填 1-6 分，不打留空；6=无法判断不计入）")


def validate(s):
    """空 → None；1-6 → int；否则报错。"""
    if pd.isna(s) or s == "":
        return None
    try:
        v = int(float(s))
    except (TypeError, ValueError):
        raise ValueError(f"非法分数: {s!r}（需 1-6 或留空）")
    if not 1 <= v <= 6:
        raise ValueError(f"分数越界: {v}（需 1-6 或留空）")
    return v


def main():
    make_template()
    if not SCORES.exists():
        return
    df = pd.read_csv(SCORES)
    for col in [*COLUMNS]:
        df[col] = df[col].apply(validate)

    # ---- 写回 MySQL（幂等） ----
    engine = create_engine(f"mysql+pymysql://{USER}:{PWD}@{HOST}:{PORT}/{DB}?charset=utf8mb4")
    with engine.connect() as c:
        for _, r in df.iterrows():
            c.execute(text(
                "UPDATE eval_review_50 SET primary_ok=:p, extras_ok=:e, negative_ok=:n WHERE id=:qid"),
                {"p": r["primary_ok"], "e": r["extras_ok"], "n": r["negative_ok"], "qid": int(r["query_id"])})
        c.commit()
    print(f"已写回 eval_review_50（{len(df)} 行）")

    # ---- 一致性率 ----
    print("\n" + "=" * 52)
    print("人工复核一致性率（4/5 分算一致，排除 6）")
    print("=" * 52)
    print(f"{'维度':<10}{'打分':>5}{'一致':>5}{'一致率':>9}{'排除6':>7}{'均分':>7}")
    totals = {"scored": 0, "agree": 0, "excluded": 0}
    for col in COLUMNS:
        vals = df[col].dropna()
        scored = (vals != 6).sum()
        agree = vals.isin([4, 5]).sum()
        excluded = (vals == 6).sum()
        mean = vals[vals != 6].mean()
        rate = agree / scored if scored else float("nan")
        totals["scored"] += scored
        totals["agree"] += agree
        totals["excluded"] += excluded
        print(f"{col:<10}{int(scored):>5}{int(agree):>5}{rate:>9.1%}{int(excluded):>7}"
              f"{mean:>7.2f}" if mean == mean else f"{col:<10}{int(scored):>5}{int(agree):>5}{rate:>9.1%}{int(excluded):>7}{'  -  '}")
    t_rate = totals["agree"] / totals["scored"] if totals["scored"] else float("nan")
    print("-" * 52)
    print(f"{'整体':<10}{totals['scored']:>5}{totals['agree']:>5}{t_rate:>9.1%}{totals['excluded']:>7}")
    print("\n口径：一致性率 = 打 4/5 分的判断数 ÷ 排除 6 后的总判断数")


if __name__ == "__main__":
    main()
