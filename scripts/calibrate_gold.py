# -*- coding: utf-8 -*-
"""calibrate_gold.py — 人工抽检反馈 → gold 真值校准（规则参数校准第 1 步）
========================================================================================
背景：用户对 10 条 gold 做人工抽检，6 条 MODIFY，质疑全部指向「标签不能代表真实口碑，
要看评论区」。本脚本把 6 条 MODIFY 按「评论负面反馈共识」校准：

  共识口径：某缺陷轴提及数 ÷ 该商品负面评论数 ≥ 70% → 标硬规则（避雷）；
            色号偏深黄/浅灰等色号轴不算避雷轴（色号适配非质量问题）。

校准动作（user 确认 q25 换 Dermacol；其余按数据校准，目标 asin 已逐一核对库内真值）：
  q8  : P Rimmel(卡粉75%) → Mirenesse；Rimmel 转 negative；extras 补 Airbrush
  q15 : 不改 gold（补价格表供重看）
  q17 : 不改 gold（Myconos 评论区核验通过）
  q20 : negative 移除 MaryKay（无负面共识）
  q25 : P Sweat(粉状非粉底液) → Dermacol Waterproof；extras 加 Hera SPF34；Sweat 移除
  q31 : negative 移除 BOOTS（色号浅灰非避雷轴）

策略：**条目级复用**——已存在的 gold 条目原文保留（含语义批注），只做挪/删/增；
      新条目才按 products 库内真相生成。三处同步：eval_review_50 + 对比表1（gold 三列）
      + candidate_pool_v2（gold_type：primary/extra/negative，非答案=空串）。
默认 dry-run 只打印改前改后；`--apply` 才写库（先备份 *_bak_v17）。

用法：python calibrate_gold.py [--apply]
"""
import io
import re
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

from db_config import db_url

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")
DB = db_url()
APPLY = "--apply" in sys.argv
_ASIN = re.compile(r"B[0-9A-Z]{9}")

# 校准动作：qid -> {primary:[asin], extras:[asin], negative:[asin]}（目标全量，已对库内真值）
# 15/17 keep 不动。
ACTIONS = {
    8:  dict(primary=["B00LTFW9XQ"],   extras=["B00AFIOPEE"],
             negative=["B019BCAMT6", "B07MX218RF", "B01L1Y3OOC", "B00J1OIDZ0"]),
    15: dict(keep=True, note="gold 不改：补价格表供重看（Revlon 9.99 / NYX 9.99 / Maybelline 10.31）"),
    17: dict(keep=True, note="gold 不改：Myconos 评论区核验通过（卡粉/脱妆/闷痘证据充分，遮瑕未标不阻碍避雷）"),
    20: dict(primary=["B01B8BR0KC"],   extras=["B079M2PLKM", "B014GJH4PE"],
             negative=["B0BB9HNWT7", "B00J1OIDZ0", "B07MGWMT9X"]),  # 去掉 MaryKay
    25: dict(primary=["B077W2RCN7"],   extras=["B071HYBZM4", "B0728BGWYN"],
             negative=["B079M2PLKM"]),                              # 去掉 Sweat，Dermacol 升 P，加 Hera
    31: dict(primary=["B01B8BR0KC"],   extras=["B00FTY5DXG"],
             negative=[]),                                          # 去掉 BOOTS
}

DEFECT_FILE = ROOT / "data" / "product_defect_evidence.csv"


def _load(eng):
    qd = pd.read_sql("SELECT id, gold_primary, gold_extras, gold_negative "
                     "FROM eval_review_50 WHERE id IN (8,15,17,20,25,31) ORDER BY id", eng)
    prod = pd.read_sql("SELECT parent_asin, title, price, average_rating, rating_number, "
                       "form_tag, skin_tags, finish_tag, coverage_tag, shade_tag FROM products", eng)
    pool = pd.read_sql("SELECT query_id, asin, gold_type FROM candidate_pool_v2 "
                       "WHERE query_id IN (8,20,25,31)", eng)
    de = pd.read_csv(DEFECT_FILE, encoding="utf-8-sig") if DEFECT_FILE.exists() else pd.DataFrame()
    return qd, prod, pool, de


def _five_axes(p):
    def g(col):
        v = p.get(col)
        return str(v) if pd.notna(v) and str(v).strip() else "未标"
    return f"质地:{g('form_tag')} | 肤质:{g('skin_tags')} | 妆效:{g('finish_tag')} | 遮瑕:{g('coverage_tag')} | 色号:{g('shade_tag')}"


def _gold_text(asin, prod, de):
    """新条目按库内真相生成（缺证据批注的商品不造假证据）。"""
    p = prod[prod.parent_asin == asin]
    if not len(p):
        return f"{asin}（库内无）"
    p = p.iloc[0]
    price = f"${p.price:.2f}" if pd.notna(p.price) else "价格待核实"
    rating = f"{p.average_rating}分/{int(p.rating_number)}条" if pd.notna(p.rating_number) else "评分缺失"
    title = str(p.title)
    if len(title) > 64:
        title = title[:63] + "…"
    d = de[de.parent_asin == asin]
    tag = f"（{d.iloc[0]['defect_axes']}证据）" if len(d) and str(d.iloc[0].get("defect_axes", "")).strip() else ""
    return f"{title}（{price}·{rating}）{tag}🔗{asin} [{_five_axes(p)}]"


def _parse_entries(text):
    """把 ' || ' 分隔的 gold 文本解析成 {asin: 原始条目文本}（保留顺序）。"""
    entries = []
    if text and str(text).strip() and str(text).strip() != "—":
        for part in str(text).split("||"):
            part = part.strip()
            m = _ASIN.search(part)
            if m:
                entries.append((m.group(0), part))
    return entries


def _compose(asin_list, curated, prod, de):
    """按 asin 顺序组列：已在原文的条目原文保留（含语义批注），新 asin 用库内真相生成。"""
    out = []
    for asin in asin_list:
        out.append(curated.get(asin) or _gold_text(asin, prod, de))
    return " || ".join(out) if out else "—"


def build_new(qd, prod, de):
    """返回 {qid: {keep|cols, old_map, new_map, all_asins}}。"""
    out = {}
    for qid, act in ACTIONS.items():
        if act.get("keep"):
            out[qid] = {"keep": True, "note": act["note"]}
            continue
        r = qd[qd.id == qid].iloc[0]
        old = {c: _parse_entries(r[c]) for c in ["gold_primary", "gold_extras", "gold_negative"]}
        # 跨列原文映射：已存在的条目原文保留（含语义批注如「极端控油=冬天拔干」）
        curated = {}
        for c in ["gold_primary", "gold_extras", "gold_negative"]:
            for a, t in old[c]:
                curated.setdefault(a, t)
        cols = {c: _compose(act[c.replace("gold_", "")], curated, prod, de)
                for c in ["gold_primary", "gold_extras", "gold_negative"]}
        all_asins = set()
        for c in ["gold_primary", "gold_extras", "gold_negative"]:
            all_asins |= {a for a, _ in old[c]} | set(act[c.replace("gold_", "")])
        out[qid] = {"cols": cols, "old": old, "all_asins": all_asins}
    return out


def _new_type(qid, asin, cols):
    for t, col in [("primary", "gold_primary"), ("extra", "gold_extras"), ("negative", "gold_negative")]:
        if f"🔗{asin}" in cols[col]:
            return t
    return ""  # candidate = 空串（库内 gold_type 取值：primary/extra/negative/空）


def main():
    print("=" * 76)
    print(f"gold 真值校准 calibrate_gold.py [{'APPLY 写库' if APPLY else 'DRY-RUN 预览'}]")
    print("=" * 76)
    eng = create_engine(DB)
    qd, prod, pool, de = _load(eng)
    new = build_new(qd, prod, de)

    for qid, item in new.items():
        print(f"\n===== q{qid} =====")
        if item.get("keep"):
            print(f"  保持原 gold：{item['note']}")
            continue
        for col in ["gold_primary", "gold_extras", "gold_negative"]:
            old_txt = " || ".join(t for _, t in item["old"][col]) or "—"
            print(f"  [{col}]")
            print(f"    OLD: {old_txt[:150]}")
            print(f"    NEW: {item['cols'][col][:150]}")

    if not APPLY:
        print("\n[DRY-RUN] 加 --apply 才写库（先备份 *_bak_v17）。")
        return

    # ---------- 写库 ----------
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS eval_review_50_bak_v17 AS SELECT * FROM eval_review_50"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS 对比表1_bak_v17 AS SELECT * FROM 对比表1"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS candidate_pool_v2_bak_v17 AS SELECT * FROM candidate_pool_v2"))
        for qid, item in new.items():
            if item.get("keep"):
                continue
            for col, val in item["cols"].items():
                conn.execute(text(f"UPDATE eval_review_50 SET {col}=:v WHERE id=:i"), dict(v=val, i=qid))
                conn.execute(text(f"UPDATE 对比表1 SET {col}=:v WHERE id=:i"), dict(v=val, i=qid))
            # 候选池 gold_type 同步：新旧 asin 全量走，移出 gold 的标回空串（candidate）
            for a in sorted(item["all_asins"]):
                t = _new_type(qid, a, item["cols"])
                cur = pool[(pool.query_id == qid) & (pool.asin == a)]["gold_type"].tolist()
                old_t = cur[0] if cur else None
                if old_t != t:
                    conn.execute(text("UPDATE candidate_pool_v2 SET gold_type=:t "
                                      "WHERE query_id=:i AND asin=:a"),
                                 dict(t=t, i=qid, a=a))
                    print(f"  pool q{qid} {a}: {old_t or '(空)'!r} -> {t or '(空)'!r}")
    print("\n[APPLIED] 已备份 *_bak_v17 + 更新 eval_review_50 / 对比表1 / candidate_pool_v2。")
    print("下一步：python eval_runner.py 重跑看锚点数字变化。")


if __name__ == "__main__":
    main()
