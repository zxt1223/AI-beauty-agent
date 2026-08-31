# -*- coding: utf-8 -*-
"""
llm_intent.py — 模糊意图兜底 → LLM（规则盲区 A/B 实验的 LLM 侧）
=================================================================
设计原则：
  规则能覆盖的绝不上模型；**规则完全盲区**（无任何可检索信号 + query 含语境线索词）
  才调 LLM。触发边界先紧后松：q8/q15 误触发教训证明「只查 implicit 空」太宽，
  必须整层收紧——规则已有任一可检索信号就绝不上模型。
  LLM 输出强制结构化 JSON {意图, 约束, 证据}，但**信任信号不用 LLM 自报置信度**——
  用「检索兑现率」：意图必须能映射到引擎可检索的规范轴、库内商品数达标（≥20）、
  且按该意图检索 top-8 中真兑现的占比 ≥0.5，才采信；
  任一不过 → 拒绝并**降级回规则**（绝不让 LLM 崩掉整轮，超时/断连/无 key 一律降级）。

安全：DEEPSEEK_API_KEY 只存 scripts/.env，本文件绝不硬编码 key、不落盘到文档。

用法：
  python llm_intent.py --test "Honeymoon by the ocean, need a foundation that won't run"
    → 打印 should_fallback / fired / verified（含证据）/ degraded + 检索兑现率明细
"""
import ast
import hashlib
import io
import json
import re
import sys
import time
from pathlib import Path

import requests

# 幂等 UTF-8 包装（与 agent/intent_reasoning 共用标记，避免重复包装 buffer 被关闭）
if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval_engine import IMPLICIT_RULES  # noqa: E402  (5 条可检索隐式规则)

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")
ENV = Path(__file__).resolve().parent / ".env"
CACHE = ROOT / "data" / "llm_cache.json"
TRANSLATE_CACHE = ROOT / "data" / "translate_cache.json"  # 语言桥缓存（多语种→英文）
API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-flash"
TIMEOUT = 15          # 超时 → 降级（产品兜底：绝不让模型阻塞整轮）
TIMEOUT_CJK = 25      # 中文 prompt 输出字段多（约束/负约束/预算/证据），实测单轮 6.7s，
                      # 高峰/推理慢时 15s 会误超时 → 中文路径放宽到 25s（英文实验口径不动）
MAX_TOKENS = 1200     # deepseek-v4-flash 是推理模型：reasoning_content 会吃掉大量配额，
                      # max_tokens 太小 content 会被截空 → 误降级（实测 686 才够，1200 留余量）
CATALOG_MIN = 20      # 库内能兑现该意图的商品下限
FULFILL_MIN = 0.5     # 检索兑现率下限（top-8 中带标签占比）

# 引擎可兑现的 5 个隐式意图轴（对齐 retrieval_engine.IMPLICIT_RULES 的名字）。
# 熟龄肌 / 轻薄质地 等规则能推的轴**不在其中** → LLM 识别出也会被兑现率门拒绝 → 降级。
VERIFIABLE = {"防晒", "防水持妆", "油皮控油", "哑光妆效", "干皮保湿"}

# LLM 意图（含同义词/中英文）→ 规范轴；映射不到 VERIFIABLE → 丢弃
INTENT_MAP = [
    (re.compile(r"防晒|spf|sun ?protect|broad spectrum", re.I), "防晒"),
    (re.compile(r"防水|water ?proof|water ?resist|防汗|sweat ?proof", re.I), "防水持妆"),
    (re.compile(r"油皮|控油|oil ?control|greasy|出油|slick|shiny|shine", re.I), "油皮控油"),
    (re.compile(r"哑光|matte", re.I), "哑光妆效"),
    (re.compile(r"干皮|保湿|补水|hydrat|moistur|parched|tight|dry skin", re.I), "干皮保湿"),
]

# 语境线索词：query 闻起来像有隐藏意图（水/户外/出油/紧绷/卡粉…）才值得花一次 LLM 调用。
# 词干前缀匹配（creas 命中 crease/creases、settl 命中 settle/settles…），避免复数/时态漏触发。
# 规则已命中的（req["implicit"] 非空）不会走到这里；无线索的裸 query 不调（保持锚点稳定）。
CUE = re.compile(
    r"\b(water|ocean|sea|splash|rain|humid|sweat|beach|pool|swim|sun|outdoor|summer|"
    r"vacation|honeymoon|cruise|trip|travel|island|resort|parched|tight|crack|flak|"
    r"creas|settl|cake|weightless|slick|greasy|shin|matte|glow|dewy|oily|oil)", re.I)

# 中文触发：规则层（extract_constraints）只认英文关键词，中文 query 一律视为盲区 →
# should_fallback 直接命中（不用等 CUE 英文线索词），交 LLM 兜底。eval 集 ids 1-41
# 全英文 → 加这个条件对锚点 / hidden A/B 零影响。
CJK = re.compile(r"[一-鿿]")

# 中文场景线索：显式约束已由 agent._cjk_explicit 从原文抽到，规则能答就不上模型
# （省 2-12s LLM 空转）；但含场景/隐式意图词（海边/婚礼/换季/出汗/泛红…）→ 仍有规则
# 看不见的意图（防晒/防水/干皮保湿…），值得上模型补。裸问（无约束无场景）也不上模型
# → 直接 ask_all（中文快路径）。eval 全英文 → 零影响。
CJK_SCENE = re.compile(
    r"海边|海滩|游泳|玩水|度假|婚礼|蜜月|旅游|旅行|出差|户外|登山|夜店|舞台|"
    r"换季|季节|"
    r"暴晒|日晒|晒黑|晒伤|晒太阳|出汗|"
    r"泛红|发痒|起皮|脱皮|紧绷|暗沉", re.I)

SYSTEM_PROMPT = (
    "你是美妆导购的意图识别器。用户用症状/场景描述需求，你要反推「隐藏意图」"
    "（用户没说、但明显想要的产品属性，比如去海边隐含防晒+防水、出油成灾隐含控油）。\n"
    "只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块：\n"
    '{"意图": ["规范意图名"], '
    '"约束": {"肤质": "油皮/干皮/混合/敏感 或 null", '
    '"妆效": "哑光/水光/自然 或 null", '
    '"遮瑕": "高/中/轻 或 null", '
    '"质地": "液体/粉状/乳霜/气垫/棒状 或 null"}, '
    '"证据": "用一句话说明你从哪句推断（引用原文关键词）"}\n'
    "意图只能是以下规范名之一（没有匹配就写识别到的最接近的，宁少勿多）：\n"
    "防晒/SPF、防水持妆、油皮控油、哑光妆效、干皮保湿、熟龄肌、轻薄质地"
)

# 中文专用 prompt：规则层不覆盖中文，除隐式意图外还要直接抽取显式约束
# （肤质/妆效/遮瑕/质地/色号/预算）与负约束（避雷轴），agent._merge_cjk_constraints 消费。
# 独立于 SYSTEM_PROMPT，英文 query 永远走旧 prompt → A/B 实验零漂移。
SYSTEM_PROMPT_CJK = (
    "你是美妆导购的意图识别器。用户用中文描述需求（肤质/妆效/遮瑕/质地/色号/预算/避雷）。\n"
    "只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块：\n"
    '{"意图": ["规范意图名"], '
    '"约束": {"肤质": "油皮/干皮/混合/混油/混干/中性/敏感/痘痘 或 null", '
    '"妆效": "哑光/水光/自然/光泽 或 null", '
    '"遮瑕": "高/中/轻 或 null", '
    '"质地": "液体/粉状/乳霜/气垫/棒状 或 null", '
    '"色号": "白皙/深色 或 null", '
    '"预算": "数字(美元)或 null"}, '
    '"负约束": ["用户明确说不要的，如 闷痘/刺激/卡粉/脱妆/油腻，没有就空数组"], '
    '"证据": "一句话说明推断依据（引用原文关键词）"}\n'
    "意图只能是以下规范名之一（宁少勿多）："
    "防晒/SPF、防水持妆、油皮控油、哑光妆效、干皮保湿、熟龄肌、轻薄质地"
)


def _load_api_key():
    if not ENV.exists():
        return None
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DEEPSEEK_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _parse_llm_json(text):
    """剥 markdown fence → json.loads → 去尾逗号 → ast.literal_eval，全部失败返回 None。"""
    t = str(text).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I).strip()
    for cand in (t, re.sub(r",\s*([}\]])", r"\1", t)):
        try:
            obj = json.loads(cand)
            return obj if isinstance(obj, dict) else None
        except Exception:
            continue
    try:
        obj = ast.literal_eval(t)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _canonical_intents(raw_intents):
    """LLM 意图 → 规范轴（只在 VERIFIABLE 内保留，去重保序）。"""
    out = []
    for item in raw_intents or []:
        s = str(item)
        for pat, canon in INTENT_MAP:
            if pat.search(s) and canon not in out:
                out.append(canon)
                break
    return out


class LlmIntentFallback:
    """规则盲区 → LLM 兜底。统计（调用/缓存命中/延迟/tokens）供 eval_compare 读。"""

    def __init__(self, api_key=None, model=MODEL, cache_path=None):
        self.api_key = api_key or _load_api_key()
        self.model = model
        self.cache_path = Path(cache_path) if cache_path else CACHE
        self.cache = self._load_cache()
        self.stats = {"calls": 0, "cache_hits": 0, "latency_ms": [], "tokens": 0}

    # ------------------------------------------------------------------ 缓存
    def _load_cache(self):
        if self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_cache(self):
        try:
            self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------- 触发条件
    def _rule_has_signal(self, req, meta):
        """规则是否已有任一可检索信号（肤质/妆效/遮瑕/质地/色号/预算/控油/持妆/熟龄…）。

        触发边界 = **规则完全盲区**：任何一条能检索的信号都算「规则能覆盖」，
        绝不上模型（对齐「规则能覆盖的绝不上模型」原则）。q8（settle 误触发、
        LLM 把 lasts all day 过度推断成防水持妆）、q15（tight 误触发）就是反面教材。
        """
        return bool(req.get("implicit") or req.get("hard") or req.get("soft")
                    or req.get("finish") or req.get("coverage") or req.get("form")
                    or req.get("shade_dir") or req.get("budget")
                    or meta.get("coverage_requested") or meta.get("control_oil")
                    or meta.get("long_wear") or meta.get("mature"))

    def should_fallback(self, req, meta, query):
        """规则完全盲区（无任何可检索信号）且（含语境线索词 或 含中文）→ 才调 LLM。

        中文特殊处理（配合 agent._cjk_explicit 中文显式约束规则层）：
        - 显式约束（肤质/妆效/遮瑕/质地/色号/预算/控油/持妆/负约束）已被规则直抽 → 规则能答，
          不上模型（省 2-12s）；
        - 含场景线索（海边/婚礼/换季/出汗/泛红…）→ 仍有规则看不见的隐式意图 → 上模型补；
        - 裸问（无约束无场景）→ 直接 ask_all，不上模型空转。
        eval 集全英文 → 中文分支永不命中，锚点 / hidden A/B 零漂移。
        """
        q = str(query)
        if CJK.search(q):
            return bool(CJK_SCENE.search(q))
        if self._rule_has_signal(req, meta):
            return False
        return bool(CUE.search(q))

    # ----------------------------------------------------------------- 调用
    def extract(self, query):
        """调 DeepSeek → 解析 dict。失败/超时/无 key/解析失败 → None（降级）。"""
        q = str(query)
        key = hashlib.sha256(q.encode("utf-8")).hexdigest()
        if key in self.cache:
            self.stats["cache_hits"] += 1
            return self.cache[key]
        if not self.api_key:
            return None
        # 中文 query → 用带显式约束/负约束/预算的中文 prompt；英文永远走 SYSTEM_PROMPT
        prompt = SYSTEM_PROMPT_CJK if CJK.search(q) else SYSTEM_PROMPT
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": q},
            ],
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
        }
        t0 = time.time()
        timeout = TIMEOUT_CJK if CJK.search(q) else TIMEOUT
        try:
            r = requests.post(API_URL, headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }, json=body, timeout=timeout)
            latency = int((time.time() - t0) * 1000)
        except Exception:
            return None
        if r.status_code != 200:
            return None
        try:
            payload = r.json()
            content = payload["choices"][0]["message"]["content"]
            self.stats["tokens"] += payload.get("usage", {}).get("total_tokens", 0)
            self.stats["latency_ms"].append(latency)
        except Exception:
            return None
        parsed = _parse_llm_json(content)
        if parsed is None:
            return None
        self.stats["calls"] += 1
        self.cache[key] = parsed
        self._save_cache()
        return parsed

    # ---------------------------------------------------------- 信任信号
    def validate(self, intent_dict, req, idx):
        """检索兑现率信任信号。返回 (ok, 采信的规范隐式意图, reason)。

        三步门：意图→规范轴（丢弃库兑现不了的）；目录兑现数≥CATALOG_MIN；
        按该意图检索 top-8 真兑现占比≥FULFILL_MIN。任一不过 → 拒绝 → 降级。

        妆效合并：LLM 常把妆效写在「约束.妆效」（q27/33 的哑光就在这，不在"意图"列表）
        —— 并进候选意图一起过兑现率门（哑光→哑光妆效），避免听漏答题纸另一栏。"""
        raw = list(intent_dict.get("意图") or [])
        yz = (intent_dict.get("约束") or {}).get("妆效")
        if yz:
            raw.append(str(yz))
        implicits = _canonical_intents(raw)
        if not implicits:
            return False, [], "llm_intent_not_verifiable"
        rules = dict(IMPLICIT_RULES)
        ok, fails = [], []
        for imp in implicits:
            fn = rules.get(imp)
            if fn is None:
                fails.append(f"{imp}不可检索")
                continue
            n_cat = sum(1 for p in idx.records if fn(p))
            if n_cat < CATALOG_MIN:
                fails.append(f"{imp}库内仅{n_cat}件(<{CATALOG_MIN})")
                continue
            req2 = {**req, "implicit": [imp], "hard": set(), "soft": set(),
                    "finish": None, "coverage": None, "form": None, "shade_dir": None}
            top = idx.score_candidates("tagfirst", req2, list(idx.by_asin))[:8]
            if not top:
                fails.append(f"{imp}无检索结果")
                continue
            hit = sum(1 for a, _s, _r in top if fn(idx.by_asin.get(a)))
            ratio = hit / len(top)
            if ratio < FULFILL_MIN:
                fails.append(f"{imp}兑现率{ratio:.0%}(<{FULFILL_MIN:.0%})")
                continue
            ok.append(imp)
        if not ok:
            return False, [], "检索兑现率不过:" + ";".join(fails)
        return True, ok, "；".join(fails) if fails else "llm-verified"


# ---------------------------------------------------------------------------
# 语言桥：任意语种 → 英文（多语种分层路由方案1 的翻译层）
#   规则引擎永远是检索决策权威；LLM 只做「翻译」，翻译结果喂回英文规则引擎。
#   失败/超时/无 key/解析失败 → None（调用方降级回规则，绝不让翻译崩掉整轮）。
#   缓存 data/translate_cache.json（幂等重跑可复现 + 省 token；无 key 本体落盘）。
# ---------------------------------------------------------------------------
TRANSLATE_SYSTEM = (
    "You are a translation bridge for a beauty shopping assistant. "
    "Translate the user's foundation/makeup shopping request into natural, fluent English. "
    "Keep all numbers and brand names as-is. "
    "Render any price or budget amount in the exact format $20 (dollar sign + digits) — "
    "never write '20 dollars' or '20 USD'. "
    "Output ONLY the translated English sentence — no explanation, no quotes, no markdown."
)


def translate_to_english(text, api_key=None, timeout=TIMEOUT):
    """任意语种 query → 英文句子。失败/无 key → None。

    语言桥被严格限定为「翻译层」：不改语义、不替规则做任何检索决策，
    翻译后的英文直接交给英文规则引擎（lang_router.route 消费）。"""
    q = str(text).strip()
    if not q:
        return None
    key = hashlib.sha256(q.encode("utf-8")).hexdigest()
    cache = {}
    if TRANSLATE_CACHE.exists():
        try:
            cache = json.loads(TRANSLATE_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    if key in cache:
        return cache[key].get("en")
    api_key = api_key or _load_api_key()
    if not api_key:
        return None
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": TRANSLATE_SYSTEM},
            {"role": "user", "content": q},
        ],
        "temperature": 0,
        "max_tokens": 300,
    }
    try:
        r = requests.post(API_URL, headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }, json=body, timeout=timeout)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        en = r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None
    en = re.sub(r'^["\'“”]+|["\'“”]+$', "", en).strip()
    if not en:
        return None
    cache[key] = {"en": en, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        TRANSLATE_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    except Exception:
        pass
    return en


# ---------------------------------------------------------------------------
# --test 单链路调试
# ---------------------------------------------------------------------------
def _main():
    ap = __import__("argparse").ArgumentParser(description="LLM 模糊意图兜底 · 单链路调试")
    ap.add_argument("--test", help="单条 query 走 should_fallback → extract → validate")
    args = ap.parse_args()
    if not args.test:
        ap.error("需要 --test <query>")
    query = args.test

    from retrieval_engine import ProductIndex
    from agent import GuideAgent

    fb = LlmIntentFallback()
    agent = GuideAgent()
    req, meta = agent.extract_constraints(query)
    print(f"Q: {query}")
    print(f"规则层: implicit={req['implicit']} control_oil={meta['control_oil']}")
    fire = fb.should_fallback(req, meta, query)
    print(f"should_fallback = {fire}")
    if not fire:
        print("→ 规则已覆盖 / 无线索，LLM 不介入（这正是设计）")
        return
    out = fb.extract(query)
    if out is None:
        print("→ LLM 无输出/超时/无 key → 降级回规则")
        return
    print(f"LLM 意图: {out.get('意图')}")
    print(f"LLM 证据: {out.get('证据')}")
    ok, implicits, reason = fb.validate(out, req, agent.idx)
    print(f"validate: ok={ok} 采信={implicits} reason={reason}")
    if ok:
        print("→ 检索兑现率通过 → 采信并喂回 decide_ask（strong 含 implicit）")
    else:
        print("→ 检索兑现率不过 → 降级回规则（A==B）")
    print(f"stats: {fb.stats}")


if __name__ == "__main__":
    _main()
