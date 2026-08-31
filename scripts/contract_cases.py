# -*- coding: utf-8 -*-
"""
Phase 2 CONTRACT 用例集（24 题）
================================
从 eval_review_50 的 notes + 用户复核意见提炼的期望行为断言。
每个 case = {"id": 题目 id, "asserts": [(断言名, 断言函数)]}。
断言函数签名 (rec, agent) -> (通过?, 失败说明)。rec 是 agent.run() 的结构化决策记录。

硬断言测决策正确性（追问/兜底/硬过滤/假命中/诚实标注/证据齐全/不默认色号），
不依赖检索排序质量；软指标（首答命中/避雷）在 eval_agent.py 单独统计。

query 原文从 MySQL eval_review_50 读取（与评测集同一真相源），本文件只放断言。
"""
import io
import re
import sys
from pathlib import Path

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

sys.path.insert(0, str(Path(__file__).resolve().parent))

KLAIRS = "B01L1Y3OOC"  # 标题含 cushion 但 form_tag=乳霜（假命中样本）


# ---------------------------------------------------------------------------
# 断言工具
# ---------------------------------------------------------------------------
def eq(rec, key, expect, what):
    actual = rec.get(key)
    return (actual == expect, f"{what}={actual}（期望 {expect}）")


def ask_is(rec, decision):
    return eq(rec["ask"], "decision", decision, "ask")


def no_ask(rec, _agent):
    return ask_is(rec, "no_ask")


def ask_all(rec, _agent):
    return ask_is(rec, "ask_all")


def ask_first(rec, _agent):
    return ask_is(rec, "ask_first")


def ask_shade_soft(rec, _agent):
    return ask_is(rec, "ask_shade_soft")


def recs_empty(rec, _agent):
    return (len(rec["recommendations"]) == 0,
            f"推荐应为空（实际 {len(rec['recommendations'])} 条）")


def recs_nonempty(rec, _agent):
    return (len(rec["recommendations"]) > 0,
            "应给出推荐（实际为空）")


def questions_match(rec, pat, what="追问"):
    qs = "；".join(rec["ask"].get("questions") or [])
    return (bool(re.search(pat, qs)), f"{what}问题未命中「{pat}」：{qs or '（无问题）'}")


def friendly_opening(rec, _agent):
    ok = rec.get("reply", "").startswith("为了更好帮您筛选商品")
    return (ok, "ask 回复缺友好预期开场白（为了更好帮您筛选商品…）")


def implicit_has(rec, *imps):
    got = rec["constraints"]["implicit"]
    miss = [i for i in imps if i not in got]
    return (not miss, f"implicit 缺 {miss}（实际 {got}）")


def implicit_is_empty(rec, _agent):
    """规则盲区 / LLM 降级：implicit 应为空（二期实验的 A 组真相）。"""
    got = rec["constraints"]["implicit"]
    return (not got, f"implicit 应为空（规则盲区/降级），实际 {got}")


def llm_degraded(rec, _agent):
    """降级演示题：llm_evidence 应含降级原因（证明 LLM 触发过但没采信）。"""
    ev = rec["llm_evidence"]
    return (bool(ev), f"llm_evidence 应为降级原因（证明 LLM 触发过），实际空")


def hidden_expect(required=None, trusted=True):
    """二期 A/B 断言：按 agent.intent_mode 分支。
    mode A（rule）：implicit 空（规则盲区——实验要证明的）。
    mode B（hybrid）：trusted 题 → implicit 含目标轴 + intent_source=llm + 有推荐；
                      degraded 题（31/32）→ implicit 空 + intent_source=none + 有降级证据。
    """
    def fn(rec, agent):
        if agent.intent_mode == "rule":
            return implicit_is_empty(rec, agent)
        if trusted:
            got = rec["constraints"]["implicit"]
            miss = [i for i in (required or []) if i not in got]
            if miss:
                return False, f"[hybrid] implicit 缺 {miss}（实际 {got}）"
            if rec["intent_source"] != "llm":
                return False, f"[hybrid] intent_source={rec['intent_source']}（期望 llm）"
            return (len(rec["recommendations"]) > 0, "[hybrid] 应给出可兑现推荐")
        if rec["constraints"]["implicit"]:
            return False, f"[hybrid] 降级题 implicit 应为空，实际 {rec['constraints']['implicit']}"
        if rec["intent_source"] != "none":
            return False, f"[hybrid] 降级题 intent_source={rec['intent_source']}（期望 none）"
        return llm_degraded(rec, agent)
    return fn


def hard_has(rec, *skins):
    got = rec["constraints"]["hard"]
    miss = [s for s in skins if s not in got]
    return (not miss, f"hard 缺 {miss}（实际 {got}）")


def coverage_is(rec, cov):
    return eq(rec["constraints"], "coverage", cov, "coverage")


def shade_is(rec, sdir):
    return eq(rec["constraints"], "shade_dir", sdir, "shade_dir")


def budget_positive(rec, _agent):
    b = rec["constraints"]["budget"]
    return (b is not None and b > 0, f"budget={b}（期望 >0）")


def recs_form_ok(rec, agent, form):
    for r in rec["recommendations"]:
        p = agent.idx.by_asin[r["asin"]]
        if p.get("form_tag") != form:
            return False, f"{r['asin']} form_tag={p.get('form_tag')}（期望 {form}）"
    return True, ""


def recs_skin_ok(rec, agent, *hard_skins):
    """hard_skins 每个肤质，推荐商品须含该肤质或全肤质。"""
    for r in rec["recommendations"]:
        p = agent.idx.by_asin[r["asin"]]
        skins = set(s for s in str(p.get("skin_tags") or "").split(";") if s)
        for h in hard_skins:
            if not (skins & {h, "全肤质"}):
                return False, f"{r['asin']} 缺 {h} 标签（skin={p.get('skin_tags')}）"
    return True, ""


def recs_coverage_ok(rec, agent, cov):
    """遮瑕硬过滤：已知不同级排除；未标允许保留但必须带诚实标注（对齐 id22 教训）。"""
    for r in rec["recommendations"]:
        p = agent.idx.by_asin[r["asin"]]
        tag = p.get("coverage_tag")
        if tag == cov:
            continue
        if not tag:
            if any("遮瑕" in h for h in r["evidence"].get("honest") or []):
                continue
            return False, f"{r['asin']} coverage 未标且无诚实标注"
        return False, f"{r['asin']} coverage_tag={tag}（期望 {cov}）"
    return True, ""


def recs_finish_ok(rec, agent, fin):
    for r in rec["recommendations"]:
        p = agent.idx.by_asin[r["asin"]]
        if p.get("finish_tag") != fin:
            return False, f"{r['asin']} finish_tag={p.get('finish_tag')}（期望 {fin}）"
    return True, ""


def recs_finish_not(rec, agent, fin):
    for r in rec["recommendations"]:
        p = agent.idx.by_asin[r["asin"]]
        if p.get("finish_tag") == fin:
            return False, f"{r['asin']} finish_tag={fin} 与需求相悖"
    return True, ""


def no_asin_in_recs(rec, asin, what):
    if asin in [r["asin"] for r in rec["recommendations"]]:
        return False, f"{what} {asin} 不应出现在推荐里（标题假命中）"
    return True, ""


def recs_defect_free(rec, agent):
    """负约束（卡粉/刺激/闷痘/脱妆）命中的缺陷证据商品不得进推荐。"""
    axes = set(rec["constraints"].get("negative_axes") or [])
    for r in rec["recommendations"]:
        hit = axes & agent.defect.get(r["asin"], set())
        if hit:
            return False, f"{r['asin']} 命中缺陷证据 {sorted(hit)}（query 负约束 {sorted(axes)}）"
    return True, ""


def recs_shade_not_dark(rec, agent):
    """色号方向=白皙时，不得推深色桶（色号避雷=避深色）。"""
    if rec["constraints"]["shade_dir"] != "fair":
        return True, ""
    for r in rec["recommendations"]:
        shades = str(agent.idx.by_asin[r["asin"]].get("shade_tag") or "").split(";")
        if "深色" in shades:
            return False, f"{r['asin']} 深色桶混入白皙推荐（shade_tag={shades}）"
    return True, ""


def evidence_complete(rec, _agent):
    """四件套 + 🔗asin：每条推荐必须齐全。price 例外——缺价商品允许留白
    （round-3 产品决策：缺价不露「待核实」，前端/回复直接省略价格，保信任感）。"""
    for r in rec["recommendations"]:
        e = r["evidence"]
        for key in ("tags", "rating", "heat", "link", "citation"):
            if not e.get(key):
                return False, f"{r['asin']} evidence.{key} 缺失"
        if not str(e["link"]).startswith("🔗"):
            return False, f"{r['asin']} link 缺 🔗asin"
        # 价格诚实契约：商品真有价 → 必须展示；商品缺价 → 必须留白（不虚标、不露待核实）
        pr = _to_float(_agent.idx.by_asin[r["asin"]].get("price"))
        if pr is not None and not e.get("price"):
            return False, f"{r['asin']} 有价但 evidence.price 缺失"
        if pr is None and e.get("price"):
            return False, f"{r['asin']} 缺价但标了 {e.get('price')}"
    return True, ""


def no_shade_citation(rec, _agent):
    """query 没提色号时，推荐不得 cite 色号（id3 用户批「没答色号直接判断白皙」）。"""
    for r in rec["recommendations"]:
        e = r["evidence"]
        for field in (e["citation"], e["tags"]):
            if re.search(r"色号|白皙|深色", field):
                return False, f"{r['asin']} 默认 cite 了色号：{field}"
    return True, ""


def honest_unmarked_coverage(rec, agent):
    """query 指定遮瑕但商品 coverage_tag 未标 → 必须带诚实标注。"""
    for r in rec["recommendations"]:
        p = agent.idx.by_asin[r["asin"]]
        if not p.get("coverage_tag"):
            if not any("遮瑕" in h for h in r["evidence"].get("honest") or []):
                return False, f"{r['asin']} coverage 未标但无诚实标注（honest={r['evidence'].get('honest')}）"
    return True, ""


def price_not_fabricated(rec, agent):
    """预算查询：缺价商品必须留白（round-3：不露「待核实」），不得虚报任何价格。"""
    for r in rec["recommendations"]:
        p = agent.idx.by_asin[r["asin"]]
        if _to_float(p.get("price")) is None:
            if r["evidence"].get("price"):
                return False, f"{r['asin']} 缺价但标了 {r['evidence'].get('price')}"
    return True, ""


def budget_range_ok(rec, agent):
    """预算查询：每条推荐要么缺价、要么 ≤ budget×1.3（2预算内+1微超升级位）。"""
    b = rec["constraints"]["budget"]
    if b is None or b <= 0:
        return True, ""
    for r in rec["recommendations"]:
        pr = _to_float(agent.idx.by_asin[r["asin"]].get("price"))
        if pr is not None and pr > b * 1.3:
            return False, f"{r['asin']} 价格 ${pr:.2f} 超 budget ${b:.0f}×1.3"
    return True, ""


def fallback_level(rec, level):
    ok = rec["fallback"]["triggered"] and rec["fallback"]["level"] == level
    return (ok, f"fallback 期望 level={level}（实际 triggered={rec['fallback']['triggered']} "
                f"level={rec['fallback'].get('level')}）")


def fallback_honest_note(rec, _agent):
    return fallback_level(rec, "honest_note")


def fallback_full(rec, _agent):
    return fallback_level(rec, "full")


def fallback_message_has(rec, pat):
    msg = rec["fallback"].get("message") or ""
    return (bool(re.search(pat, msg)), f"兜底话术未命中「{pat}」：{msg[:60]}")


def alternatives_nonempty(rec, _agent):
    alts = rec["fallback"].get("alternatives") or []
    return (bool(alts), "兜底应给出替代方向（alternatives 非空）")


def _to_float(x):
    try:
        f = float(str(x).replace("$", "").strip())
        return f if f == f else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 24 题 CONTRACT 用例
# ---------------------------------------------------------------------------
CASES = [
    # id=1 干皮保湿直说：不追问，证据齐全
    {"id": 1, "asserts": [
        ("no_ask", no_ask),
        ("evidence_complete", evidence_complete),
    ]},

    # id=2 控油≠哑光：先追问妆效（D-2），问题命中「哑光/妆效」；追问时不硬推
    {"id": 2, "asserts": [
        ("ask_first", ask_first),
        ("question_asks_finish", lambda r, a: questions_match(r, r"哑光|妆效")),
        ("recs_empty", recs_empty),
    ]},

    # id=3 熟龄高遮瑕：不追问；不默认色号；只推高遮瑕
    {"id": 3, "asserts": [
        ("no_ask", no_ask),
        ("coverage=高遮瑕", lambda r, a: coverage_is(r, "高遮瑕")),
        ("recs_coverage_ok", lambda r, a: recs_coverage_ok(r, a, "高遮瑕")),
        ("no_shade_citation", no_shade_citation),
        ("evidence_complete", evidence_complete),
    ]},

    # id=4 模糊：ask_all 合并式一轮问完 + 友好预期；追问时不硬推
    {"id": 4, "asserts": [
        ("ask_all", ask_all),
        ("questions>=3", lambda r, a: (len(r["ask"]["questions"]) >= 3,
                                       f"问题数 {len(r['ask']['questions'])} < 3")),
        ("friendly_opening", friendly_opening),
        ("recs_empty", recs_empty),
    ]},

    # id=5 「日常」不改变候选集：肤质+妆效才改变 → 一轮问完
    {"id": 5, "asserts": [
        ("ask_all", ask_all),
        ("recs_empty", recs_empty),
    ]},

    # id=6 新手引导式追问
    {"id": 6, "asserts": [
        ("ask_all", ask_all),
        ("newbie_flagged", lambda r, a: eq(r["constraints"], "newbie", True, "newbie")),
        ("recs_empty", recs_empty),
    ]},

    # id=7 敏感痘肌双硬约束 + 高遮瑕 + 缺陷证据避雷
    {"id": 7, "asserts": [
        ("no_ask", no_ask),
        ("hard=敏感+痘痘", lambda r, a: hard_has(r, "敏感肌", "痘痘肌")),
        ("recs_skin_ok", lambda r, a: recs_skin_ok(r, a, "敏感肌", "痘痘肌")),
        ("recs_coverage_ok", lambda r, a: recs_coverage_ok(r, a, "高遮瑕")),
        ("recs_defect_free", recs_defect_free),
        ("evidence_complete", evidence_complete),
    ]},

    # id=8 纯缺陷证据避雷（轻薄/持妆无相反标签轴 → 查 product_defect_evidence）
    {"id": 8, "asserts": [
        ("no_ask", no_ask),
        ("neg_axes=卡粉", lambda r, a: eq(r["constraints"], "negative_axes", ["卡粉"],
                                          "negative_axes")),
        ("recs_defect_free", recs_defect_free),
        ("evidence_complete", evidence_complete),
    ]},

    # id=9 油皮敏感双标：哑光✓ + 敏感肌✓，无肤质标签全排除；症状「break out」不误判痘痘肌
    {"id": 9, "asserts": [
        ("no_ask", no_ask),
        ("hard=敏感肌非痘痘", lambda r, a: (
            "敏感肌" in r["constraints"]["hard"] and "痘痘肌" not in r["constraints"]["hard"],
            f"hard={r['constraints']['hard']}（期望仅敏感肌）")),
        ("recs_skin_ok", lambda r, a: recs_skin_ok(r, a, "敏感肌")),
        ("recs_finish_ok", lambda r, a: recs_finish_ok(r, a, "哑光")),
        ("evidence_complete", evidence_complete),
    ]},

    # id=10 Goth 白皙：不追问；浅色方向；不推深色桶
    {"id": 10, "asserts": [
        ("no_ask", no_ask),
        ("shade=fair", lambda r, a: shade_is(r, "fair")),
        ("recs_shade_not_dark", recs_shade_not_dark),
        ("evidence_complete", evidence_complete),
    ]},

    # id=11 色号×质地双命中：矿物粉状 + 白皙；深色矿物粉=教科书假正例（排除）
    {"id": 11, "asserts": [
        ("no_ask", no_ask),
        ("shade=fair", lambda r, a: shade_is(r, "fair")),
        ("recs_form_ok=粉状", lambda r, a: recs_form_ok(r, a, "粉状")),
        ("recs_shade_not_dark", recs_shade_not_dark),
        ("evidence_complete", evidence_complete),
    ]},

    # id=12 纯色号匹配：不追问，浅色方向，不推深色
    {"id": 12, "asserts": [
        ("no_ask", no_ask),
        ("shade=fair", lambda r, a: shade_is(r, "fair")),
        ("recs_shade_not_dark", recs_shade_not_dark),
        ("evidence_complete", evidence_complete),
    ]},

    # id=13 预算=硬约束：色号软追问（加分）；缺价诚实标注；价格范围不超预算档
    {"id": 13, "asserts": [
        ("ask_shade_soft", ask_shade_soft),
        ("budget_positive", budget_positive),
        ("question_asks_shade", lambda r, a: questions_match(r, r"色号|更精准")),
        ("price_not_fabricated", price_not_fabricated),
        ("budget_range_ok", budget_range_ok),
        ("evidence_complete", evidence_complete),
    ]},

    # id=14 比价≠只推最便宜：油皮哑光先过滤妆效轴；预算档位 + 缺价诚实 + 色号软追问
    {"id": 14, "asserts": [
        ("ask_shade_soft", ask_shade_soft),
        ("budget_positive", budget_positive),
        ("question_asks_shade", lambda r, a: questions_match(r, r"色号|更精准")),
        ("recs_finish_ok=哑光", lambda r, a: recs_finish_ok(r, a, "哑光")),
        ("price_not_fabricated", price_not_fabricated),
        ("budget_range_ok", budget_range_ok),
        ("evidence_complete", evidence_complete),
    ]},

    # id=15 预算极紧：先问清（肤质/妆效/色号全缺 → ask_all），尤其色号
    {"id": 15, "asserts": [
        ("ask_all", ask_all),
        ("question_asks_shade", lambda r, a: questions_match(r, r"色号")),
        ("recs_empty", recs_empty),
    ]},

    # id=16 坎昆：显式只给防水，必须用场景规则补出防晒（意图完整性）
    {"id": 16, "asserts": [
        ("no_ask", no_ask),
        ("implicit=防晒+防水", lambda r, a: implicit_has(r, "防晒", "防水持妆")),
        ("evidence_complete", evidence_complete),
    ]},

    # id=17 高遮瑕粉状持妆：只推高遮瑕+粉状（query 原文已补全，粉状轴可测）；
    # query 只给「right shade」未指明方向 → 不得默认白皙（不默认色号）
    {"id": 17, "asserts": [
        ("no_ask", no_ask),
        ("coverage=高遮瑕", lambda r, a: coverage_is(r, "高遮瑕")),
        ("recs_coverage_ok", lambda r, a: recs_coverage_ok(r, a, "高遮瑕")),
        ("form=粉状", lambda r, a: eq(r["constraints"], "form", "粉状", "form")),
        ("recs_form_ok=粉状", lambda r, a: recs_form_ok(r, a, "粉状")),
        ("shade=None", lambda r, a: shade_is(r, None)),
        ("no_shade_citation", no_shade_citation),
        ("evidence_complete", evidence_complete),
    ]},

    # id=18 油皮持妆=哑光控油+持妆口碑：水光款双重负面，不得推
    {"id": 18, "asserts": [
        ("no_ask", no_ask),
        ("implicit=油皮控油+哑光", lambda r, a: implicit_has(r, "油皮控油", "哑光妆效")),
        ("recs_finish_not=水光", lambda r, a: recs_finish_not(r, a, "水光")),
        ("evidence_complete", evidence_complete),
    ]},

    # id=19 敏感痘肌 hypoallergenic：可自证温和（敏感/全肤质标签）+ 痘痘肌双硬约束
    {"id": 19, "asserts": [
        ("no_ask", no_ask),
        ("hard=敏感+痘痘", lambda r, a: hard_has(r, "敏感肌", "痘痘肌")),
        ("recs_skin_ok", lambda r, a: recs_skin_ok(r, a, "敏感肌", "痘痘肌")),
        ("evidence_complete", evidence_complete),
    ]},

    # id=20 换季「一件到底」：诚实兜底第一层（honest_note），仍推平衡型 + 诚实说明
    {"id": 20, "asserts": [
        ("no_ask", no_ask),
        ("fallback=honest_note", fallback_honest_note),
        ("honest_msg", lambda r, a: fallback_message_has(r, r"自动调肤|平衡型|分区")),
        ("recs_nonempty", recs_nonempty),
        ("evidence_complete", evidence_complete),
    ]},

    # id=21 又油又干同时存在：需求无解 → 直说 + 替代方向，不硬推
    {"id": 21, "asserts": [
        ("no_ask", no_ask),
        ("unsolvable_flagged", lambda r, a: eq(r["constraints"], "unsolvable", True, "unsolvable")),
        ("fallback=full", fallback_full),
        ("honest_msg", lambda r, a: fallback_message_has(r, r"没有|不存在|不现实|同时")),
        ("alternatives_nonempty", alternatives_nonempty),
        ("recs_empty", recs_empty),
    ]},

    # id=22 轻遮瑕：只推轻遮瑕；coverage 未标须诚实标注
    {"id": 22, "asserts": [
        ("no_ask", no_ask),
        ("recs_coverage_ok", lambda r, a: recs_coverage_ok(r, a, "轻遮瑕")),
        ("honest_unmarked_coverage", honest_unmarked_coverage),
        ("evidence_complete", evidence_complete),
    ]},

    # id=23 粉状：质地看 form_tag 值；KLAIRS（标题含 Pact 实际乳霜）不得混入
    {"id": 23, "asserts": [
        ("no_ask", no_ask),
        ("recs_form_ok=粉状", lambda r, a: recs_form_ok(r, a, "粉状")),
        ("no_kairs_fake_hit", lambda r, a: no_asin_in_recs(r, KLAIRS, "KLAIRS 假命中")),
        ("evidence_complete", evidence_complete),
    ]},

    # id=24 气垫：只推 form_tag=气垫；KLAIRS 名字带 cushion 实际乳霜=假命中排除
    {"id": 24, "asserts": [
        ("no_ask", no_ask),
        ("recs_form_ok=气垫", lambda r, a: recs_form_ok(r, a, "气垫")),
        ("no_kairs_fake_hit", lambda r, a: no_asin_in_recs(r, KLAIRS, "KLAIRS 假命中")),
        ("evidence_complete", evidence_complete),
    ]},
]


# ---------------------------------------------------------------------------
# 9 条 hidden-intent 题（ids 25-33）CASES_HIDDEN —— 二期 A/B 实验断言
# mode A（rule）：implicit 空（规则盲区，9 题全部 ask_all、答不了——实验前提）
# mode B（hybrid）：兑现题（25-30/33）断言 implicit 含目标轴 + intent_source=llm + 有推荐；
#                  降级题（31 熟龄肌 / 32 轻薄质地）断言 implicit 空 + intent_source=none
#                  + llm_evidence 有降级原因（LLM 识别出但库兑现不了 → 拒绝 → 降级 A==B）
# eval_compare 双模式各跑一遍；只 import 有 stdout 保护的 contract_cases。
# ---------------------------------------------------------------------------
CASES_HIDDEN = [
    {"id": 25, "asserts": [
        ("hidden_ab_防晒防水", hidden_expect(["防水持妆", "防晒"], trusted=True)),
    ]},
    {"id": 26, "asserts": [
        ("hidden_ab_防水控油哑光", hidden_expect(["防水持妆", "油皮控油", "哑光妆效"], trusted=True)),
    ]},
    {"id": 27, "asserts": [
        ("hidden_ab_控油哑光", hidden_expect(["油皮控油", "哑光妆效"], trusted=True)),
    ]},
    {"id": 28, "asserts": [
        ("hidden_ab_干皮保湿", hidden_expect(["干皮保湿"], trusted=True)),
    ]},
    {"id": 29, "asserts": [
        ("hidden_ab_防水持妆", hidden_expect(["防水持妆"], trusted=True)),
    ]},
    {"id": 30, "asserts": [
        ("hidden_ab_防晒防水", hidden_expect(["防晒", "防水持妆"], trusted=True)),
    ]},
    {"id": 31, "asserts": [
        ("hidden_ab_熟龄降级", hidden_expect(trusted=False)),
    ]},
    {"id": 32, "asserts": [
        ("hidden_ab_轻薄降级", hidden_expect(trusted=False)),
    ]},
    {"id": 33, "asserts": [
        ("hidden_ab_控油哑光", hidden_expect(["油皮控油", "哑光妆效"], trusted=True)),
    ]},
]
