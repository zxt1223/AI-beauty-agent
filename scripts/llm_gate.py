# -*- coding: utf-8 -*-
"""
llm_gate.py — 对话意图闸门（Harness「工具拦截 + 置信度分支」落地）
=================================================================
多轮追问先回答一个问题：「用户这句话是要推荐商品，还是在问别的事？」
只有「推荐/调整需求」才进商品库（走现有确定性推荐引擎）；对比/查色号/求助/闲聊
一律不碰推荐器，走 LLM 应答——这是「校对」的最小落地：输出前先对一遍用户在问什么，
答不上/拿不准就诚实兜底，绝不把求助再推一遍商品。

置信度分支（用户定标 2026-08-31）：
  意图置信度 > 85%     → 直出（route=auto）
  意图置信度 60%~85%   → 生成回答 + 人工复核徽章（route=review）
  意图置信度 < 60%     → 不生成回答，直接转人工（route=human）

事实防幻觉：对比/色号类回答 =「确定性事实 + LLM 润色」——结构化数据（标签/评分/条数/
差评主题/色号标签/价格）由代码从库内取真并注入 prompt，LLM 只能基于这些事实改写语气，
不得编造评分/条数/色号/评论原话。事实永远来自数据层，不来自模型。

安全：DEEPSEEK_API_KEY 只从 scripts/.env 读，本文件不硬编码、不落盘。
锚点：闸门只在多轮追问（query 含 "User says:"）触发；eval/contract 全英文首答不经过
它 → 零影响。

用法：
  python llm_gate.py --test "哪款有我这个色号" --asins B0BLTY8TJL,B00UB0JU5U
"""
import csv
import io
import json
import re
import sys
import time
from pathlib import Path

import requests

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

ROOT = Path(__file__).resolve().parent.parent
ENV = Path(__file__).resolve().parent / ".env"
API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-flash"
TIMEOUT = 25          # 对话轮次可等 25s（比规则兜底长，但对比/求助值得等）
CLASSIFY_TOKENS = 300
# 推理模型（deepseek-v4-flash）max_tokens = 推理(reasoning_content) + 正文一起算：
# 900 常被思考吃光 → content 空/截断 → 落回机器化兜底（2026-08-31 用户实测踩到）。
# 1800 = 推理 + 完整对比正文的预算，正文稳定吐出来。
GEN_TOKENS = 1800

GATE_DIRECT = 85.0    # 意图置信度 > 85% → 直出
GATE_HUMAN = 60.0     # 意图置信度 < 60% → 转人工
MAX_COMPARE = 4       # 对比最多取几款

# ---------------------------------------------------------------------------
# 确定性规则预分类（明显模式不劳 LLM：毫秒级 + 高置信，省 token/延迟）
# ---------------------------------------------------------------------------
_CHAT = re.compile(
    r"^(谢谢|多谢|感谢|好的|好的好的|好哒|嗯嗯|再见|拜拜|bye|thank|thanks|thx|"
    r"ok|okay|hi|hello|hey|👍|👌|辛苦了|麻烦你了|没问题|可以)$", re.I)
_HELP = re.compile(
    r"怎么办|不知道|不会选|不会挑|怎么选|怎么挑|怎么判断|教教|帮帮|指教|"
    r"help|don'?t know|no idea|not sure|i don'?t know|how (do|can|should) i", re.I)
_SHADE_Q = re.compile(r"色号|shade", re.I)
_SHADE_ASK = re.compile(r"哪款|哪一款|哪几款|有没有|有.*吗|which|do you have|is there", re.I)
_COMPARE = re.compile(
    r"区别|差别|有什么不同|不同在哪|哪里不同|对比|比较|相比|"
    r"compare|difference|different|differ|which.*better|how.*(compare|differ)", re.I)
# ---- 对已推荐商品的属性/风险追问（2026-09-01 用户实测 badcase）----
# 用户推荐后问「这三款会不会卡粉 / 这款适合干皮吗 / 容不容易脱妆」= 在问刚推的那几款商品，
# 必须绑定商品真实数据（标签 + 评论区差评主题）答，而不是泛化成通用护肤知识。
# 规则：有商品指代(这款/这几款/上面…) + 风险或适配词；或「会不会/容易」+ 风险词。
# 放在 _HELP 之前：复合问句「怎么选择色号呢？这三款会不会卡粉？」真实重点是商品，不能被
# help（"怎么选"）抢走 → 否则 LLM 不知道「这三款」是哪三款 → 反问「你手边哪几款」+「什么肤质」。
_QA_PROD_REF = re.compile(
    r"这三款|这款|那款|这几款|那几款|上面|刚才|它们|这几个|那两个|这两款|"
    r"这几|刚推荐|推荐的那|它|这些", re.I)
_QA_VERB = re.compile(r"会不会|会不|容易|是否|易", re.I)
_QA_RISK = re.compile(
    r"卡粉|浮粉|闷痘|脱妆|拔干|搓泥|过敏|刺激|斑驳|假面|暗沉|起皮|厚重|太干|太油|闷|"
    r"cake|cak|patchy|pill|breakout|break out|allerg|irritat|oxid|fall off|flaky", re.I)
_QA_SUIT = re.compile(
    r"适合|能用|可用|适配|友好|行吗|油皮|干皮|混油|混干|敏感|痘肌|"
    r"suit|fit|work for|good for|oily|dry|sensitive", re.I)
# 中文检测（补 CJK 规则层抽取用；agent.extract_constraints 是英文规则，不认中文）
_CJK = re.compile(r"[一-鿿]")
_AMBI_QUESTION = re.compile(
    r"为什么|为啥|怎么回事|怎么样|怎么知道|值得买|好不好|划算吗|能行吗|靠谱吗|"
    r"how come|why|is it good|worth it|this one|good buy", re.I)
_REFINE = re.compile(
    r"推荐|要|想|更|换|再|预算|肤质|妆效|遮瑕|质地|色号|控油|持妆|便宜|平价|"
    r"哑光|水光|自然|光泽|敏感|油皮|干皮|混油|混干|痘痘|中性|"
    r"recommend|budget|skin|finish|coverage|texture|shade|want|need|cheap|"
    r"matte|dewy|oily|dry|combination|sensitive|acne", re.I)

# 色号方向解析（库内粗分 自然/白皙/深色/冷调/橄榄；一白/二白/三白体系没有，诚实粗映射）
_SHADE_NATURAL = re.compile(r"黄一白|黄二白|黄三白|二白|三白|黄调|暖调|偏黄|偏自然|自然色")
_SHADE_FAIR = re.compile(r"一白|白皙|偏白|白皮|浅色|浅皮")
_SHADE_DARK = re.compile(r"深色|偏深|深皮|小麦色")

# ---- 色号诊断续答（2026-08-31 用户实测教训：前端没拼 AI 上轮回答 → 用户答了「血管/口红/金饰」
# ---- 观察却被误判成「要推荐」又推 3 款。修复不是逐个加关键词，而是对话记忆层：前端传双方对话，
# ---- 闸门看得到「AI 上一条问了什么」→ 系统性识别「用户正在回答 AI 的问题」。
# ---- ① _SHADE_DIAG 观察信号（快速兜底）；② 上一条 AI 是诊断提问（_AI_DIAG_Q）→ 本轮=续答。
_AI_DIAG_Q = re.compile(r"血管|金饰|银饰|手腕|口红|观察|自测|素颜", re.I)      # 上一条 AI 是否在问色号自测
_AI_OFFER = re.compile(r"要不要|要吗|want me to|帮你挑|挑几款|pick a few", re.I)  # 上一条 AI 是否软询问挑款
_SHADE_DIAG = re.compile(
    r"血管|手背|静脉|金饰|银饰|首饰|口红|泛红|红晕|素颜|衬肤|黄黄|"
    r"冷调|暖调|中性调", re.I)
# 诊断会话中「点头挑款」的确认（纯确认 / 软确认）
_CONFIRM_PURE = re.compile(
    r"^(好|好的|好呀|好啊|好哇|好嘞|好哒|要|要的|要呀|是的|对|可以|行|嗯|嗯嗯|"
    r"来吧|来|挑|挑挑|看看|看看吧|走|走起|ok|okay|yes|sure|go|deal)$", re.I)
_CONFIRM_SOFT = re.compile(r"好|要|可以|挑|看看|走|ok|yes|sure", re.I)
# 转折/结束信号（→ 不进诊断续答，走正常意图分类）
_PIVOT = re.compile(
    r"换|改|重新|不要|别的|其他|再|预算|肤质|妆效|遮瑕|质地|控油|持妆|"
    r"哑光|水光|便宜|平价|推荐|谢谢|感谢|再见|拜拜|"
    r"budget|finish|skin|coverage|recommend|matte|dewy|thank|bye", re.I)
# 色号结论标记（诊断生成必须以单独一行「色号结论：自然」收尾，供确认轮注入推荐器）
_DIAG_MARK = re.compile(
    r"(?:色号结论|shade conclusion)\s*[:：]\s*(自然|白皙|深色|冷调|橄榄|"
    r"natural|fair|deep|cool|olive)", re.I)
_FAM_ZH = {"natural": "自然", "fair": "白皙", "deep": "深色", "cool": "冷调", "olive": "橄榄"}


def _num(x, default=None):
    try:
        f = float(str(x).replace("$", "").replace(",", "").strip())
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def _load_api_key():
    if not ENV.exists():
        return None
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DEEPSEEK_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _load_defect_map():
    """product_defect_evidence.csv → {parent_asin: {"axes": str, "n": int}}（只读一次）。"""
    p = ROOT / "data" / "product_defect_evidence.csv"
    out = {}
    if not p.exists():
        return out
    try:
        with open(p, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                a = str(r.get("parent_asin") or "").strip()
                if a:
                    out[a] = {"axes": str(r.get("defect_axes") or "").strip(),
                              "n": int(_num(r.get("n_neg_reviews"), 0))}
    except Exception:
        pass
    return out


DEFECT_MAP = _load_defect_map()


def _chat(system, user, max_tokens=GEN_TOKENS, temperature=0.7):
    """调 DeepSeek → 文本。失败/超时/无 key → None（调用方降级，绝不让 LLM 崩掉整轮）。"""
    key = _load_api_key()
    if not key:
        return None
    body = {"model": MODEL,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": temperature, "max_tokens": max_tokens}
    try:
        r = requests.post(API_URL, headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }, json=body, timeout=TIMEOUT)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def _parse_json(text):
    """剥 markdown fence → json.loads → ast 兜底；全失败返回 None。"""
    t = str(text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I).strip()
    for cand in (t, re.sub(r",\s*([}\]])", r"\1", t)):
        try:
            obj = json.loads(cand)
            return obj if isinstance(obj, dict) else None
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# 意图分类（规则预分类 → 兜底 LLM）
# ---------------------------------------------------------------------------
_CLASSIFY_SYSTEM = (
    "你是美妆导购助手的「对话意图分类器」。判断用户这轮在说什么，只输出一个 JSON：\n"
    '{"intent": "recommend|compare|product_qa|shade_question|help|chat|shade_diagnosis|other", '
    '"confidence": 0-100整数, "reason": "一句话说明判断依据"}\n'
    "intent 含义：\n"
    "- recommend：用户在调整需求/继续要推荐（说肤质/妆效/预算/色号/换一款/再推荐等）\n"
    "- compare：要对比几款商品（区别/哪款更好）\n"
    "- product_qa：在问刚才推荐的那几款商品的具体情况（会不会卡粉/适不适合我/容不容易脱妆）\n"
    "- shade_question：在问某款有没有他的色号（哪款有我这个色号/有黄二白吗）\n"
    "- help：求助/不会选/不知道怎么判断（不知道色号怎么办/怎么选）\n"
    "- shade_diagnosis：用户在回答 AI 上一条问的色号自测问题（说了血管颜色/冷暖调/金饰银饰/"
    "口红偏好等观察）\n"
    "- chat：寒暄/感谢\n"
    "- other：以上都不是\n"
    "特别注意：如果 AI 上一条消息是在问用户观察类问题（血管/冷暖调/首饰/口红等），"
    "用户现在的回答就是 shade_diagnosis，绝不是 recommend——千万不要把「用户回答观察」"
    "误判成「要推荐商品」。\n"
    "不要任何解释，不要 markdown 代码块。"
)


def _render_dialogue(dialogue):
    """把最近对话（双方，用户+AI）渲染成简短文段，供 LLM 理解「AI 上一条问了什么」。"""
    if not dialogue:
        return None
    rows = []
    for m in dialogue[-6:]:
        role = "AI" if str(m.get("role")) == "ai" else "用户"
        rows.append(f"{role}: {str(m.get('text') or '')[:200]}")
    return "\n".join(rows)


def _classify(context, turn_text, titles, reply_lang="zh", dialogue=None, last_ai=None):
    """返回 {"intent", "confidence", "reason"}。规则预分类命中即返回；否则 LLM。"""
    t = turn_text.strip()
    if _CHAT.match(t):
        return {"intent": "chat", "confidence": 95.0, "reason": "rule:chat"}
    # 对已推荐商品的追问（先于求助）：有上轮推荐 且（商品指代+风险/适配 或 会不会+风险）→ 商品追问。
    # 修复 2026-09-01 用户 badcase：「这三款会不会卡粉」被 help("怎么选") 抢走 → LLM 不知是哪三款
    # → 反问「你手边哪几款/什么肤质」，卡粉也答成通用知识。此规则让商品追问先接住、绑定商品数据答。
    if titles and ((_QA_PROD_REF.search(t) and (_QA_RISK.search(t) or _QA_SUIT.search(t)))
                   or (_QA_VERB.search(t) and _QA_RISK.search(t))):
        return {"intent": "product_qa", "confidence": 92.0, "reason": "rule:product_qa"}
    if _HELP.search(t):
        return {"intent": "help", "confidence": 93.0, "reason": "rule:help"}
    # 观察信号兜底（上一条 AI 诊断提问时 route() 已在会话层接住；这里是跨会话安全网）
    if _SHADE_DIAG.search(t) and not _PIVOT.search(t):
        return {"intent": "shade_diagnosis", "confidence": 88.0, "reason": "rule:diag"}
    if _SHADE_Q.search(t) and _SHADE_ASK.search(t):
        return {"intent": "shade_question", "confidence": 93.0, "reason": "rule:shade_q"}
    if _COMPARE.search(t):
        return {"intent": "compare", "confidence": 93.0, "reason": "rule:compare"}
    if _AMBI_QUESTION.search(t):
        return _llm_classify(context, turn_text, titles, reply_lang, dialogue, last_ai)
    if _REFINE.search(t):
        return {"intent": "recommend", "confidence": 90.0, "reason": "rule:refine"}
    return _llm_classify(context, turn_text, titles, reply_lang, dialogue, last_ai)


def _llm_classify(context, turn_text, titles, reply_lang, dialogue=None, last_ai=None):
    dl = _render_dialogue(dialogue)
    user = (f"用户本轮消息：{turn_text}\n"
            f"对话背景（用户此前的需求）：{context or '（无）'}\n"
            f"最近对话（用户+AI，AI 的话也算）：\n{dl or '（无）'}\n"
            f"AI 上一条消息：{last_ai or '（无）'}\n"
            f"上一轮推荐的商品：{' / '.join(titles) if titles else '（无）'}\n"
            f"请判断本轮意图。")
    out = _chat(_CLASSIFY_SYSTEM, user, max_tokens=CLASSIFY_TOKENS, temperature=0)
    parsed = _parse_json(out)
    if parsed and parsed.get("intent") in ("recommend", "compare", "product_qa", "shade_question",
                                           "help", "chat", "shade_diagnosis", "other"):
        conf = _num(parsed.get("confidence"), 75.0)
        return {"intent": parsed["intent"], "confidence": max(0.0, min(100.0, conf)),
                "reason": str(parsed.get("reason") or "llm")[:80]}
    # LLM 失败/解析失败 → 默认推荐（与闸门出现前的行为一致，最安全）
    return {"intent": "recommend", "confidence": 90.0, "reason": "gate:llm_failed_默认推荐"}


# ---------------------------------------------------------------------------
# 确定性取数（事实永远来自数据层）
# ---------------------------------------------------------------------------
def _req_meta(agent, context):
    """对原始需求跑规则约束抽取，得到用户已说过的需求（供关联场景）。"""
    try:
        req, meta = agent.extract_constraints(context)
    except Exception:
        req = {"hard": set(), "soft": set(), "finish": None, "coverage": None,
               "form": None, "shade_dir": None, "implicit": [], "qtext": context,
               "vec_text": context, "budget": None, "seasonal": False}
        meta = {"control_oil": False, "stated_skin": False, "coverage_requested": False,
                "unsolvable": False, "mature": False, "newbie": False,
                "long_wear": False, "seasonal": False, "negative_axes": set(),
                "skins_stated": set()}
    # 中文需求补 CJK 规则层抽取（extract_constraints 是英文规则，不认中文）：
    # 否则中文多轮闸门（商品追问/对比/求助）拿到的 context 行是「未识别到明确需求」，
    # LLM 不知道用户肤质 → 反问「皮肤偏干偏油」（2026-09-01 用户 badcase 的次生根因）。
    try:
        cjk = getattr(agent, "_cjk_explicit", None)
        if cjk and _CJK.search(str(context or "")):
            cjk(req, meta, context)
    except Exception:
        pass
    return req, meta


def _context_line(req, meta, reply_lang="zh"):
    en = reply_lang == "en"
    parts = []
    skins = sorted(set((list(req["hard"]) or []) + (list(req["soft"]) or [])))
    if skins:
        parts.append(("肤质=" if not en else "skin=") + "+".join(skins))
    if req["finish"]:
        parts.append(("妆效=" if not en else "finish=") + str(req["finish"]))
    if req["coverage"]:
        parts.append(("遮瑕=" if not en else "coverage=") + str(req["coverage"]))
    if req["form"]:
        parts.append(("质地=" if not en else "form=") + str(req["form"]))
    if req["budget"] and req["budget"] > 0:
        parts.append(("预算≤$" if not en else "budget≤$") + f"{_num(req['budget'], 0):.0f}")
    if req["shade_dir"]:
        parts.append(("色号=" if not en else "shade=")
                     + ("白皙" if req["shade_dir"] == "fair" else "深色"))
    return ("、".join(parts) if parts
            else ("（未识别到明确需求）" if not en else "(no clear request detected)"))


def _product_facts_block(agent, asins, reply_lang="zh"):
    """每款商品的真实结构化信息（标签/口碑/色号/价格/差评主题）→ 供 LLM 对比叙述。"""
    en = reply_lang == "en"
    rows = []
    for a in (asins or [])[:MAX_COMPARE]:
        p = agent.idx.by_asin.get(a)
        if not p:
            continue
        title = str(p.get("title_zh") or p.get("title") or a)[:60]
        tags = []
        if p.get("finish_tag"):
            tags.append(("妆效=" if not en else "finish=") + str(p["finish_tag"]))
        if p.get("skin_tags"):
            tags.append(("适合=" if not en else "skin=")
                        + str(p["skin_tags"]).replace(";", "、" if not en else ","))
        if p.get("coverage_tag"):
            tags.append(("遮瑕=" if not en else "coverage=") + str(p["coverage_tag"]))
        if p.get("form_tag"):
            tags.append(("质地=" if not en else "form=") + str(p["form_tag"]))
        tag_txt = "；".join(tags) if tags else ("未标注标签" if not en else "no tags")
        avg = _num(p.get("average_rating"))
        rn = int(_num(p.get("rating_number")) or 0)
        if avg is None:
            rating = "口碑待核实" if not en else "rating pending"
        else:
            rating = f"{avg:.1f}分/{rn}条" if not en else f"{avg:.1f}★/{rn} ratings"
        price = _num(p.get("price"))
        price_txt = f"${price:.2f}" if price is not None else ("未标价" if not en else "—")
        shade = str(p.get("shade_tag") or "")
        d = DEFECT_MAP.get(a)
        defect_txt = (str(d["axes"]) if (d and d["axes"]) else
                      ("无共识差评" if not en else "no consensus complaints"))
        rows.append(
            f"【{title}】\n"
            f"  标签：{tag_txt}\n"
            f"  口碑：{rating}　色号：{shade or ('未标注' if not en else 'not listed')}　"
            f"价格：{price_txt}\n"
            f"  差评声音：{defect_txt}")
    return "\n".join(rows)


# 口语质地备注（对比兜底用，2026-08-31 用户定：朋友式口吻，不是字段名）
_FORM_NOTE_ZH = {"液体": "液体款，日常通勤都能用", "气垫": "气垫款，出门补妆超方便",
                 "乳霜": "乳霜款，质地更润", "粉状": "粉状款，上妆轻薄",
                 "棒状": "棒状款，方便快捷"}
_FORM_NOTE_EN = {"液体": "liquid, great for everyday wear",
                 "气垫": "cushion, super handy for touch-ups",
                 "乳霜": "cream, richer texture", "粉状": "powder, light application",
                 "棒状": "stick, quick and easy"}


def _compare_fallback(agent, asins, reply_lang="zh"):
    """LLM 挂了 → 确定性「朋友式」对比（口语分点式，2026-08-31 用户定）。

    绝不甩【】/标签：/差评声音：这类结构化清单——像朋友一样拆开讲：开头点共性与差异、
    每款一段（质地+适合谁+评论区声音+差评主题+价格）、结尾反问收窄。全程只用真实数据
    （评分/条数/差评主题都来自评论库），禁止编造评论原话。"""
    en = reply_lang == "en"
    ps = []
    for a in (asins or [])[:MAX_COMPARE]:
        p = agent.idx.by_asin.get(a)
        if p:
            ps.append((a, p))
    if not ps:
        return _no_recs_msg(reply_lang)
    # 开头：点共同妆效 + 一句「各有侧重」
    finishes = {str(p.get("finish_tag") or "").strip() for _, p in ps}
    finishes.discard("")
    if len(finishes) == 1:
        fin = next(iter(finishes))
        head = (f"这三款都是{fin}妆效，但质地和适合人群不太一样：" if not en
                else f"All three share a {fin} finish, but they differ in texture and who they suit:")
    else:
        head = ("这三款粉底液各有侧重，我拆开给你说：" if not en
                else "These three lean differently — let me walk you through each:")
    # 每款一段（口语分点式）
    lines = []
    max_rn = max((int(_num(p.get("rating_number")) or 0) for _, p in ps), default=0)
    for a, p in ps:
        title = str(p.get("title_zh") or p.get("title") or a)[:46]
        form = str(p.get("form_tag") or "").strip()
        skin = str(p.get("skin_tags") or "").replace(";", "、").strip()
        avg = _num(p.get("average_rating"))
        rn = int(_num(p.get("rating_number")) or 0)
        price = _num(p.get("price"))
        # 质地 + 适合（口语）
        bits = []
        form_note = (_FORM_NOTE_ZH if not en else _FORM_NOTE_EN).get(form)
        if form_note:
            bits.append(form_note)
        if skin:
            bits.append(f"{skin}友好" if not en else f"suits {skin}")
        intro = "；".join(bits) if bits else ("质地/肤质未标注" if not en else "texture/skin not tagged")
        # 评论区声音（评分+条数+样本最多最稳）
        if avg is not None:
            mouth = f"评论区 {avg:.1f}分/{rn}条" if not en else f"{avg:.1f}★/{rn} ratings"
            if rn == max_rn and rn >= 30:
                mouth += ("，样本最多最稳" if not en else ", largest sample, most stable")
        else:
            mouth = ("口碑待核实" if not en else "rating pending")
        # 差评主题（评论库真实信号）
        d = DEFECT_MAP.get(a)
        complaint = ("，但有被反复吐槽：" + str(d["axes"]) if (d and d.get("axes"))
                     else ("，没被集中吐槽的毛病" if not en else ", no recurring complaints"))
        # 价格
        price_txt = f"，${price:.2f}" if price is not None else ""
        lines.append(f"🌟 {title}：{intro}{price_txt}。{mouth}{complaint}。")
    body = "\n".join(lines)
    # 结尾：诚实总结 + 反问收窄
    if any((DEFECT_MAP.get(a) or {}).get("axes") for a, _ in ps):
        tail = ("有款被评论区反复吐槽过，选之前先避雷。" if not en else
                "One or more has recurring complaints — worth avoiding before you pick.")
    else:
        tail = ("三款评论区都没被集中吐槽，口碑都还行。" if not en else
                "None has recurring complaints — all look decent on reviews.")
    ask = ("你更看重质地、口碑还是价格？我帮你收窄。" if not en else
           "Do you care more about texture, reviews, or price? I can narrow it down.")
    return f"{head}\n{body}\n{tail}\n{ask}"


def _parse_shade(text):
    """从对话里解析用户色号方向 → {"label": 自然|白皙|深色, "raw": 原词} 或 None。"""
    m = _SHADE_NATURAL.search(text)
    if m:
        return {"label": "自然", "raw": m.group(0)}
    m = _SHADE_FAIR.search(text)
    if m:
        return {"label": "白皙", "raw": m.group(0)}
    m = _SHADE_DARK.search(text)
    if m:
        return {"label": "深色", "raw": m.group(0)}
    return None


def _shade_facts_block(agent, asins, shade, reply_lang="zh"):
    """色号匹配判断（确定性，逐款查库内色号标签）+ 诚实说明。"""
    en = reply_lang == "en"
    label = shade["label"]
    rows = []
    for a in (asins or [])[:MAX_COMPARE]:
        p = agent.idx.by_asin.get(a)
        if not p:
            continue
        title = str(p.get("title_zh") or p.get("title") or a)[:60]
        st = str(p.get("shade_tag") or "")
        if st and label in st:
            verdict = (f"匹配（色号标签含「{label}」）" if not en
                       else f"match (shade tag has {label})")
        elif st and "深色" in st:
            verdict = "偏深，不匹配" if not en else "runs deep, no match"
        elif st and "白皙" in st:
            verdict = "偏白，不匹配" if not en else "runs fair, no match"
        elif st:
            verdict = (f"色号为「{st}」，不完全匹配" if not en
                       else f"shade tag '{st}', not a direct match")
        else:
            verdict = "未标注色号" if not en else "shade not listed"
        rows.append(f"【{title}】色号标签：{st or ('未标注' if not en else 'not listed')} → {verdict}")
    honesty = (
        "库内色号只粗分「自然/白皙/深色/冷调/橄榄」，没有一白二白三白这种细分；"
        f"您说的「{shade['raw']}」最接近「{label}」色系（中调）。" if not en else
        f"The catalog only labels shades coarsely (natural/fair/deep/cool/olive), not the "
        f"1/2/3-white system. Your '{shade['raw']}' maps closest to '{label}'.")
    return "\n".join(rows) + "\n" + honesty


# ---------------------------------------------------------------------------
# LLM 应答（事实先行 + 润色）
# ---------------------------------------------------------------------------
def _generate(prompt_zh, prompt_en, user, reply_lang):
    system = prompt_zh if reply_lang != "en" else prompt_en
    return _chat(system, user, max_tokens=GEN_TOKENS, temperature=0.7)


def _generate_compare(facts, context, reply_lang):
    zh = ("你是美妆导购，很会聊天，说话像朋友。用户要对比几款粉底液。\n"
          "以下是这几款的【真实信息】（评分/条数/标签/色号/价格/差评主题都来自商品评论区，必须属实）：\n"
          f"{facts}\n用户原始需求：{context}\n"
          "请用**口语分点式**像朋友一样介绍（不要用表格）：\n"
          "开头一句点出共同点和最大的不同（比如都是水光妆效，但质地/适合人群不一样）；\n"
          "然后每款单独一段（🌟 开头）：先说它是什么质地、适合谁（肤质/场景），"
          "再说评论区的声音——评分多少分、多少条评价、有没有被反复吐槽的毛病（差评主题）；\n"
          "哪款被评论区集中吐槽过（如卡粉/闷痘）一定要点出来；价格对比一句带过（哪款最贵/最便宜）。\n"
          "最后反问一句用户更看重哪个维度（质地/口碑/价格），方便继续收窄。\n"
          "只说上述真实数据，禁止编造评分/条数/色号/评论原话；语气像朋友聊天，不要像数据报表。")
    en = ("You're a friendly beauty-shopping guide. The user wants to compare foundations.\n"
          f"Below is their REAL info (rating/review count/tags/shade/price/complaint themes — "
          f"all from the catalog's review data, must be accurate):\n{facts}\nOriginal request: {context}\n"
          "Compare them in plain, warm, spoken style (NO tables):\n"
          "Open with one line on what they share and the biggest difference (e.g. all a dewy finish, "
          "but different texture / who they suit).\n"
          "Then one short paragraph per product (start each with 🌟): say what texture it is and who "
          "it suits, then the voice of reviews — rating, how many reviews, and any recurring complaint "
          "theme; call out any product reviewers consistently complain about (caking, breakouts...).\n"
          "Mention price in one line (which is most/least expensive).\n"
          "End by asking which dimension matters most (texture/reviews/price) to narrow down.\n"
          "Use ONLY the real data above — never invent ratings, counts, shades, or quotes. "
          "Sound like a friend chatting, not a data sheet.")
    return _generate(zh, en, f"这几款分别是什么？请对比并给建议。", reply_lang)


def _generate_shade(facts, context, reply_lang, shade, turn_text):
    zh = ("你是美妆导购，很会聊天。用户想知道这几款有没有他的色号。\n"
          "以下是每款真实的色号标签和匹配判断（来自商品库）：\n"
          f"{facts}\n用户原始需求：{context}\n用户本轮说：{turn_text}\n"
          "请诚实说明：哪款接近、哪款不接近、为什么；说明库内色号是粗分体系、没有一白二白三白；"
          "给出下一步建议（比如要不要按最接近的色系再找几款）。语气友好，用列表+emoji。"
          "只用上述真实数据，禁止编造色号标签。")
    en = ("You're a friendly beauty-shopping guide. The user asks whether these products come in "
          f"their shade.\nReal shade tags and match verdicts (from the catalog):\n{facts}\n"
          f"Original request: {context}\nThis turn: {turn_text}\n"
          "Answer honestly: which is close, which isn't and why; explain the catalog uses a coarse "
          "shade system (no 1/2/3-white grades); suggest a next step (e.g. re-search by the closest "
          "tone). Friendly, lists + emoji. Use ONLY the data above — never invent shade tags.")
    return _generate(zh, en, f"哪款有我的色号？请诚实回答。", reply_lang)


def _generate_product_qa(facts, context, turn_text, reply_lang):
    """对已推荐商品的属性/风险追问（这三款会不会卡粉/适合干皮吗）→ 绑定真实商品数据答。

    2026-09-01 用户 badcase：原 help 路径不接收推荐商品 → LLM 不知道「这三款」是哪三款，
    反问「你手边哪几款/什么肤质」，卡粉也泛化成通用护肤知识。本函数把商品真实信息
    （标签/评分/条数/价格/色号/差评主题）注入 prompt，并硬约束「已知的不要再反问」。
    事实永远来自数据层，LLM 只改写语气。"""
    zh = ("你是美妆导购，很会聊天，说话像朋友。用户在问「刚才推荐的那几款商品」的具体情况——"
          "比如会不会卡粉、浮粉、闷痘、脱妆，适不适合他的肤质。\n"
          "以下是这几款的【真实信息】（标签/评分/条数/价格/色号/差评主题都来自商品数据，必须属实）：\n"
          f"{facts}\n用户已知需求：{context}\n用户本轮问：{turn_text}\n"
          "回答结构（**精简，别啰嗦**，2026-09-01 用户定）：\n"
          "1. 开头（只有用户问到了选色号/肤色才写）：肤色判别最多 2-3 行（看血管/首饰一两句带过，别铺开）。\n"
          "2. 逐款（🌟 每款开头，**每款 1-2 行**）：第一句质地+妆效+适合谁；第二句评论区声音/风险"
          "（有没有被反复吐槽卡粉等；没有就诚实说「评论区没提到」）+ 一句关键提醒。\n"
          "3. 总结 1-2 行：直接给结论（哪款最稳/哪款小心什么/选哪款）+ 反问一句帮他收窄。\n"
          "关键：用户问的这几款就是刚才推荐的——**绝对不要再问「你手边是哪几款」**；"
          "用户肤质等信息在上面的「用户已知需求」里，**已知的不要再反问**。\n"
          "只用真实数据，禁止编造评分/条数/色号/评论原话/差评主题；口语化、像朋友聊天、"
          "不要堆满 emoji、不要像数据报表。")
    en = ("You're a friendly beauty-shopping guide. The user is asking about the products you just "
          f"recommended — whether they'll cake, go patchy, break out, or suit their skin.\n"
          "Below is their REAL info (tags/rating/count/price/shade/complaint themes — from the "
          f"catalog, must be accurate):\n{facts}\nKnown needs: {context}\nThis turn: {turn_text}\n"
          "Structure (BE CONCISE, don't ramble):\n"
          "1. Opening (only if the user asked about shade/undertone): shade check in at most 2-3 "
          "lines (wrist veins / jewelry, one or two sentences, no deep dive).\n"
          "2. Per product (start each with 🌟, **1-2 lines each**): first line = texture + finish + "
          "who it suits; second line = the review voice / risk (any recurring complaint like "
          "caking — say it plainly; if none, honestly say the reviews don't mention it) + one "
          "key caveat.\n"
          "3. Close in 1-2 lines: give the verdict (which is safest / what to watch out for / which "
          "to pick) + one question to narrow down.\n"
          "Key: these are exactly the products you recommended — NEVER ask 'which products do you "
          "have'; their skin info is in 'Known needs' above — don't re-ask what's known.\n"
          "Use ONLY the real data — never invent ratings, counts, shades, quotes, or complaint "
          "themes. Sound like a friend, not a data sheet; don't overuse emoji.")
    return _generate(zh, en, f"请回答用户对这几款商品的追问。", reply_lang)


def _product_qa_fallback(agent, asins, context, reply_lang="zh"):
    """LLM 挂了 → 确定性朋友式回答（绑定真实商品数据 + 评论区声音），绝不崩、绝不反问已知。"""
    en = reply_lang == "en"
    ps = []
    for a in (asins or [])[:MAX_COMPARE]:
        p = agent.idx.by_asin.get(a)
        if p:
            ps.append((a, p))
    if not ps:
        return _no_recs_msg(reply_lang)
    lines = []
    for a, p in ps:
        title = str(p.get("title_zh") or p.get("title") or a)[:50]
        form = str(p.get("form_tag") or "")
        finish = str(p.get("finish_tag") or "")
        skins = str(p.get("skin_tags") or "").replace(";", "、" if not en else ",")
        desc = "、".join(x for x in [
            (("质地" if not en else "texture") + f" {form}") if form else "",
            (("妆效" if not en else "finish") + f" {finish}") if finish else "",
            (("适合" if not en else "suits") + f" {skins}") if skins else "",
        ] if x)
        d = DEFECT_MAP.get(a)
        axes = str(d["axes"]) if (d and d["axes"]) else ""
        if axes:
            risk = (("评论区有被反复吐槽：" + axes) if not en else
                    ("recurring review complaints: " + axes))
        else:
            risk = (("评论区没被集中吐槽过这类问题" if not en else
                     "no recurring complaints in the reviews"))
        lines.append(f"🌟 {title}：{desc or ('未标注标签' if not en else 'no tags')}。{risk}。")
    # 结尾：结合已知肤质给一句建议 + 反问收窄（已知的绝不反问）
    m = re.search(r"肤质=([^、]+)", context or "")
    if m:
        skins_txt = m.group(1)
        tail = ((f"结合您肤质（{skins_txt}）的话，怕卡粉就优先挑质地润一点的、"
                 f"上妆前做好保湿打底；您最担心哪一点，我再帮您把关。") if not en else
                (f"Given your skin ({skins_txt}), if caking is a concern, lean toward the "
                 f"moisturizing ones and hydrate well before applying. Which concern matters "
                 f"most — I'll take it from there."))
    else:
        tail = (("具体建议可以告诉我您最担心哪一点（卡粉、出油还是脱妆），我帮您重点看。")
                if not en else
                ("Tell me your top concern (caking, shine, or fading) and I'll dig in."))
    return (("好嘞，咱们就看刚推荐的这几款：\n" if not en else
             "Sure — here's the read on the picks:\n") + "\n".join(lines) + "\n" + tail)


def _generate_help(context, turn_text, reply_lang, titles=None):
    zh = ("你是美妆导购，很会聊天，说话像朋友。用户在求助——比如不知道怎么判断自己的色号、不知道怎么选。\n"
          f"用户已知需求：{context}\n用户本轮说：{turn_text}\n"
          f"上一轮推荐的商品：{' / '.join(titles) if titles else '（无）'}\n"
          "请像朋友一样给出可操作的判断方法（看手腕血管判断冷暖调、回想穿衣衬色等），"
          "或提一两个引导性问题帮用户定位。不要急着推荐商品。\n"
          "注意：用户已知需求（肤质/妆效等）在上面的「用户已知需求」里，**已知的绝对不要再反问**；"
          "用户提到的商品如果就是上一轮推荐的，直接按已知的聊，不要问「你手边是哪几款」。"
          "语气友好亲近、口语化，不要堆满 emoji、不要像教科书。")
    en = ("You're a friendly beauty-shopping guide. The user is asking for help — e.g. how to "
          f"figure out their shade, or how to choose.\nKnown needs: {context}\nThis turn: {turn_text}\n"
          f"Last recommended products: {' / '.join(titles) if titles else '(none)'}\n"
          "Give actionable advice like a friend (wrist-vein undertone test, what colors flatter them), "
          "or ask 1-2 guiding questions. Don't rush to recommend products.\n"
          "Note: their known needs are in 'Known needs' above — never re-ask what's already known; "
          "if they mention products you recommended, talk about those directly, don't ask which ones. "
          "Warm and conversational, not textbook-like, not emoji-heavy.")
    return _generate(zh, en, f"请帮帮我。", reply_lang)


def _generate_chat(turn_text, reply_lang):
    zh = ("你是美妆导购。简短友好地回应寒暄/感谢，并自然地把话题带回选购（如果合适）。用中文回复。")
    en = ("You're a beauty-shopping guide. Reply briefly and warmly to the greeting/thanks, and "
          "naturally steer back to shopping if fitting. Reply in English.")
    return _generate(zh, en, turn_text, reply_lang)


def _generate_other(context, titles, reply_lang):
    zh = ("你是美妆导购。用户这句话不太像直接需求。\n"
          f"对话背景：{context or '（无）'}\n上一轮推荐：{' / '.join(titles) if titles else '（无）'}\n"
          "尽量友善回应：能猜到意图就试着帮（可以基于对话背景聊），猜不到就礼貌地请对方说清楚一点。"
          "不要编造商品数据。")
    en = ("You're a beauty-shopping guide. This message isn't a clear request.\n"
          f"Context: {context or '(none)'}\nLast picks: {' / '.join(titles) if titles else '(none)'}\n"
          "Respond warmly: if you can guess the intent, help; otherwise politely ask them to clarify. "
          "Never invent product data.")
    return _generate(zh, en, "请回应这句话。", reply_lang)


def _parse_family(text):
    """从诊断生成里取机器标记行「色号结论：自然」→ 家族（供确认轮注入推荐器）。"""
    m = _DIAG_MARK.search(str(text or ""))
    if not m:
        return None
    v = m.group(1).strip().lower()
    return _FAM_ZH.get(v, v)


def _generate_diagnosis(context, dialogue, turn_text, reply_lang):
    """色号诊断续答：基于用户自述观察给色号结论 + 软询问挑款（不主动推商品）。"""
    zh = ("你是美妆导购，很会聊天。用户刚才向你求助选色号，你给了自测方法（看血管/穿衣/首饰/口红），"
          "现在用户把自己的观察告诉了你——这些观察是用户自述，可信。\n"
          f"最近对话：{_render_dialogue(dialogue) or '（无）'}\n用户观察：{turn_text}\n"
          "请像朋友一样分析这些观察，推断肤色基调：\n"
          "- 暖调（偏黄底，最接近「自然」色）——血管偏绿、戴金饰显贵气、橘色系口红更显白；\n"
          "- 冷调（偏粉/蓝底，最接近「白皙」或「冷调」）——血管偏蓝紫、穿蓝紫粉更衬肤色、银饰更显干净；\n"
          "- 中性调（偏自然，大部分色号都行）。\n"
          "把分析讲成人话（比如「血管中性+金饰显贵气+橘色系显白 → 偏暖调」），给出色号方向结论，"
          "最后软问一句「要不要我帮你挑几款xxx色号的粉底液？」——不要主动推荐具体商品。\n"
          "最后必须**另起一行**写一行「色号结论：XXX」，XXX 只能是 自然/白皙/深色/冷调/橄榄 之一"
          "（暖调/中性调一律写「自然」，冷调写「冷调」）。语气友好，用列表+emoji，中文回复。\n"
          "严禁提商品库/库内数据/色号细分体系这类内部词，不说用户听不懂的话——结论按观察直说就行。")
    en = ("You're a friendly beauty-shopping guide. The user just asked how to find their shade; "
          "you gave self-check methods (wrist veins / clothes / jewelry / lipstick) and now they "
          "tell you their observations — treat these as trustworthy self-reports.\n"
          f"Recent dialogue: {_render_dialogue(dialogue) or '(none)'}\nUser observations: {turn_text}\n"
          "Analyze like a friend and infer the undertone:\n"
          "- warm (yellow-based, closest to 'natural') — greenish veins, gold flatters, orange "
          "lipstick looks brighter;\n"
          "- cool (pink/blue-based, closest to 'fair' or 'cool') — blue/purple veins, cool colors "
          "flatter, silver looks cleaner;\n"
          "- neutral (closest to 'natural', most shades work).\n"
          "Explain in plain warm language (e.g. 'neutral veins + gold flatters + orange lipstick → "
          "warm-leaning'), state the shade conclusion, then softly ask 'want me to pick a few "
          "foundations in your shade?' — do NOT push specific products yet.\n"
          "End with a dedicated line: 'Shade conclusion: XXX' where XXX is one of natural/fair/"
          "deep/cool/olive (warm or neutral → natural; cool → cool). Lists + emoji, friendly, in English. "
          "Never mention the catalog / internal shade taxonomy — speak plainly, just the conclusion.")
    return _generate(zh, en, f"我的观察是：{turn_text}", reply_lang)


def _diagnosis_fallback(turn_text, reply_lang):
    """LLM 挂了 → 确定性结论（粗规则映射）+ 软询问（2026-08-31 用户定稿：
    不提商品库/库内色号体系，不说用户听不懂的话，只给结论 + 一句软询问）。"""
    t = str(turn_text or "")
    en = reply_lang == "en"
    if re.search(r"冷调|蓝|紫|粉调|银饰", t) and not re.search(r"暖调|金饰|橘", t):
        family, concl_zh, concl_en = "冷调", "偏冷调（蓝底）", "cool-toned (blue-based)"
    elif re.search(r"深色|小麦|偏深", t):
        family, concl_zh, concl_en = "深色", "偏深色", "deep-toned"
    else:
        family, concl_zh, concl_en = "自然", "暖调/中性调（偏自然）", "warm/neutral (natural-leaning)"
    if en:
        return (f"Based on what you told me, your skin tone is most likely {concl_en}.\n"
                f"Want me to pick a few '{family}' foundations for you?"), family
    return (f"按您说的，您的肤色大概率是{concl_zh}。"
            f"要不要我帮你挑几款{family}色号的粉底液？"), family


# ---------------------------------------------------------------------------
# 兜底话术（LLM 失败时给真实事实 / 友好降级）
# ---------------------------------------------------------------------------
def _handoff_msg(reply_lang):
    return ("这个问题我拿不准（意图置信度偏低），已为您转接人工客服，稍后会有专人回复您。"
            if reply_lang != "en" else
            "I'm not confident about this one — I've handed it to a human agent "
            "who'll get back to you shortly.")


def _no_recs_msg(reply_lang):
    return ("您还没有可对比/查看的商品呢——先让我推荐几款，再问区别或色号都行。"
            if reply_lang != "en" else
            "There aren't any products to look at yet — let me recommend a few first, "
            "then we can compare or check shades.")


def _help_fallback(reply_lang):
    return ("别急，判断色号有两个土办法：① 看手腕内侧血管——偏绿偏暖是黄调，偏蓝紫是粉调；"
            "② 回想穿橘色衬肤色、还是穿粉色衬肤色。您可以先告诉我大概偏白还是偏自然，我帮您一起选。"
            if reply_lang != "en" else
            "No worries — two quick ways to find your shade: ① look at your wrist veins — "
            "greenish means warm/yellow, blue/purple means cool/pink; ② think about whether orange "
            "or pink clothing flatters you more. Tell me roughly fair or natural and we'll pick together.")


def _chat_fallback(reply_lang):
    return ("不客气！想好了随时告诉我您想要什么妆效/肤质/预算，我再帮您挑。"
            if reply_lang != "en" else
            "You're welcome! Anytime — tell me your finish, skin type, or budget and I'll find more.")


def _other_fallback(reply_lang):
    return ("没太明白您的意思——是想换一个方向，还是在问刚才那几款？您可以多说一句，我马上接着帮您。"
            if reply_lang != "en" else
            "I didn't quite catch that — did you want a different direction, or a question about the "
            "last picks? Say a bit more and I'll jump right in.")


# ---------------------------------------------------------------------------
# 主入口：多轮追问 → 意图闸门
# ---------------------------------------------------------------------------
def route(query, agent, last_asins=None, reply_lang="zh", convo=None, diag_family=None):
    """多轮追问 → 意图闸门。返回 gate dict（前端渲染）或 None（=走推荐器）。

    query       = 完整上下文 query（含 "User says: 本轮"）
    convo       = 最近对话（用户+AI 双方，前端每轮传）——闸门看得到 AI 上一条说了什么，
                  这就是「联系上下文」的系统化落地（2026-08-31）：用户回答 AI 的提问 →
                  识别为续答，不再逐个加关键词。
    diag_family = 上一轮色号诊断给的色号家族（自然/白皙/...），用户点头挑款时注入推荐器。
    """
    parts = str(query or "").rsplit("User says:", 1)
    context = parts[0].strip()
    turn_text = parts[1].strip() if len(parts) == 2 else str(query or "").strip()
    if not turn_text:
        return None
    last_asins = [str(a) for a in (last_asins or []) if a]
    idx = agent.idx
    titles = [str(idx.by_asin.get(a, {}).get("title_zh")
                 or idx.by_asin.get(a, {}).get("title") or a)[:40] for a in last_asins]

    # ---- 对话记忆层：取上一条 AI 消息（前端传双方 → 系统化识别「回答 AI 的提问」）----
    dialogue = [m for m in (convo or []) if isinstance(m, dict) and m.get("role")]
    last_ai = ""
    for m in reversed(dialogue):
        if m.get("role") == "ai" and m.get("text"):
            last_ai = str(m["text"])
            break

    # 上一条 AI 是色号自测提问/诊断结论：
    #   · 若是软询问挑款 + 用户点头 → confirm_recommend（带诊断色号走商品库）
    #   · 否则用户这轮仍在回答观察 → shade_diagnosis 续答
    #   · 用户转向（换肤质/妆效/预算…）→ 正常分类
    ai_asked_diag = bool(last_ai and _AI_DIAG_Q.search(last_ai))
    # 软询问挑款：不要求 last_ai 再含观察词——诊断结论文案（LLM/兜底）不一定重复「血管/金饰」，
    # 只要有「要不要…挑几款」+ 会话已有色号家族 + 用户纯点头 → 就该进 confirm_recommend。
    # 否则「好」会被当 recommend 又推一次商品（2026-08-31 用户实测教训的同类复发）。
    ai_offered = bool(last_ai and _AI_OFFER.search(last_ai) and diag_family)
    if ai_offered and (_CONFIRM_PURE.match(turn_text)
                       or (_CONFIRM_SOFT.search(turn_text) and not _PIVOT.search(turn_text))):
        return {"kind": "confirm_recommend", "route": "auto", "confidence": 95,
                "reason": "rule:confirm", "shade_family": diag_family, "text": ""}
    if ai_asked_diag:
        if _PIVOT.search(turn_text):
            cls = _classify(context, turn_text, titles, reply_lang, dialogue, last_ai)
        else:
            cls = {"intent": "shade_diagnosis", "confidence": 93.0, "reason": "rule:diag_session"}
    else:
        cls = _classify(context, turn_text, titles, reply_lang, dialogue, last_ai)
    intent, conf, reason = cls["intent"], cls["confidence"], cls["reason"]

    # 推荐/调整需求 → 不进闸门应答，交给推荐器（web_server 继续走 agent.run）
    if intent == "recommend":
        return None

    route_tag = ("auto" if conf > GATE_DIRECT
                 else ("review" if conf >= GATE_HUMAN else "human"))

    # <60%：不生成，直接转人工
    if route_tag == "human":
        return {"kind": intent, "route": "human", "confidence": int(conf),
                "text": _handoff_msg(reply_lang), "reason": reason}

    req, meta = _req_meta(agent, context)
    ctx_line = _context_line(req, meta, reply_lang)
    text = None

    if intent == "compare":
        if len(last_asins) < 2:
            text = _no_recs_msg(reply_lang)
        else:
            facts = _product_facts_block(agent, last_asins, reply_lang)
            text = _generate_compare(facts, ctx_line, reply_lang)
            if not text:
                # LLM 挂 → 朋友式兜底（2026-08-31 用户：绝不甩结构化 facts 清单，兜底也讲人话）
                text = _compare_fallback(agent, last_asins, reply_lang)

    elif intent == "product_qa":
        # 对已推荐商品的属性/风险追问（这三款会不会卡粉/适合干皮吗）→ 绑定商品真实数据答。
        # 2026-09-01 用户 badcase 修复：原来这类问句被 help 接走，LLM 不知是哪三款 → 反问。
        if not last_asins:
            text = _no_recs_msg(reply_lang)
        else:
            facts = _product_facts_block(agent, last_asins, reply_lang)
            text = _generate_product_qa(facts, ctx_line, turn_text, reply_lang)
            if not text:
                # LLM 挂 → 确定性朋友式兜底（绑定真实商品 + 评论区声音，不崩、不反问已知）
                text = _product_qa_fallback(agent, last_asins, ctx_line, reply_lang)

    elif intent == "shade_question":
        if not last_asins:
            text = _no_recs_msg(reply_lang)
        else:
            shade = _parse_shade(context + "\n" + turn_text)
            if shade is None:
                facts = ("（未从对话中识别到用户色号方向——请先引导：偏白、偏自然、还是偏深？）"
                         if reply_lang != "en" else
                         "(No shade direction detected — guide the user: fair, natural, or deep?)")
            else:
                facts = _shade_facts_block(agent, last_asins, shade, reply_lang)
            text = _generate_shade(facts, ctx_line, reply_lang, shade or {}, turn_text)
            if not text:
                text = facts

    elif intent == "shade_diagnosis":
        text = _generate_diagnosis(context, dialogue, turn_text, reply_lang)  # LLM 成功 → 文本字符串
        family = _parse_family(text)   # 从「色号结论：X」标记行取家族（无 → None）
        if not text or not family:     # LLM 挂了 / 没给结论行 → 确定性兜底（不得崩溃）
            text, family = _diagnosis_fallback(turn_text, reply_lang)
        # 剥掉机器标记行（色号结论：X）——那是给确认轮用的，不给用户看
        text = re.sub(r"\n?\s*(?:色号结论|shade conclusion)[^\n]*$", "", text, flags=re.I).strip()

    elif intent == "help":
        text = _generate_help(ctx_line, turn_text, reply_lang, titles) or _help_fallback(reply_lang)

    elif intent == "chat":
        text = _generate_chat(turn_text, reply_lang) or _chat_fallback(reply_lang)

    else:  # other
        text = _generate_other(context, titles, reply_lang) or _other_fallback(reply_lang)

    resp = {"kind": intent, "route": route_tag, "confidence": int(conf),
            "text": text, "reason": reason}
    if intent == "shade_diagnosis":
        resp["shade_family"] = family
    return resp


# ---------------------------------------------------------------------------
# --test 单链路调试
# ---------------------------------------------------------------------------
def _main():
    ap = __import__("argparse").ArgumentParser(description="LLM 对话意图闸门 · 单链路调试")
    ap.add_argument("--test", required=True, help="本轮用户消息")
    ap.add_argument("--ctx", default="肤质是混油、混干，预算25美元以内，想要水光妆效，帮我推荐合适的粉底液",
                    help="对话背景（原始需求）")
    ap.add_argument("--asins", default="", help="上一轮推荐 asin（逗号分隔）")
    ap.add_argument("--lang", default="zh", help="zh|en")
    args = ap.parse_args()

    from agent import GuideAgent
    agent = GuideAgent(intent_mode="rule", reply_lang=args.lang)
    asins = [a.strip() for a in args.asins.split(",") if a.strip()]
    q = (args.ctx + " User says: " + args.test) if args.ctx else args.test
    g = route(q, agent, last_asins=asins, reply_lang=args.lang)
    print(f"本轮：{args.test}")
    print(f"背景：{args.ctx}")
    if g is None:
        print("→ 意图=recommend，走推荐器（不进闸门）")
        return
    print(f"意图：{g['kind']}  置信度：{g['confidence']}  路线：{g['route']}  依据：{g['reason']}")
    print("-" * 60)
    print(g["text"])


if __name__ == "__main__":
    _main()
