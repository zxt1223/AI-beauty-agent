# -*- coding: utf-8 -*-
"""apply_label_patch_v3.py — ③ v3 标签缺口回填（真实用户评语逐条人工核验后落库）

安全模式同 apply_label_patch.py：先备份 products_clean.csv，幂等可重复跑，
之后必须 python load_mysql.py 双写 MySQL products 表。

覆盖既有机制的三个新维度：
  - PATCH_FIN  / finish_tag 仅补空（已标不覆盖自动提取），补入时 finish_type_source=manual
  - PATCH_FORM / form_tag   仅当现值为预期 from 时替换为 to（气垫误标乳霜纠正，幂等）
  - PATCH_SKIN_REPLACE / 整字段替换（Palladio「Dual Wet & Dry」被误读成肤质 Dry 的假阳性纠正）

证据来源：data/_label_gap_checklist.csv 逐行调原始评语人工核验（2026-09-02）。
全部改动只在 products_clean.csv，gold 不动。
"""
import io
import sys
import shutil
from datetime import datetime
from pathlib import Path

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

import pandas as pd

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")
CSV = ROOT / "data" / "products_clean.csv"
BACKUP = ROOT / "data" / "backup" / f"products_clean_pre_patch3_{datetime.now():%Y%m%d_%H%M%S}.csv"

# ---- 肤质：追加（保留原标签；幂等：已含则跳过）----
PATCH_SKIN = {
    "B00QHB2J2S": ["敏感肌"],          # MaxFactor Panstik: "does not bother my sensitive skin" (q60)
    "B06Y4WP8SR": ["敏感肌"],          # Gressa Serum: "I have quite sensitive skin...light and soothing" (q61)
    "B083SCPG87": ["敏感肌"],          # VT CICA: "hyper sensitive skin and this does not break me out" (q62)
    "B01NCOK3W3": ["敏感肌"],          # Clinique Redness: "Absolute necessity if you have Rosacea" (q63)
    "B01AXR8Z9C": ["敏感肌", "痘痘肌", "干皮"],  # J.One: "sensitive dry acne prone skin...will not break out" (q66)
    "B06Y4YFFYQ": ["痘痘肌", "干皮", "油皮"],    # Gressa#2: "very dry acne prone skin as well as friend's oily" (q100)
    "B003V1RC6A": ["敏感肌", "痘痘肌"],          # Bare Escentuals: "hormonal acne...only thing that did not make it worse" (q110)
    "B019BCAMT6": ["痘痘肌"],          # Myconos: acne-pan bb 用户选此款 (q119，中置信)
    "B079XW1XJF": ["油皮"],            # VELY: "This is perfect for oily skin" (q101)
    "B01N7WZ2WB": ["干皮"],            # Holika: "most foundations dry me out - this one didn't, at all" (q53)
}
# ---- 肤质：整字段替换（一次性假阳性纠正）----
# Palladio「Herbal Dual Wet & Dry Foundation」的 "Wet & Dry" 被误读成肤质 Dry（skin_type_source=title），
# q106 油性 T 区用户评语「It stays on and I have oily t-zone」→ 应为油皮，非干皮
PATCH_SKIN_REPLACE = {
    "B00P7COT6M": ["油皮"],
}
# ---- 遮瑕：仅补空，补入 source=manual（已标不覆盖）----
PATCH_COV = {
    "B00QHB2J2S": "中度遮瑕",  # MaxFactor: "med coverage" (q60)
    "B089NJG212": "轻遮瑕",    # AMOREPACIFIC: "It does provide light coverage" (q51)
    "B07TB3HJQ4": "中度遮瑕",  # stila: "light to medium coverage" (q76)
    "B019BCAMT6": "轻遮瑕",    # Myconos: "It is light coverage" (q124)
    "B000COKYTG": "高遮瑕",    # Laura Mercier: "it gives full coverage" (q54，中置信)
}
# ---- 妆效：仅补空，补入 finish_type_source=manual（已标不覆盖）----
PATCH_FIN = {
    "B00WZOH1KQ": "自然",  # NYX HD: "Light natural" (q68)
    "B00FVAQYJK": "哑光",  # KVD Lock-It: "keeps you matted" (q95，评论用词 matted 未命中词表但同义)
    "B07D7R6J6N": "水光",  # Peripera Airy Ink Cushion: "LEAVES YOUR SKIN MORE DEWY AND NOT MATTE" (q129)
    "B000COKYTG": "水光",  # Laura Mercier: "gives...a dewy finish" (q54，中置信)
}
# ---- 质地：仅当现值为 from 时替换为 to（气垫误标乳霜纠正，幂等）----
PATCH_FORM = {
    "B01C6YE0XK": ("乳霜", "气垫"),  # Nakeup Waterking Cover Cushion: 标题+评语均称 cushion (q72)
    "B019BCAMT6": ("乳霜", "气垫"),  # Myconos Air Cushion Compact: 标题 "Cushion Compact"+评语 "my first BB Cushion" (q119)
}


def main():
    df = pd.read_csv(CSV).fillna("")
    for col in ("coverage_tag_source", "finish_type_source", "item_form_source"):
        if col not in df.columns:
            df[col] = ""
    changed_skin, changed_skin_r, changed_cov, changed_fin, changed_form = [], [], [], [], []
    skipped = []

    def row(a):
        m = df["parent_asin"] == a
        if not m.any():
            print(f"  !! 补丁未命中 {a}")
            return None
        return m

    for a, add in PATCH_SKIN.items():
        m = row(a)
        if m is None: continue
        cur = [s for s in str(df.loc[m, "skin_tags"].iloc[0]).split(";") if s]
        new = cur + [s for s in add if s not in cur]
        if new != cur:
            df.loc[m, "skin_tags"] = ";".join(new)
            changed_skin.append((a, ";".join(cur) or "(空)", ";".join(new)))
        else:
            skipped.append(("skin", a))

    for a, final in PATCH_SKIN_REPLACE.items():
        m = row(a)
        if m is None: continue
        cur = [s for s in str(df.loc[m, "skin_tags"].iloc[0]).split(";") if s]
        if cur == final:
            skipped.append(("skin_replace(已一致)", a)); continue
        df.loc[m, "skin_tags"] = ";".join(final)
        changed_skin_r.append((a, ";".join(cur) or "(空)", ";".join(final)))

    for a, tag in PATCH_COV.items():
        m = row(a)
        if m is None: continue
        cur = str(df.loc[m, "coverage_tag"].iloc[0] or "")
        if cur:
            skipped.append(("cov(已标)", a)); continue
        df.loc[m, "coverage_tag"] = tag
        df.loc[m, "coverage_tag_source"] = "manual"
        changed_cov.append((a, "(空)", tag))

    for a, tag in PATCH_FIN.items():
        m = row(a)
        if m is None: continue
        cur = str(df.loc[m, "finish_tag"].iloc[0] or "")
        if cur:
            skipped.append(("fin(已标)", a)); continue
        df.loc[m, "finish_tag"] = tag
        df.loc[m, "finish_type_source"] = "manual"
        changed_fin.append((a, "(空)", tag))

    for a, (frm, to) in PATCH_FORM.items():
        m = row(a)
        if m is None: continue
        cur = str(df.loc[m, "form_tag"].iloc[0] or "")
        if cur == to:
            skipped.append(("form(已一致)", a)); continue
        if cur != frm:
            print(f"  !! form 现值 {cur} 非预期 {frm}，跳过 {a}")
            skipped.append(("form(值异常)", a)); continue
        df.loc[m, "form_tag"] = to
        df.loc[m, "item_form_source"] = "manual"
        changed_form.append((a, frm, to))

    changed = changed_skin or changed_skin_r or changed_cov or changed_fin or changed_form
    if changed:
        BACKUP.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CSV, BACKUP)
        df.to_csv(CSV, index=False, encoding="utf-8")
        print(f"备份原 CSV → {BACKUP}")
    else:
        print("无变更（已全部幂等跳过）")

    print(f"\n肤质追加 {len(changed_skin)} 个：")
    for a, frm, to in changed_skin: print(f"  {a}  [{frm}] → [{to}]")
    print(f"\n肤质整字段替换 {len(changed_skin_r)} 个（假阳性纠正）：")
    for a, frm, to in changed_skin_r: print(f"  {a}  [{frm}] → [{to}]")
    print(f"\n遮瑕补空 {len(changed_cov)} 个（source=manual）：")
    for a, frm, to in changed_cov: print(f"  {a}  [{frm}] → [{to}]")
    print(f"\n妆效补空 {len(changed_fin)} 个（source=manual）：")
    for a, frm, to in changed_fin: print(f"  {a}  [{frm}] → [{to}]")
    print(f"\n质地纠正 {len(changed_form)} 个（source=manual）：")
    for a, frm, to in changed_form: print(f"  {a}  [{frm}] → [{to}]")
    print(f"\n跳过 {len(skipped)} 个：{skipped}")
    print("\n下一步：python load_mysql.py 双写 MySQL products 表")


if __name__ == "__main__":
    main()
