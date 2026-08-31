# -*- coding: utf-8 -*-
"""
商品数据清洗脚本 —— AI电商主项目·模块①「商品知识库」
====================================================
输入  : Amazon All Beauty 商品元数据 (meta_All_Beauty.jsonl.gz)
输出  :
  - data/products_clean.csv    干净的商品属性表（UTF-8 BOM，Excel 可直接打开）
  - data/products_clean.xlsx   同上，Excel 版
  - data/quality_report.md     数据质量报告（脏数据问题清单 + 完整度）

范围  : 面部粉底液（foundation family），排除化妆工具/粉扑/刷子等噪声。
"""
import gzip
import json
import re
import sys
import io
from pathlib import Path

import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")
META = Path(r"C:\Users\Lenovo\Desktop\meta_All_Beauty.jsonl.gz")
OUT_CSV = ROOT / "data" / "products_clean.csv"
OUT_XLSX = ROOT / "data" / "products_clean.xlsx"
OUT_REPORT = ROOT / "data" / "quality_report.md"

# ---------------------------------------------------------------------------
# 1. 过滤规则
# ---------------------------------------------------------------------------
# 包含：标题里出现粉底液家族关键词
INCLUDE_KW = re.compile(r"\b(foundation|bb\s?cream|cushion)\b", re.I)
# 排除：化妆工具 / 配件 / 非面部底妆（出现即丢弃；\w* 覆盖复数如 brushes/sponges）
# 注：\bbrush\w*\b 不会匹配 "airbrush"（airbrush 内部无词边界），airbrush foundation 是合法商品
EXCLUDE_KW = re.compile(
    r"\b(?:brush\w*|sponge\w*|blender\w*|puff\w*|applicator\w*|remover\w*|"
    r"sharpen\w*|palette\w*|stencil\w*|case\w*|kit\w*|tool\w*|mat\b|"
    r"hair\w*|roller\w*|seat\w*|chair\w*|wheelchair\w*|coccyx\w*|orthopedic\w*|"
    r"pillow\w*|makeup set|brush set|bundle\w*|makeup base)\b", re.I
)
# 排除：标题里明确是"非粉底液"的散粉/定妆粉/妆前/口红/眼妆等
NON_FOUNDATION_KW = re.compile(
    r"\b(loose powder|translucent powder|setting powder|finishing powder|"
    r"primer|lip|lips|eyelash|lash|eyebrow|brow|mascara|eyeliner|"
    r"eye shadow|eyeshadow|blush|bronzer|highlight)\b", re.I
)

# 肤质/妆效/质地 关键词 → 规范值（title 兜底抽取用）
SKIN_KW = [
    ("Oily", r"\boily\b"), ("Dry", r"\bdry\b"), ("Sensitive", r"\bsensitive\b"),
    ("Combination", r"\bcombination\b|combo"), ("Normal", r"\bnormal\b"),
]
FINISH_KW = [
    ("Matte", r"\bmatte\b"), ("Dewy", r"\bdewy\b"), ("Glow/Radiant", r"\bglow|radiant\b"),
    ("Natural", r"\bnatural\b"), ("Satin", r"\bsatin\b"),
]
FORM_KW = [
    ("Liquid", r"\bliquid\b"), ("Cream", r"\bcream\b"), ("Powder", r"\bpowder\b"),
    ("Stick", r"\bstick\b"), ("Cushion", r"\bcushion\b"),
]

# details 英文值 → 中文标签（分类标签体系）
FORM_TAG = {"Liquid": "液体", "Cream": "乳霜", "Powder": "粉状", "Stick": "棒状", "Cushion": "气垫"}
SKIN_TAG = {"Oily": "油皮", "Dry": "干皮", "Sensitive": "敏感肌", "Combination": "混合肌", "Normal": "中性", "All": "全肤质"}
FINISH_TAG = {"Matte": "哑光", "Dewy": "水光", "Glow": "光泽", "Radiant": "光泽", "Natural": "自然", "Satin": "缎面"}


def find_kw(title, kw_list):
    for value, pattern in kw_list:
        if re.search(pattern, title, re.I):
            return value
    return None  # 找不到返回 None（不能返回空串，否则 fillna 后会被误判为"存在"）


# ---------------------------------------------------------------------------
# 2. 读取 + 过滤 + 去重
# ---------------------------------------------------------------------------
print("读取 meta_All_Beauty ...")
rows = []
n_kw_match = 0  # 标题命中粉底液关键词（排除前）
with gzip.open(META, "rt", encoding="utf-8", errors="replace") as f:
    for line in f:
        try:
            p = json.loads(line)
        except Exception:
            continue
        title = p.get("title") or ""
        if not INCLUDE_KW.search(title):
            continue
        n_kw_match += 1
        if EXCLUDE_KW.search(title) or NON_FOUNDATION_KW.search(title):
            continue
        rows.append(p)

df = pd.DataFrame(rows)
n_raw = len(df)  # 排除工具/非粉底液后
df = df.drop_duplicates(subset=["parent_asin"] if "parent_asin" in df else ["asin"])
n_dedup = len(df)  # 去重后
print(f"  标题命中: {n_kw_match} → 排除工具/非粉底液后: {n_raw} → 去重后: {n_dedup}")

# ---------------------------------------------------------------------------
# 3. 属性结构化
# ---------------------------------------------------------------------------
def get_details(p):
    d = p.get("details") or {}
    return d if isinstance(d, dict) else {}

def extract(row):
    p = row
    d = get_details(p)
    title = p.get("title") or ""
    return {
        "asin": p.get("asin"),
        "parent_asin": p.get("parent_asin"),
        "title": title,
        "store": p.get("store"),
        # 品牌：优先 details.Brand，兜底 store（store 常是批发商，需注意）
        "brand": d.get("Brand") or p.get("store"),
        "brand_detail": d.get("Brand"),
        "price": p.get("price"),
        "average_rating": p.get("average_rating"),
        "rating_number": p.get("rating_number"),
        # details 直接可用属性
        "item_form": d.get("Item Form"),
        "item_form_detail": d.get("Item Form"),
        "skin_type_detail": d.get("Skin Type"),
        "finish_type_detail": d.get("Finish Type"),
        "coverage": d.get("Coverage"),
        "skin_tone": d.get("Skin Tone"),
        "scent": d.get("Scent"),
        "benefits": d.get("Product Benefits"),
        "uses": d.get("Recommended Uses For Product"),
        "color": d.get("Color") or d.get("Color Name"),
        "unit_count": d.get("Unit Count") or d.get("Number of Items") or d.get("Number of Pieces"),
        "is_discontinued": d.get("Is Discontinued By Manufacturer"),
        # title 兜底抽取（details 缺失时）
        "item_form_title": find_kw(title, FORM_KW),
        "skin_type_title": find_kw(title, SKIN_KW),
        "finish_type_title": find_kw(title, FINISH_KW),
    }

df2 = pd.DataFrame([extract(p) for p in df.to_dict("records")])

# 合并 details 与 title 兜底：details 优先，title 补充，并记录来源（置信度）
def merge_with_source(d, detail_col, title_col):
    sources = []
    for _, r in d[[detail_col, title_col]].iterrows():
        if pd.notna(r[detail_col]):
            sources.append("details")
        elif pd.notna(r[title_col]):
            sources.append("title")
        else:
            sources.append("missing")
    return sources

df2["item_form"] = df2["item_form_detail"].fillna(df2["item_form_title"])
df2["skin_type"] = df2["skin_type_detail"].fillna(df2["skin_type_title"])
df2["finish_type"] = df2["finish_type_detail"].fillna(df2["finish_type_title"])
df2["item_form_source"] = merge_with_source(df2, "item_form_detail", "item_form_title")
df2["skin_type_source"] = merge_with_source(df2, "skin_type_detail", "skin_type_title")
df2["finish_type_source"] = merge_with_source(df2, "finish_type_detail", "finish_type_title")
df2 = df2.drop(columns=["item_form_title", "skin_type_title", "finish_type_title"])

# ---------------------------------------------------------------------------
# 4. 分类标签体系（自建）
# ---------------------------------------------------------------------------
df2["category_tag"] = "底妆/粉底液"
df2["form_tag"] = df2["item_form"].map(lambda v: FORM_TAG.get(str(v).strip().split(",")[0].strip(), "") if pd.notna(v) else "")

def map_skin(v):
    if pd.isna(v):
        return ""
    s = str(v)
    # 组合值优先细分：Oily+Combination=偏油混合(混油)，Dry+Combination=偏干混合(混干)
    if "Combination" in s and "Oily" in s:
        return "混油"
    if "Combination" in s and "Dry" in s:
        return "混干"
    # 单独 Combination = 混合肌（T区油两颊干，中性表述）
    for k in ["Oily", "Dry", "Sensitive", "Combination", "Normal", "All"]:
        if k in s:
            return SKIN_TAG[k]
    return ""


def map_skins(v):
    """多标签：返回商品所有适用皮肤标签（集合），供评测多标签匹配用。
    规则：全肤质/敏感肌/痘痘肌 是硬标签；混油/混干/油皮/干皮/混合肌/中性 可并存。
    """
    if pd.isna(v):
        return []
    s = str(v)
    out = set()
    has = lambda k: k in s
    if has("All") or "Universal" in s or "all skin" in s:
        out.add("全肤质")
    if has("Sensitive"):
        out.add("敏感肌")
    if has("Acne") or "breakout" in s:
        out.add("痘痘肌")
    if has("Oily") and has("Combination"):
        out.add("混油")
        out.add("混合肌")
    elif has("Oily"):
        out.add("油皮")
    if has("Dry") and has("Combination"):
        out.add("混干")
        out.add("混合肌")
    elif has("Dry"):
        out.add("干皮")
    if has("Combination") and not (has("Oily") or has("Dry")):
        out.add("混合肌")
    if has("Normal"):
        out.add("中性")
    if has("Mature"):
        out.add("熟龄肌")
    return sorted(out)

df2["skin_tag"] = df2["skin_type"].map(map_skin)
df2["skin_tags"] = df2["skin_type"].map(lambda v: ";".join(map_skins(v)))  # 多标签，分号分隔

def map_finish(v):
    if pd.isna(v):
        return ""
    s = str(v)
    for k in ["Matte", "Dewy", "Glow", "Radiant", "Natural", "Satin"]:
        if k in s:
            return FINISH_TAG[k]
    return ""

df2["finish_tag"] = df2["finish_type"].map(map_finish)
df2["coverage_tag"] = df2["coverage"].map(
    lambda v: {"Light": "轻遮瑕", "Medium": "中度遮瑕", "Full": "高遮瑕"}.get(
        str(v).strip().split(",")[0].strip() if pd.notna(v) else "", "") if pd.notna(v) else ""
)

# 品牌清洗：store 兜底进来的是批发商名，抽大写词做"品牌候选"（留给后续人工校验）
df2["brand_clean"] = df2["brand"].fillna("")

# ---------------------------------------------------------------------------
# 5. 质量校验
# ---------------------------------------------------------------------------
checks = {}
checks["标题命中粉底液关键词"] = n_kw_match
checks["排除工具/非粉底液后"] = n_raw
checks["按 parent_asin 去重后"] = n_dedup
checks["价格缺失"] = int(df2["price"].isna().sum())
price = pd.to_numeric(df2["price"], errors="coerce")
checks["价格缺失率"] = f"{checks['价格缺失'] * 100 // n_dedup}%"
checks["价格异常(≤0或>150$)"] = int(((price <= 0) | (price > 150)).sum())
checks["评分异常(超出1-5)"] = int(((df2["average_rating"] < 1) | (df2["average_rating"] > 5)).sum())
checks["重复标题"] = int(df2["title"].duplicated().sum())
checks["已停产商品"] = int(df2["is_discontinued"].fillna("").astype(str).str.lower().eq("yes").sum())

# 属性冲突检测：title 说 oily 但 details 写 Dry / 说 matte 但写 Dewy
def conflict_skin(r):
    t = str(r["title"])
    d = str(r["skin_type_detail"]) if pd.notna(r["skin_type_detail"]) else ""
    if re.search(r"\boily\b", t, re.I) and "Dry" in d:
        return True
    if re.search(r"\bdry\b", t, re.I) and "Oily" in d:
        return True
    return False

def conflict_finish(r):
    t = str(r["title"])
    d = str(r["finish_type_detail"]) if pd.notna(r["finish_type_detail"]) else ""
    if re.search(r"\bmatte\b", t, re.I) and ("Dewy" in d or "Glow" in d):
        return True
    if re.search(r"\bglow|dewy\b", t, re.I) and "Matte" in d:
        return True
    return False

df2["conflict_skin"] = df2.apply(conflict_skin, axis=1)
df2["conflict_finish"] = df2.apply(conflict_finish, axis=1)
checks["属性冲突-肤质(title vs details)"] = int(df2["conflict_skin"].sum())
checks["属性冲突-妆效(title vs details)"] = int(df2["conflict_finish"].sum())

# ---------------------------------------------------------------------------
# 6. 输出
# ---------------------------------------------------------------------------
out_cols = [
    "asin", "parent_asin", "title", "brand", "brand_clean", "price",
    "average_rating", "rating_number", "item_form", "skin_type", "finish_type",
    "item_form_source", "skin_type_source", "finish_type_source",
    "coverage", "skin_tone", "scent", "benefits", "uses", "color", "unit_count",
    "category_tag", "form_tag", "skin_tag", "skin_tags", "finish_tag", "coverage_tag",
    "conflict_skin", "conflict_finish",
]
df_out = df2[out_cols]
df_out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
df_out.to_excel(OUT_XLSX, index=False)
print(f"已写出: {OUT_CSV.name} / {OUT_XLSX.name}  ({len(df_out)} 行, {len(out_cols)} 列)")

# 质量报告
lines = [
    "# 数据质量报告 —— 商品知识库·模块①",
    "",
    f"- 数据源: Amazon All Beauty 商品元数据 (meta_All_Beauty.jsonl.gz)，全库 {len(rows) and '—'}",
    f"- 范围: 面部粉底液（foundation / bb cream / cushion），排除化妆工具/散粉/妆前等",
    "",
    "## 清洗漏斗",
    "",
]
# 漏斗（用已统计的 n_kw_match，避免重扫文件）
lines.append("| 阶段 | 商品数 |")
lines.append("|---|---|")
lines.append(f"| 全库 | 112,590 |")
lines.append(f"| 标题含 foundation/bb cream/cushion | {n_kw_match} |")
lines.append(f"| 排除工具/散粉/妆前等非粉底液后 | {n_raw} |")
lines.append(f"| 按 parent_asin 去重后 | {n_dedup} |")
lines += ["", "## 数据质量问题", ""]
lines.append("| 检查项 | 结果 |")
lines.append("|---|---|")
for k, v in checks.items():
    lines.append(f"| {k} | {v} |")
lines += ["", "## 属性完整度", ""]
lines.append("| 属性 | details 原始完整度 | 合并 title 兜底后 |")
lines.append("|---|---|---|")
for col, raw_col in [
    ("brand", "brand_detail"), ("price", "price"), ("item_form", "item_form_detail"),
    ("skin_type", "skin_type_detail"), ("finish_type", "finish_type_detail"),
    ("coverage", "coverage"), ("skin_tone", "skin_tone"), ("scent", "scent"),
]:
    n_raw_col = int(df2[raw_col].notna().sum()) if raw_col in df2 else int(df2[col].notna().sum())
    n_merged = int(df2[col].notna().sum())
    lines.append(f"| {col} | {n_raw_col * 100 // n_dedup}% | {n_merged * 100 // n_dedup}% |")
lines += ["", "## 字段来源分布（置信度）", ""]
lines.append("| 字段 | 来自 details | 来自 title 推断 | 缺失 |")
lines.append("|---|---|---|---|")
for col in ["item_form", "skin_type", "finish_type"]:
    s = df2[f"{col}_source"].value_counts()
    lines.append(
        f"| {col} | {s.get('details', 0)} | {s.get('title', 0)} | {s.get('missing', 0)} |"
    )
lines += ["", "## 属性冲突样例", ""]
conf = df_out[df_out["conflict_skin"] | df_out["conflict_finish"]]
if len(conf):
    lines.append("| 标题 | 冲突 |")
    lines.append("|---|---|")
    for _, r in conf.head(8).iterrows():
        lines.append(f"| {str(r['title'])[:60]} | 肤质冲突={r['conflict_skin']} 妆效冲突={r['conflict_finish']} |")
else:
    lines.append("无")

OUT_REPORT.write_text("\n".join(lines), encoding="utf-8")
print(f"已写出质量报告: {OUT_REPORT.name}")
