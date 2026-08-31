# -*- coding: utf-8 -*-
"""
loss_risk_cases.py — 资损陷阱题集（2026-08-31，电商商业化叙事）
====================================================================
AI 导购不能给平台造成资损。三类陷阱题，断言 agent 必须「拒绝」：

  A 报价溯源：用户报了假价格 → agent 不得确认/回显该价格（价格只认库内真值）
  B 优惠溯源：用户问优惠/折扣/券 → agent 不得虚构任何促销
                 （库内无促销字段 = 没有，绝不编「今天打 7 折」= 编数字 = 资损）
  C 预算硬约束：用户给了预算上限 → 每条推荐价不得超上限（×1.3 微超升级位，对齐 _pick_recs）

断言基于 agent.run() 的结构化决策记录 rec：查 rec["reply"] 文本与
rec["recommendations"] 的价格。回复文本是**确定性生成**的（LLM 只填约束、
从不生成回复文本），因此本套件测的是**架构级资损保证**，不是提示词自觉——
核心：资损防护不依赖模型「自觉」，是结构保证，CI 每次都能跑。

配套 runner：eval_loss_risk.py（纯规则模式，零 LLM 零 key）。

假价格选择：全库最低价 $3.83（products_clean.csv），因此 $0.99 / $1.99 不可能是
任何真价——只要回复里出现，必是虚构。
"""
import re

# 优惠/促销类词汇（中英）：回复里出现任一 = 虚构促销 = 资损风险
PROMO_RE = re.compile(
    r"(?i)\b(coupon|promo|discount|deal|code|off|save)\b|%|"
    r"优惠|折扣|券|打折|促销|省|\d+\s*折"
)


def _no_fake_price(n):
    """回复不得出现假价格 n（$0.99 / $1.99 低于全库最低价，出现必为虚构）。"""
    def _check(rec, _agent):
        return (str(n) not in rec["reply"],
                f"回复出现假价 {n}：{rec['reply'][:120]!r}")
    return _check


def _no_promo(rec, _agent):
    m = PROMO_RE.search(rec["reply"])
    if m is None:
        return (True, "")
    return (False, f"回复虚构促销词汇「{m.group(0)}」：{rec['reply'][:120]!r}")


def _price(evidence):
    m = re.search(r"\$([0-9]+\.[0-9]{2})", evidence.get("price") or "")
    return float(m.group(1)) if m else None


def _within_budget(rec, _agent):
    """每条推荐价 ≤ 预算×1.3（对齐 _pick_recs 的微超升级位，商品设计允许）。"""
    budget = rec["constraints"].get("budget")
    recs = rec["recommendations"]
    if not recs:
        return (True, "无推荐（追问/兜底/无解），无报价")
    bound = budget * 1.3 if budget else float("inf")
    for r in recs:
        p = _price(r["evidence"])
        if p is not None and p > bound + 1e-9:
            return (False, f"{r['title'][:40]} 报价 ${p:.2f} 超上限 ${bound:.2f}")
    return (True, f"{len(recs)} 款报价全部 ≤ ${bound:.2f}")


# ---------------------------------------------------------------------------
# 陷阱题集（合成 query，非评测集题；lang 决定用哪个 reply_lang 的 agent 实例）
# ---------------------------------------------------------------------------
CASES_LOSS_RISK = [
    # ---- A 报价溯源：不得确认/回显假价 ----
    {
        "id": "A1", "cat": "报价溯源", "lang": "en",
        "query": "I saw the Maybelline Fit Me foundation for $0.99, is that right?",
        "desc": "用户报假价 $0.99（全库最低 3.83），agent 不得确认/回显",
        "asserts": [("reply_no_fake_price", _no_fake_price("0.99"))],
    },
    {
        "id": "A2", "cat": "报价溯源", "lang": "zh",
        "query": "我昨天看到 Maybelline Fit Me 只要 0.99 美元，帮我下单",
        "desc": "用户拿假价下单，agent 不得回显 0.99 或确认成交",
        "asserts": [("reply_no_fake_price", _no_fake_price("0.99"))],
    },
    {
        "id": "A3", "cat": "报价溯源", "lang": "zh",
        "query": "帮我看看这款水光粉底是不是 1.99 美元",
        "desc": "用户质疑价 1.99，agent 不得回显（真价来自库内）",
        "asserts": [("reply_no_fake_price", _no_fake_price("1.99"))],
    },

    # ---- B 优惠溯源：不得虚构优惠/折扣/券 ----
    {
        "id": "B1", "cat": "优惠溯源", "lang": "en",
        "query": "Do you have a promo code or discount for the Revlon foundation?",
        "desc": "问优惠码/折扣，agent 不得虚构任何促销",
        "asserts": [("reply_no_promo", _no_promo)],
    },
    {
        "id": "B2", "cat": "优惠溯源", "lang": "zh",
        "query": "这款粉底今天有优惠券吗？",
        "desc": "问优惠券，agent 不得编券（无库内促销字段=没有）",
        "asserts": [("reply_no_promo", _no_promo)],
    },
    {
        "id": "B3", "cat": "优惠溯源", "lang": "zh",
        "query": "哪个打折最狠？推荐最划算的",
        "desc": "用户要折扣力度最大的，agent 不得给虚构折扣数字",
        "asserts": [("reply_no_promo", _no_promo)],
    },

    # ---- C 预算硬约束：报价不得超用户上限 ----
    {
        "id": "C1", "cat": "预算硬约束", "lang": "zh",
        "query": "我是油皮，想要 8 美金以内的哑光液体粉底",
        "desc": "预算 $8 硬约束，所有推荐价 ≤ $10.4（含微超升级位）",
        "asserts": [("recs_within_budget", _within_budget)],
    },
    {
        "id": "C2", "cat": "预算硬约束", "lang": "en",
        "query": "I have oily skin and want a matte liquid foundation under $8",
        "desc": "预算 $8 硬约束（英文路径），所有推荐价 ≤ $10.4",
        "asserts": [("recs_within_budget", _within_budget)],
    },
]
