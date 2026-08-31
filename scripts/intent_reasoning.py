# -*- coding: utf-8 -*-
"""
意图识别 v13 · 隐藏意图推理 + query 改写
=========================================
用户需求（2026-08-26）：意图识别一定要做好，识别隐藏意图——「用户表达是症状，要反推需求」。
  - 防水 → 隐含防晒/SPF（且说防水多是户外/水上场景）
  - 消除油光 → 隐含油皮/混油肤质 + 哑光妆效
  - 保湿 → 干皮/混干；高遮瑕+熟龄 → 熟龄肌；轻薄 → 轻薄质地

架构（对齐 pangu_search_qp 的 rule + model 双策略）：
  ① 显式意图（规则层）：现有 intent 10 轴多标签，兼容保留 → explicit_intent
  ② 隐藏意图（推理层）：领域知识规则表（双条件触发：intent 轴 ∩ 关键词）→ implicit_intent
  ③ query 改写（对齐 qp revise）：原句 + 隐式关键词注入 → query_rewrite（供检索扩展召回）
  ④ intent_source：rule / llm 预留（v13 落 rule；LLM 零样本推理为预留策略位）

输入  : data/evaluation_set.csv（含 query / intent / skin_label 等）
输出  : evaluation_set.csv 加 4 列（explicit_intent / implicit_intent / intent_source / query_rewrite）
规则表 : docs/intent_reasoning_rules.md（领域知识真相源）
"""
import io
import sys
import re
from pathlib import Path

import pandas as pd

# 幂等 UTF-8 包装：被 agent.py 等模块导入时避免重复包装导致 buffer 被关闭
if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")
ES = ROOT / "data" / "evaluation_set.csv"


# ---------------------------------------------------------------------------
# 隐藏意图推理规则表（显式信号 → 隐式意图）
# 双条件触发：intent 轴命中 且 关键词命中，防误报
# ---------------------------------------------------------------------------
IMPLICIT_RULES = [
    {
        "name": "防水→防晒",
        "intents": set(),          # 关键词强信号：waterproof 只能指防水持妆，无需 intent 轴配合
        "requires_intent": False,
        "kw": re.compile(r"\bwater ?proof\b|\bwater ?resist", re.I),
        "implicit": ["防晒/SPF"],
        "rewrite": ["SPF", "sun protection"],
    },
    {
        "name": "户外/水上场景→防晒+防水",
        "intents": set(),
        "requires_intent": False,
        # 出行意图必须「出行动词 to + 地点/场景名词」：going to Cancun ✓，going to draw ✗（语法歧义）
        "kw": re.compile(
            r"\b(vacation|holiday|beach|pool|swim(?:ming)?|cruise|camping|outdoor|island|tropical|resort)\b|"
            r"\b(?:going|traveling|travelling|headed|flying)\s+to\s+(?:the\s+)?"
            r"(?:beach|beaches|pool|cancun|maui|miami|florida|hawaii|island|tropical|vacation|holiday|trip|cruise|resort)\b",
            re.I),
        "implicit": ["防晒/SPF", "防水持妆"],
        "rewrite": ["waterproof", "SPF"],
    },
    {
        "name": "控油→油皮/混油+哑光",
        "intents": {"控油"},       # 弱信号：matte/shine 单独出现需控油轴确认（防把妆效描述当控油）
        "requires_intent": True,
        "kw": re.compile(r"\bshine\b|\boil[- ]?control\b|\bblot(?:ting)?\b|\bmattif|\bgrease\b|\bgreasy\b|\bmatte\b", re.I),
        "implicit": ["油皮/混油肤质", "哑光妆效"],
        "rewrite": ["matte", "oil-control"],
    },
    {
        "name": "保湿→干皮/混干",
        "intents": set(),          # 强信号：dry/dehydrated/moisture 明确表保湿（修复保湿轴正则漏 dehydrated/moisture）
        "requires_intent": False,
        "kw": re.compile(r"\bhydrat|\bmoisturiz|\bdehydrated\b|\bmoisture\b|\bdry\b|\bflaky\b", re.I),
        "implicit": ["干皮/混干肤质"],
        "rewrite": ["hydrating"],
    },
    {
        "name": "高遮瑕+熟龄→熟龄肌",
        "intents": set(),          # 强信号：over 60 / mature 几乎必是熟龄诉求
        "requires_intent": False,
        "kw": re.compile(r"\bover (?:6\d|7\d|8\d|9\d)\b|\baging\b|\bmature\b|\bsenior\b", re.I),
        "implicit": ["熟龄肌"],
        "rewrite": ["mature skin"],
    },
    {
        "name": "轻薄→轻薄质地",
        "intents": set(),          # 强信号：lightweight 明确表质地诉求
        "requires_intent": False,
        # 修复漏报：id=11 "feels light" 是「感觉轻薄」表达，light 在前/后两种语序都要覆盖
        "kw": re.compile(r"\blight ?weight\b|\b(?:light feel|feels? light)\b|\bthin\b|\bnot heavy\b", re.I),
        "implicit": ["轻薄质地"],
        "rewrite": ["lightweight"],
    },
]


def infer_implicit(intent, query_text):
    """显式 intent + query 原文 → (隐藏意图列表, 改写注入词列表, 命中规则名列表)"""
    intent_set = {x.strip() for x in str(intent).split(";") if x.strip()}
    q = str(query_text)
    implicits, rewrites, rule_hits = [], [], []
    for r in IMPLICIT_RULES:
        # 触发：requires_intent=True 的规则需 intent 轴命中；强信号规则单靠关键词即可
        if r.get("requires_intent", True) and r["intents"] and not (intent_set & r["intents"]):
            continue
        if not r["kw"].search(q):
            continue
        implicits.extend(r["implicit"])
        rewrites.extend(r["rewrite"])
        rule_hits.append(r["name"])
    # 去重保序
    return list(dict.fromkeys(implicits)), list(dict.fromkeys(rewrites)), rule_hits


def rewrite_query(query_text, rewrite_words):
    """query 改写：保留原句 + 句末注入隐式关键词（对齐 qp revise）"""
    q = str(query_text).strip()
    if not rewrite_words:
        return q
    tail = " ".join(rewrite_words)
    # 句末追加（去尾标点，避免句号后拼接）
    q_clean = q.rstrip(".!?。！？")
    return f"{q_clean} ({tail})"


def main():
    es = pd.read_csv(ES)
    rows = []
    for _, r in es.iterrows():
        implicits, rewrites, hits = infer_implicit(r["intent"], r["query"])
        rows.append({
            "explicit_intent": str(r["intent"]),
            "implicit_intent": ";".join(implicits),
            "intent_source": "rule" if implicits else "none",
            "query_rewrite": rewrite_query(r["query"], rewrites),
        })
    res = pd.DataFrame(rows)
    # 覆盖旧列（幂等重跑）
    for col in ["explicit_intent", "implicit_intent", "intent_source", "query_rewrite"]:
        es[col] = res[col].values
    es.to_csv(ES, index=False, encoding="utf-8-sig")

    # 明细展示
    print(f"=== 意图识别 v13：显式 + 隐藏意图推理 + query 改写 ===")
    n_imp = 0
    for i, r in es.iterrows():
        imp = str(r["implicit_intent"])
        if imp:
            n_imp += 1
        print(f"id={i+1:>2} 显式=[{r['intent']}]")
        print(f"    隐藏意图: [{imp or '-'}]  (source={r['intent_source']})")
        print(f"    改写    : {str(r['query_rewrite'])[:95]}")
    print(f"\n隐藏意图命中的 query: {n_imp}/{len(es)}")
    print(f"命中规则分布: ", end="")
    from collections import Counter
    cnt = Counter()
    for _, r in es.iterrows():
        _, _, hits = infer_implicit(r["intent"], r["query"])
        cnt.update(hits)
    print(dict(cnt))


if __name__ == "__main__":
    main()
