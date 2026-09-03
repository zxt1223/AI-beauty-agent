# -*- coding: utf-8 -*-
"""
Phase 2 RAG 导购 Agent · 3 显式决策节点 + grounding 生成
=========================================================
用户要求：导购 Agent 要有一套**标准流程，以后自己执行**——不是写死的线性
脚本，而是把三个「要不要」做成显式决策节点，Agent 自判断自执行自修复：

  ① 决策节点「追问」：ask_all / ask_first / ask_shade_soft / no_ask
     （规则 = 2026-08-27 用户验收定稿的追问策略）
  ② 决策节点「改写重试」：硬过滤后无解 → infer_implicit 注入隐式词 → 重试一次
  ③ 决策节点「诚实兜底」：重试后仍无解 / 需求矛盾（又油又干）→ 直说 + 替代方向

输出 = 一个**结构化决策记录 dict**，对话 demo 和 CONTRACT 断言（eval_agent.py）
都消费它。无模型 key，纯确定性规则，可复现。

用法：
  python agent.py --chat "I have dry, dehydrated skin..."   # 交互对话（含追问）
  python agent.py --case "query"                            # 单条打印决策记录
"""
import io
import re
import sys
from pathlib import Path

import pandas as pd

# 幂等 UTF-8 包装（与 intent_reasoning 共用标记，避免重复包装）
if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True
ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")

from retrieval_engine import ProductIndex
from intent_reasoning import infer_implicit, rewrite_query
from defect_consensus import consensus_axes
from recall_router import RecallRouter
from ranker import get_ranker
from config import (HEAT_HI, HEAT_MID, SEM_WEIGHTS,
                    SEM_PROBE_THRESHOLD, SEM_PROBE_COARSE_K)

# 中文判定（前端双语支持）：extract_constraints 只认英文关键词，含中文 → 规则必然盲区，
# _llm_merge 补全显式约束（见 _merge_cjk_constraints）。eval 集 ids 1-41 全英文，零影响。
CJK = re.compile(r"[一-鿿]")

# 库外硬约束强声明（2026-09-02 用户拍板选项2）：only buy / hard requirement / must have /
# no X at any stage / zero X 等排他性硬声明 → 用户要的是可验证的硬保证（cruelty-free/纯素/
# 无动物成分等 catalog 标签空间外的属性），语义推荐无法硬保证 → 保持追问比放行诚实。
# q157 实证：conf=0.060 略过 θ，放行推荐混入池内 17-query negative 商品。语义试探入口拦截。
_HARD_DECLARE = re.compile(
    r"(only\s+buy|hard\s+requirement|must\s+have|at\s+any\s+stage|"
    r"no\s+animal|zero\s+(animal|derived)|strictly|absolutely\s+(no|must)|"
    r"refuse\s+to)",
    re.IGNORECASE)

# 缺陷证据轴 → 中文话术（product_defect_evidence.csv 的 defect_axes 词汇）
DEFECT_LABEL = {"卡粉": "卡粉/脱妆", "脱妆": "卡粉/脱妆", "刺激": "刺激/致敏",
                "闷痘": "闷痘", "油腻": "油腻"}

# ---------------------------------------------------------------------------
# 双语（回复层）：reply_lang="en" 时，回复正文/卡片证据用英文（确定性词典，零 LLM）。
# 默认 "zh" → 下面所有字符串逐字节不变（评测锚点零风险）。
# 库内标签词表是有界的（见 products_clean.csv 枚举），词典兜底 `.get(x, x)` 保底不崩。
# ---------------------------------------------------------------------------
TAGS_EN = {  # 库内标签 → 英文
    # 质地 form_tag
    "液体": "liquid", "粉状": "powder", "乳霜": "cream", "气垫": "cushion", "棒状": "stick",
    # 肤质 skin_tags
    "干皮": "dry", "油皮": "oily", "混合肌": "combination", "混干": "combo-dry",
    "混油": "combo-oily", "敏感肌": "sensitive", "痘痘肌": "acne-prone",
    "中性": "normal", "全肤质": "all skin types",
    # 妆效 finish_tag
    "哑光": "matte", "水光": "dewy", "光泽": "glow", "缎面": "satin", "自然": "natural",
    # 遮瑕 coverage_tag
    "高遮瑕": "full coverage", "中度遮瑕": "medium coverage", "轻遮瑕": "light coverage",
}
REASON_EN = {  # 命中原因 → 英文（citation 依据行）
    "肤质": "skin type", "全肤质": "all-skin", "干油双标(全年)": "dry+oily (all-year)",
    "单季品·降权": "seasonal (penalized)", "妆效": "finish", "遮盖": "coverage",
    "遮盖·置信": "coverage (low-conf)", "色号白皙": "fair shade", "色号深色": "deep shade",
    "色号深·扣": "shade", "色号白·扣": "shade",
    "隐式保湿": "hydrating", "隐式控油": "oil-control", "隐式防水": "long-wear",
    "隐式防晒": "SPF", "隐式哑光": "matte",
}
DEFECT_EN = {"卡粉": "cakes", "脱妆": "wears off", "刺激": "irritating",
             "闷痘": "clogs pores", "油腻": "greasy"}


def _en(zh, en, lang):
    """双语切换：lang=="en" 用英文，否则原样中文（默认路径零改动）。"""
    return en if lang == "en" else zh


class GuideAgent:
    """3 决策节点 + grounding 生成的导购 Agent。

    intent_mode: "rule"（默认，纯规则零 LLM，锚点口径不变）/
                 "hybrid"（规则盲区 → LLM 兜底，二期 A/B 实验组 B）。
    reply_lang:  "zh"（默认，回复/卡片全中文，锚点零风险）/
                 "en"（回复正文与卡片证据走英文词典，确定性、零 LLM、毫秒级）——
                 多语种分层路由（lang_router）把非中文输入路由到英文回复。
    """

    def __init__(self, idx=None, intent_mode="rule", reply_lang="zh"):
        self.idx = idx or ProductIndex()
        self.defect = self._load_defect()
        self.recall_router = RecallRouter(self.idx)   # 多路召回 + 路由（Phase-MVP）
        self.ranker = get_ranker(self.idx)            # 精排器接口（config.RANKER，当前冷启动 tagfirst）
        self.intent_mode = intent_mode
        self.reply_lang = reply_lang
        self._llm = None
        if intent_mode == "hybrid":
            from llm_intent import LlmIntentFallback
            self._llm = LlmIntentFallback()

    # ---- 双语工具 ----
    def _t(self, zh, en):
        """字符串级双语（模板/话术）。"""
        return en if self.reply_lang == "en" else zh

    def _tag(self, zh):
        """标签级双语：未知词保底原样（不崩、不硬翻）。"""
        return TAGS_EN.get(zh, zh) if self.reply_lang == "en" else zh

    # ------------------------------------------------------------------
    # 数据准备
    # ------------------------------------------------------------------
    def _load_defect(self):
        """商品缺陷证据表 → {asin: set(达标硬规则的缺陷轴)}。
        口径（用户定标 2026-08-28）：缺陷轴提及次数 ÷ 负面评论数 ≥70% → 硬规则（命中即避雷）。
        色号偏深黄/偏浅灰 = 色号适配，不算避雷轴；负面评论数 0 → 无法共识 → 不标。"""
        p = ROOT / "data" / "product_defect_evidence.csv"
        if not p.exists():
            return {}
        df = pd.read_csv(p, encoding="utf-8-sig").fillna("")
        return {str(r["parent_asin"]): axes
                for _, r in df.iterrows()
                if (axes := consensus_axes(r.get("defect_scores"), r.get("n_neg_reviews")))}

    # ------------------------------------------------------------------
    # LLM 兜底（二期：规则盲区 → 模型补隐式意图 → 检索兑现率验证 → 合并 or 降级）
    # ------------------------------------------------------------------
    def _llm_merge(self, req, meta, query):
        """hybrid 模式在 decide_ask 之前调用：盲区交给 LLM，采信才合并。

        返回 (req, meta, llm_info)。llm_info:
          fired / intent_source(llm|none) / evidence / degraded(降级原因，可选)。
        """
        if self._llm is None or not self._llm.should_fallback(req, meta, query):
            return req, meta, {"fired": False, "intent_source": "none", "evidence": ""}
        out = self._llm.extract(query)
        if out is None:
            return req, meta, {"fired": True, "intent_source": "none", "evidence": "",
                               "degraded": "llm_no_output"}
        is_cjk = bool(CJK.search(str(query)))
        ok, implicits, reason = self._llm.validate(out, req, self.idx)
        used = False
        if ok:
            # 采信：合并进 req["implicit"]（控油意图同步 meta，喂给检索打分）
            for imp in implicits:
                if imp not in req["implicit"]:
                    req["implicit"].append(imp)
            if any("油皮" in i or "控油" in i for i in implicits):
                meta["control_oil"] = True
            # 妆效意图同步 finish 轴：LLM 已明确哑光 → 跳过 D-2 追问（否则控油+哑光题
            # 被「控油但没妆效→先问」截胡成 ask_first，哑光答案白答——q26/27/33 教训）
            if "哑光妆效" in implicits and req["finish"] is None:
                req["finish"] = "哑光"
            used = True
        # 中文路径：显式约束/负约束/预算是用户明说的，**不挂在「隐式意图验证」后面**——
        # 即使 validate 不过（如只有「自然妆效」「预算」这类非 VERIFIABLE 轴），仍要合进
        # req，否则纯中文预算/遮瑕/肤质请求会被误降级成追问，中文「真实使用」走不通。
        if is_cjk:
            self._merge_cjk_constraints(req, meta, out, query)
            used = True
        if not used:
            return req, meta, {"fired": True, "intent_source": "none", "evidence": "",
                               "degraded": reason}
        return req, meta, {"fired": True, "intent_source": "llm",
                           "evidence": str(out.get("证据") or ""), "reason": reason}

    # 混油/混干 细粒度必须排在 油/干 之前（否则子串匹配把 混油 降级成 油皮，
    # 用户画像记忆和问候都保留不了细粒度）。只影响中文 hybrid 路径，eval（全英文）零影响。
    _CJK_SKIN = (("敏感", "hard", "敏感肌"), ("痘", "hard", "痘痘肌"),
                 ("混油", "soft", "混油"), ("混干", "soft", "混干"),
                 ("油", "soft", "油皮"), ("干", "soft", "干皮"),
                 ("混合", "soft", "混合肌"), ("中性", "soft", "中性"))

    def _merge_cjk_constraints(self, req, meta, out, query=""):
        """中文 query：把 LLM 返回的「约束/负约束/预算」合进 req（语义对齐 extract_constraints）。

        只在 should_fallback 触发（规则完全盲区）且 query 含中文时调用；
        英文盲区题（hidden 25-33）不走这里 → A/B 实验零漂移。
        遮瑕/质地/色号/预算对齐英文规则的硬过滤语义；负约束 → meta["negative_axes"] 走缺陷证据避雷。
        """
        c = out.get("约束") or {}
        skin = str(c.get("肤质") or "")
        for kw, kind, label in self._CJK_SKIN:
            if kw in skin:
                if kind == "hard":
                    req["hard"].add(label)
                else:
                    req["soft"].add(label)
                    meta["stated_skin"] = True
                meta["skins_stated"].add(label)   # 中文肤质也进记忆候选
        # 混油/混干 细粒度：LLM 常把「混油」归一成「混合肌」，用户画像记忆和问候
        # 就丢了细粒度。直接从原文补捞（只在本 LLM 路径运行，不影响 should_fallback），
        # 并踢掉语义重叠的粗粒度 混合肌（细粒度优先，检索和记忆都更精准）。
        q = str(query)
        for kw, label in (("混油", "混油"), ("混干", "混干")):
            if kw in q:
                req["soft"].add(label)
                meta["stated_skin"] = True
                meta["skins_stated"].add(label)
                req["soft"].discard("混合肌")
                meta["skins_stated"].discard("混合肌")
        finish = str(c.get("妆效") or "")
        if "哑光" in finish:
            req["finish"] = "哑光"
        elif "水光" in finish:
            req["finish"] = "水光"
        elif "自然" in finish or "光泽" in finish:
            req["finish"] = "自然"
        cov = str(c.get("遮瑕") or "")
        if "高" in cov or "full" in cov.lower():
            req["coverage"] = "高遮瑕"
        elif "中" in cov or "medium" in cov.lower():
            req["coverage"] = "中度遮瑕"
        elif "轻" in cov or "light" in cov.lower() or "sheer" in cov.lower():
            req["coverage"] = "轻遮瑕"
        if cov and not req["coverage"]:
            meta["coverage_requested"] = True   # 提到遮瑕但无级别（诚实标注，不硬过滤）
        form = str(c.get("质地") or "")
        for kw, f in (("液体", "液体"), ("粉状", "粉状"), ("气垫", "气垫"),
                      ("乳霜", "乳霜"), ("棒状", "棒状")):
            if kw in form:
                req["form"] = f
                break
        shade = str(c.get("色号") or "")
        if "深" in shade or "dark" in shade.lower():
            req["shade_dir"] = "dark"
        elif "白" in shade or "浅" in shade or "fair" in shade.lower():
            req["shade_dir"] = "fair"
        b = c.get("预算")
        if isinstance(b, (int, float)) and b > 0:
            req["budget"] = float(b)
        for item in out.get("负约束") or []:
            s = str(item)
            if "闷痘" in s or "breakout" in s.lower():
                meta["negative_axes"].add("闷痘")
            if "刺激" in s or "irritat" in s.lower():
                meta["negative_axes"].add("刺激")
            if "卡粉" in s or "cake" in s.lower():
                meta["negative_axes"].add("卡粉")
            if "脱妆" in s:
                meta["negative_axes"].add("脱妆")
            if "油腻" in s or "greas" in s.lower():
                meta["negative_axes"].add("油腻")

    # ------------------------------------------------------------------
    # ①.5 中文显式约束规则层（LLM 无关，直接抽原文）
    # ------------------------------------------------------------------
    def _cjk_explicit(self, req, meta, query):
        """中文显式约束规则层：从原文直接抽 肤质/妆效/遮瑕/质地/色号/预算/控油/持妆/负约束/熟龄。

        解决两个真问题：
        ① 慢——中文显式约束题（「我是冬混干夏混油」「油皮要哑光控油」）不必等 LLM（2-12s，
           甚至 25s 超时），规则抽到即答（毫秒级）；
        ② 脆——LLM 失败/超时时，用户明说的肤质不再丢（曾复现：LLM 返回空 → 整段 CJK 合并
           被跳过 → 用户说「冬混干夏混油」仍被 ask_all 问肤质）。
        在 run() 里 extract_constraints 之后、should_fallback 之前调用（hybrid/rule 都跑，
        内部按 query 是否含中文门控）；英文 query 走英文规则不碰这里 → eval 全英文锚点零影响。
        """
        q = str(query)
        if not CJK.search(q):
            return
        meta["cjk"] = True   # 中文路径标记 → _retrieve 的妆效严格执行/口碑护栏只在中文生效
        # ---- 肤质：细粒度优先（混干/混油 必须先于 干/油，否则子串把混油降级成油皮）----
        fine = [kw for kw in ("混油", "混干") if kw in q]
        skin_done = bool(fine)
        if fine:
            for f in fine:
                req["soft"].add(f)
                meta["skins_stated"].add(f)
            meta["stated_skin"] = True
        if not skin_done and re.search(r"t\s*区|T区", q):
            # T区油两颊干 = 混合肌经典表述；按偏油/偏干给 混油/混干
            has_o = bool(re.search(r"油|出油", q))
            has_d = bool(re.search(r"干", q))
            label = "混合肌" if (has_o and has_d) else ("混油" if has_o else ("混干" if has_d else "混合肌"))
            req["soft"].add(label)
            meta["stated_skin"] = True
            meta["skins_stated"].add(label)
            skin_done = True
        if "敏感" in q and "不敏感" not in q and "非敏感" not in q:
            req["hard"].add("敏感肌")
            meta["stated_skin"] = True
            meta["skins_stated"].add("敏感肌")
        # 痘→痘痘肌：只认「肤质」表述；「不要闷痘/怕闷痘/长痘」是负约束，不误判成肤质
        if re.search(r"痘痘肌|痘肌|痘皮|爱长痘|容易长痘|常长痘|长痘肤质|痘坑|易痘", q):
            req["hard"].add("痘痘肌")
            meta["stated_skin"] = True
            meta["skins_stated"].add("痘痘肌")
        if not skin_done:
            for kw, label in (("油", "油皮"), ("干", "干皮"), ("混合", "混合肌"), ("中性", "中性")):
                if kw in q:
                    req["soft"].add(label)
                    meta["stated_skin"] = True
                    meta["skins_stated"].add(label)
            # 混合偏油/偏干 → 细化为 混油/混干（比「混合肌+油皮」双标更精准；skins_stated 同步清理）
            if "混合" in q and ("偏油" in q or "油性" in q):
                req["soft"].discard("混合肌"); req["soft"].discard("油皮"); req["soft"].add("混油")
                meta["skins_stated"].discard("混合肌"); meta["skins_stated"].discard("油皮")
                meta["skins_stated"].add("混油")
            elif "混合" in q and ("偏干" in q or "干性" in q):
                req["soft"].discard("混合肌"); req["soft"].discard("干皮"); req["soft"].add("混干")
                meta["skins_stated"].discard("混合肌"); meta["skins_stated"].discard("干皮")
                meta["skins_stated"].add("混干")
        # ---- 妆效 ----
        if not req["finish"]:
            for kw, f in (("雾面", "哑光"), ("哑光", "哑光"), ("水光", "水光"),
                          ("滋润", "水光"), ("裸妆", "自然"), ("自然", "自然"), ("光泽", "自然")):
                if kw in q:
                    req["finish"] = f
                    break
            if req["finish"]:
                # 用户**原文明确说了妆效** → _retrieve 里当硬约束严格执行（只推该妆效款）。
                # 与 LLM 场景派生的妆效（海边→哑光）区分：派生妆效不硬排未标，避免误伤。
                meta["cjk_finish_explicit"] = True
        # ---- 遮瑕（级别；裸「遮瑕」只标意向，诚实标注不硬过滤）----
        if not req["coverage"]:
            if re.search(r"高遮瑕|遮瑕好|遮盖力强|遮瑕强|遮瑕力强|遮瑕度好", q):
                req["coverage"] = "高遮瑕"
            elif re.search(r"轻遮瑕|轻薄|清透|裸妆感", q):
                req["coverage"] = "轻遮瑕"
            elif re.search(r"中度遮瑕|中等遮瑕", q):
                req["coverage"] = "中度遮瑕"
        if not req["coverage"] and re.search(r"遮瑕", q):
            meta["coverage_requested"] = True
        # ---- 质地 ----
        if not req["form"]:
            for kw, f in (("粉底液", "液体"), ("液体", "液体"), ("粉状", "粉状"),
                          ("粉饼", "粉状"), ("气垫", "气垫"), ("乳霜", "乳霜"),
                          ("粉霜", "乳霜"), ("粉条", "棒状"), ("棒状", "棒状")):
                if kw in q:
                    req["form"] = f
                    break
        # ---- 色号方向 ----
        if not req["shade_dir"]:
            if re.search(r"白皙|偏白|白皮|浅色|浅皮", q):
                req["shade_dir"] = "fair"
            elif re.search(r"深色|偏深|深皮", q):
                req["shade_dir"] = "dark"
        # ---- 色号已说（黄二白家族：只标记「用户已说色号」，不硬塞 fair/dark——
        #      二白/三白是中调，塞白皙或深色哪档都错；标「已说」即可不追问，色号细收窄
        #      交给对话闸门 llm_gate 在追问轮直接答）----
        if not meta.get("shade_stated") and re.search(r"黄一白|黄二白|黄三白|二白|三白|黄调|暖调|偏黄|偏自然", q):
            meta["shade_stated"] = True
        # ---- 色号家族（对话闸门诊断后注入「色号自然/白皙/深色/冷调/橄榄」→ 推荐器按家族排序）----
        # 仅中文路径触发（词含「色号」前缀），英文 eval 恒不命中 → 锚点零漂移。
        m = re.search(r"色号(自然|白皙|深色|冷调|橄榄)", q)
        if m:
            req["shade_family"] = m.group(1)
            meta["shade_stated"] = True
        # ---- 预算（数值硬约束；「便宜/性价比」=软预算）----
        if req["budget"] is None:
            m = (re.search(r"\$(\d+(?:\.\d+)?)", q)
                 or re.search(r"(?:预算|价位|价格|不超过|以内|以下|左右|大概)\s*[:：]?\s*(\d+)\s*(?:美元|美金|刀|块)?", q)
                 or re.search(r"(\d+)\s*(?:美元|美金|刀|块)", q))
            if m:
                req["budget"] = float(m.group(1))
            elif re.search(r"预算|便宜|平价|性价比", q):
                req["budget"] = -1.0
        # ---- 控油 / 持妆（隐式意图轴，词直接出现即可，无需 LLM）----
        if re.search(r"控油|出油|油光|吸油", q):
            meta["control_oil"] = True
        if re.search(r"持久|持妆|不脱妆|不掉妆|一整天不掉", q):
            meta["long_wear"] = True
        # ---- 防晒/防水/保湿 隐式轴（词直接出现 = 规则可兑现，不劳 LLM）----
        if re.search(r"防晒|防嗮|spf", q, re.I) and "防晒" not in req["implicit"]:
            req["implicit"].append("防晒")
        if re.search(r"防水|防汗|遇水|下水", q) and "防水持妆" not in req["implicit"]:
            req["implicit"].append("防水持妆")
        if re.search(r"保湿|补水|水润", q) and "干皮保湿" not in req["implicit"]:
            req["implicit"].append("干皮保湿")
        # ---- 熟龄 / 新手 ----
        if re.search(r"熟龄|年纪|妈妈|长辈|斑点|遮斑", q):
            meta["mature"] = True
        if re.search(r"新手|第一次|从来没", q):
            meta["newbie"] = True
        # ---- 负约束 → 缺陷证据轴（「痘痘肌」是肤质，不误判成「怕闷痘」）----
        if re.search(r"卡粉|浮粉|斑驳|吃妆", q):
            meta["negative_axes"].add("卡粉")
        if re.search(r"刺激|过敏|刺痛", q):
            meta["negative_axes"].add("刺激")
        if re.search(r"闷痘|闭口|怕闷|不要长痘|别长痘", q):
            meta["negative_axes"].add("闷痘")
        if re.search(r"脱妆|易脱|脱粉|花妆", q):
            meta["negative_axes"].add("脱妆")
        if re.search(r"油腻|太油|油糊|油光满面", q):
            meta["negative_axes"].add("油腻")
        # ---- 季节/矛盾：冬混干夏混油=季节（全年可用），又油又干同时=无解 ----
        if ("冬" in q or "冬天" in q) and ("夏" in q or "夏天" in q):
            meta["seasonal"] = True
            req["seasonal"] = True
        elif re.search(r"又油又干|既油又干|同时油和干|油也干", q):
            meta["unsolvable"] = True

    def _axis_rejected(self, q, word):
        """2026-09-02 v3 triage 否定守卫：word（matte/oily/powder/stick…）是否被同小句否定/对比标记否决。

        q18 教训：不能全句宽窗搜否定词——"without touch-ups — I have oily skin" 的 without 与 oily
        隔着破折号小句，oily 是肤质声明不是被否定对象；宽窗 {0,28} 会把肤质/正意向误读成"不要该轴"。
        故按 . , ; ! ? — – : ( ) 换行 切小句，只在目标词所在小句内、其前 ≤3 个词找否定标记；
        "rather than X / instead of X / but not X / X over Y（X 后置=被否决）"整体视作否决。
        """
        q = str(q).lower().replace("’", "'")
        word = word.lower()
        neg_tok = (r"\b(?:not|no\b|never|without|avoid|isn'?t|is\s+not|aren'?t|are\s+not|don'?t|do\s+not|"
                   r"doesn'?t|does\s+not|won'?t|will\s+not|can'?t|cannot|over)\b")
        phrase = r"(?:rather\s+than|instead\s+of|but\s+not)"
        for clause in re.split(r"[.,;!?—–:()\n]+", q):
            if re.search(rf"{phrase}[^.,;!?—–:()\n]{{0,24}}\b{re.escape(word)}\b", clause):
                return True
            for m in re.finditer(rf"\b{re.escape(word)}\b", clause):
                head = clause[:m.start()]
                if re.search(rf"{neg_tok}(?:\s+\w+){{0,3}}\s*$", head):
                    return True
        return False

    # ------------------------------------------------------------------
    # ① 约束抽取（纯文本关键词规则，确定性；复用 intent_reasoning 场景规则）
    # ------------------------------------------------------------------
    def extract_constraints(self, query):
        q = str(query).lower()
        req = {"hard": set(), "soft": set(), "finish": None, "coverage": None,
               "form": None, "shade_dir": None, "implicit": [], "qtext": query,
               "vec_text": query, "budget": None, "seasonal": False,
               "shade_family": None}   # 色号家族（自然/白皙/深色/冷调/橄榄）：对话闸门诊断注入
        meta = {"control_oil": False, "stated_skin": False, "coverage_requested": False,
                "unsolvable": False, "mature": False, "newbie": False,
                "long_wear": False, "seasonal": False, "negative_axes": set(),
                "skins_stated": set(),   # 用户明确说过的肤质（跨会话记忆喂给 user_profiles）
                "shade_stated": False}   # 用户明确说过色号方向（黄二白家族/偏自然等）→ 不再追问

        # ---- 肤质（敏感肌/痘痘肌=硬约束；其余软偏好）----
        # 敏感肌=可自证温和的信号标签；痘痘肌单列（id7/19「prone to breakouts / acne-prone」
        # 是肤质双硬约束；id9「break out and turn red」是症状描述→只进敏感肌，不误判痘痘肌）
        if re.search(r"\bsensitive\b|\bhypoallergenic\b|\birritat|\bturn red\b|\bfragrance\b", q):
            req["hard"].add("敏感肌"); meta["skins_stated"].add("敏感肌")
        if re.search(r"\bacne[- ]?prone\b|\bprone to break[- ]?outs?\b|\bbreak[- ]?out[- ]?prone\b", q):
            req["hard"].add("痘痘肌")          # 与敏感肌构成双硬约束（需双标签或全肤质自证）
            meta["skins_stated"].add("痘痘肌")
        # 肤质一词多义（2026-09-02 v3 triage q128/q68/q42/q48）："isn't oily / won't ... feel greasy"
        # 是商品反特性不是肤质声明；"let it dry / dry climates"是动词与气候不是肤质 → 都不得抽肤质，
        # 否则错把全库油皮/干皮品捞到前面。
        if (re.search(r"\boily\b|\boil[- ]?control\b|\bgreas", q)
                and not self._axis_rejected(q, "oily") and not self._axis_rejected(q, "greasy")):
            req["soft"].add("油皮"); meta["stated_skin"] = True; meta["skins_stated"].add("油皮")
        _dry_verb = re.search(
            r"\b(?:let(?:ting)?\s+it\s+dry|dries?\s+(?:down|off|quickly|fast|naturally|to\s+a|within|by)\b"
            r"|\bdry\s+(?:for|it\b|out\b|climates?|weather|air\b|heat\b|season|environment|regions?|brushes?|clean(?:ing)?))\b", q)
        if (re.search(r"\bdehydrat\b|\bflaky\b|\bdryness\b|\bparched\b|\bdry skin\b", q)
                or (re.search(r"\bdry\b", q) and not _dry_verb)):
            req["soft"].add("干皮"); meta["stated_skin"] = True; meta["skins_stated"].add("干皮")
        if re.search(r"\bcombination\b", q):
            req["soft"].add("混合肌"); meta["stated_skin"] = True; meta["skins_stated"].add("混合肌")
        if re.search(r"\bnormal\b", q):
            req["soft"].add("中性"); meta["skins_stated"].add("中性")
        if re.search(r"\bover (?:6\d|7\d|8\d|9\d)\b|\bmature\b|\baging\b|\bsenior\b", q):
            meta["mature"] = True
        if re.search(r"\bnever used\b|\bnew to\b|\bwhere should i start\b|\bbeginner\b", q):
            meta["newbie"] = True

        # ---- 控油信号（「eliminates shine」≠ 哑光，见 D-2 追问设计）----
        # 2026-08-27 修复：漏掉「controls oil」（id9）与「oily skin」（id14/18）→
        # 油皮控油隐式意图不触发。补 \bcontrol[ls]? oil\b、\boily\b（matte 不并入——
        # 干皮也要哑光，控油≠哑光是 D-2 铁律）。
        if re.search(r"\bshine\b|\bblot(?:ting)?\b|\bmattif\b|\bgreas\b|\boil[- ]?control\b|\bcontrol[ls]? oil\b|\boily\b", q):
            meta["control_oil"] = True

        # ---- 妆效 ----
        # 2026-09-02 v3 triage 否定读反修复：q59"rather than a matte finish" / q129"glowing but not matte" /
        # q45"isn't too matte" 原全被 \bmatte\b 误抽成哑光（真实用户要的是"非哑光"）→ matte 前有否定词
        # 则不认，dewy/glow 正常接管；"natural skin tone/shade"=肤色不是妆效（q123）→ 不抽自然妆效。
        _natural_tone = re.search(r"\bnatural\b[^.,;!?]{0,18}\b(?:tone|shade|undertone)\b", q)
        if re.search(r"\bmatte\b", q) and not self._axis_rejected(q, "matte"):
            req["finish"] = "哑光"
        elif re.search(r"\bdewy\b", q):
            req["finish"] = "水光"
        elif re.search(r"\bglow(?:ing|y)?\b|\bradiant\b", q):
            req["finish"] = "光泽"
        elif re.search(r"\bnatural(?:[- ]looking)?\b", q) and not _natural_tone:
            req["finish"] = "自然"

        # ---- 遮瑕（防色号陷阱：裸 medium/light=色号，不提取；「good/even coverage」=只要遮瑕无级别）----
        if re.search(r"\bfull[\s-]?coverage\b", q):
            req["coverage"] = "高遮瑕"
        elif re.search(r"\bmedium[\s-]?coverage\b", q):
            req["coverage"] = "中度遮瑕"
        elif re.search(r"\blight[\s-]?coverage\b|\bsheer\b", q):
            req["coverage"] = "轻遮瑕"
        if re.search(r"\b(?:good|even|great)\s+coverage\b|\bcoverage\b", q) and not req["coverage"]:
            meta["coverage_requested"] = True

        # ---- 质地（看 form_tag 值，不采信标题，KLAIRS 假命中教训）----
        # 2026-09-02 v3 triage 一词多义修复：powder 做定妆/工具用法（q56/q96"without needing powder"、
        # q86"under finishing powder"、q117"mattifying powder"）不是粉底粉状，只有 q125"a powder that blurs"
        # 这类把粉饼当目标品才抽粉状；won't stick=动词卡粉（q126）不是棒状质地。
        _powder_tool = (self._axis_rejected(q, "powder")
                        or bool(re.search(r"\b(?:finishing|setting|mattifying|translucent|baking)\s+powder\b"
                                          r"|\b(?:under|beneath|before|after|on top of|with a|with the)\b[^.,;!?—–]{0,16}\bpowder\b", q)))
        if not _powder_tool and re.search(r"\bpowder\b|\bmineral\b", q):
            req["form"] = "粉状"
        elif re.search(r"\bliquid\b", q):
            req["form"] = "液体"
        elif re.search(r"\bcushion\b", q):
            req["form"] = "气垫"
        elif re.search(r"\bcream\b", q):
            req["form"] = "乳霜"
        elif re.search(r"\bstick\b", q) and not re.search(r"\bsticks?\s+(?:to|on|up|out|around)\b", q) \
                and not self._axis_rejected(q, "stick"):
            req["form"] = "棒状"

        # ---- 色号方向（排除 fair share 习语；对齐 parse_query 逻辑）----
        if re.search(r"\bvery fair\b|\bquite fair\b|\bextremely pale\b|\bvery pale\b|\bpale\b|\bivory\b|\bporcelain\b|\blightest\b", q):
            req["shade_dir"] = "fair"
        elif re.search(r"\bdark\b|\bdeep\b|\btan\b", q):
            req["shade_dir"] = "dark"

        # ---- 预算（数值硬约束）----
        m = re.search(r"\$(\d+(?:\.\d+)?)", q)
        if m:
            req["budget"] = float(m.group(1))
        elif re.search(r"\bbudget\b|\bcheap(?:est)?\b", q):
            req["budget"] = -1.0          # 提到预算但无数值（软预算）

        # ---- 持妆/场景 ----
        if re.search(r"last(?:s)?(?: the)? (?:all|full) day|without touch-?ups|long[- ]lasting|waterproof|work day", q):
            meta["long_wear"] = True
        if "winter" in q and "summer" in q:
            meta["seasonal"] = True
            req["seasonal"] = True

        # ---- 矛盾检测（又油又干同时存在 → 需求无解，进兜底）----
        has_oily = bool(re.search(r"\boily\b|\boil[- ]?control\b|\bgreas", q))
        has_dry = bool(re.search(r"\bdry\b|\bdehydrat\b|\bflaky\b", q))
        if has_oily and has_dry:
            same_time = (("at the same time" in q or "simultaneously" in q
                          or re.search(r"T[- ]?zone", q))
                         and re.search(r"\bboth\b", q))
            if same_time and not meta["seasonal"]:
                meta["unsolvable"] = True

        # ---- 隐式意图（场景归因：坎昆→防晒+防水；干皮→保湿；熟龄→滋润）----
        intent_hint = "控油" if (meta["control_oil"] and meta["stated_skin"]) else ""
        implicits, _rewrites, _hits = infer_implicit(intent_hint, query)
        for imp in implicits:
            if "防晒" in imp or "SPF" in imp.upper():
                req["implicit"].append("防晒")
            if "防水" in imp:
                req["implicit"].append("防水持妆")
            if "油皮" in imp or "混油" in imp:
                req["implicit"].append("油皮控油")
            if "哑光" in imp:
                req["implicit"].append("哑光妆效")
            if "干皮" in imp or "混干" in imp:
                req["implicit"].append("干皮保湿")
        if meta["mature"]:
            req["implicit"].append("干皮保湿")       # 熟龄偏滋润不拔干（D-3 逻辑）
        if meta["control_oil"]:
            req["implicit"].append("油皮控油")        # 控油 → 油皮适配（不推哑光，见追问）
            if meta["stated_skin"] and not req["finish"]:
                req["implicit"].append("哑光妆效")    # 明说油皮 + 控油 → 哑光（W-3 逻辑）

        # ---- 负约束 → 缺陷证据轴（避雷 + 诚实标注用）----
        if re.search(r"settle into pore|no streak|doesn'?t streak|\bstreak\b|\bstripe\b|\bcake|flake|crease|draw attention to", q):
            meta["negative_axes"].add("卡粉")
        if re.search(r"\birritat|\bfragrance\b|\balcohol\b|turn red|hypoallergenic", q):
            meta["negative_axes"].add("刺激")
        if re.search(r"\bacne\b|\bbreak ?out\b", q):
            meta["negative_axes"].add("闷痘")
        if re.search(r"doesn'?t last|\bfade\b|melts? off|comes off|not last", q):
            meta["negative_axes"].add("脱妆")

        # 去重保序（infer_implicit 映射 + 直加可能重复，见 id9 隐式控油×2）
        req["implicit"] = list(dict.fromkeys(req["implicit"]))

        return req, meta

    # ------------------------------------------------------------------
    # ② 决策节点「追问」（规则 = 用户验收定稿的追问策略）
    # ------------------------------------------------------------------
    def decide_ask(self, req, meta):
        # 缺失的关键约束（预算属软约束，不参与缺失计数；遮瑕只要「有遮瑕意向」就不算缺）
        missing = []
        if not req["hard"] and not req["soft"] and not meta["mature"]:
            missing.append("肤质")
        if not req["finish"]:
            missing.append("妆效")
        if not req["coverage"] and not meta["coverage_requested"]:
            missing.append("遮瑕")
        # 色号：用户已明说（黄二白家族/偏自然等）→ 不算缺失，不追问
        if not req["shade_dir"] and not meta.get("shade_stated"):
            missing.append("色号")

        # 矛盾需求 → 不追问，交给兜底节点
        if meta["unsolvable"]:
            return {"decision": "no_ask", "questions": []}

        # 强意图：用户至少给出了一个可检索的明确信号。
        # 含 req["implicit"]：LLM 兜底验证过的隐式意图必须进 strong，
        # 否则盲区题（缺失约束多）会被 ask_all 截胡、LLM 永远不生效（坑②）。
        strong = bool(req["hard"] or req["soft"] or req["finish"] or req["coverage"]
                      or req["form"] or req["shade_dir"] or meta["mature"]
                      or meta["control_oil"] or meta["long_wear"]
                      or bool(req["implicit"]))

        # ask_all：信息极缺（缺 ≥3 关键约束）且无强意图 —— 模糊 F-1/2/3、预算 P-3
        if len(missing) >= 3 and not strong:
            return {"decision": "ask_all", "questions": self._ask_questions(meta, req)}

        # ask_first：控油但没给妆效、且没说肤质 —— 妆效最改变候选集（D-2）
        if meta["control_oil"] and not req["finish"] and not req["soft"]:
            return {"decision": "ask_first",
                    "questions": [self._t(
                        "你要的是完全哑光雾面，还是自然一点带微光但能控油的？",
                        "Do you want full matte, or natural with a touch of glow but "
                        "still oil-controlling?")]}

        # ask_shade_soft：预算类 + 约束较全仅缺色号（P-1/P-2，色号尤其重要）
        # 用户已明说色号方向（黄二白家族/偏自然）→ 不再软追问
        if req["budget"] is not None and req["budget"] > 0 and not req["shade_dir"] \
                and not meta.get("shade_stated") \
                and (req["finish"] or req["soft"] or req["coverage"]):
            return {"decision": "ask_shade_soft",
                    "questions": [self._t(
                        "告诉我您常用色号，可以更精准噢",
                        "What shade do you usually wear — natural or fair? I can narrow it down.")]}

        return {"decision": "no_ask", "questions": []}

    def _ask_questions(self, meta, req):
        """一轮合并问完独立约束（先给友好预期，见 F-1 追问设计）。"""
        qs = []
        if self.reply_lang == "en":
            if meta["newbie"]:
                qs.append("Do you tend to get oily, or more on the dry side?")
                qs.append("Do you want a natural no-makeup look, or stronger coverage?")
                return qs
            qs.append("What's your skin type — oily, dry, or combination? Sensitive at all?")
            qs.append("Do you prefer a matte, dewy, or more natural finish?")
            if req["budget"] is not None and req["budget"] > 0:
                qs.append("What shade do you usually wear — more natural or fairer?")
            else:
                qs.append("And roughly what's your budget?")
            return qs
        if meta["newbie"]:
            qs.append("你平时出油多，还是觉得干？（判断肤质）")
            qs.append("想要自然裸妆的效果，还是遮盖力强一点的？")
            return qs
        qs.append("请问您的肤质是油皮、干皮还是混合皮？皮肤容易敏感吗？")
        qs.append("想要的妆效是哑光、水光，还是自然一些？")
        if req["budget"] is not None and req["budget"] > 0:
            qs.append("平时用什么色号的粉底？偏自然一点，还是偏白一点？")
        else:
            qs.append("方便问一下预算范围吗？")
        return qs

    # ------------------------------------------------------------------
    # 检索 + 决策节点「改写重试」+ 决策节点「诚实兜底」
    # ------------------------------------------------------------------
    def _semantic_probe(self, req, meta):
        """语义试探（2026-09-02 用户拍板 §8.18）：decide_ask 判 ask_all/ask_first 时的二次闸门。

        仅对无结构化约束 query（recall_router.channel=="semantic"）执行——reranker 全程隔离
        tagfirst，绝不碰结构化主链路。流程：mixed 粗排全库 top-20 → bge-reranker 精排 →
        top-1 置信度 ≥ SEM_PROBE_THRESHOLD(0.05) 判「有明确语义指向」→ 走语义推荐；
        < θ 判「真模糊」→ 保持 ask_all/ask_first 追问（锚点 q4/q5/q6 conf 0.001-0.008 实证）。

        返回 dict：{passed, top1_conf, reranked} 或 None（非语义通道，不试探）。
        reranked = [(asin, conf_score, reasons), ...] 精排降序，供 _retrieve 复用完整后置硬过滤。
        """
        # 入口条件 = 结构化约束全空（真正没有可检索标签）——不看 route_query 字符串。
        # 理由（2026-09-02 实测实证）：q64 humid stay put route=avoid、q88 affordable route=budget、
        # q74 true beige route=shade 是「route 语义归类有、但 extract_constraints 标签抽取全空」的
        # 词表外语义题，decide_ask 判 ask_all，恰恰是语义试探要救的对象；若按 RecallRouter.channel
        # （含 route 判定）会把这些题误归 tagfirst 挡在试探外（q43/q64/q88/q115 门禁点名验证失败）。
        # 有真实结构化约束（hard/soft/finish/coverage/form/budget/implicit 任一非空）→ 绝不进试探，
        # reranker 全程隔离 tagfirst 主链路。
        if (req.get("hard") or req.get("soft") or req.get("finish")
                or req.get("coverage") or req.get("form")
                or (req.get("budget") is not None) or req.get("implicit")):
            return None
        # 库外硬约束强声明 → 语义推荐无法硬保证（无法验证 cruelty-free/纯素/无动物成分等
        # catalog 标签空间外的属性）→ 保持 ask_all 追问，比放行诚实（2026-09-02 用户拍板）。
        # 能走到这里已保证标签全空，此时强声明词出现的诉求必在库外 → 拦截不试探。
        if _HARD_DECLARE.search(req.get("qtext") or ""):
            meta["_sem_block"] = "hard_declare"
            return None
        # mixed 排序需要向量分（bm25+向量各半）。_retrieve 已去无条件 enable_vectors 省性能，
        # 语义试探是唯一需要向量的兜底路径 → 在此显式启用（幂等：已加载直接返回）。
        self.idx.enable_vectors()
        # 惰性加载 reranker（与 eval_dual_channel.RerankerChannel 同路径，缺模型自动降级不崩）
        if getattr(self, "_reranker_model", None) is None:
            try:
                from sentence_transformers import CrossEncoder
                local = ROOT / "models" / "bge-reranker-base"
                path = str(local) if (local / "model.safetensors").exists() else "BAAI/bge-reranker-base"
                self._reranker_model = CrossEncoder(path)
                print(f"Reranker 就绪: {path}")
            except Exception as e:
                print(f"语义试探跳过（reranker 不可用: {e}）")
                self._reranker_model = False
        if self._reranker_model is False:
            return None

        cands = list(self.idx.by_asin)
        coarse = self.idx.score_candidates("mixed", req, cands, SEM_WEIGHTS)[:SEM_PROBE_COARSE_K]
        if not coarse:
            return None
        def _doc(p):
            def en(v):
                s = str(v).strip()
                return s if s and s.lower() not in ("nan", "missing") else ""
            return " ".join(x for x in [p["title"], p["brand"], en(p.get("finish_type")),
                                        en(p.get("coverage")), en(p.get("item_form")),
                                        en(p.get("skin_type"))] if x)
        pairs = [(req["qtext"], _doc(self.idx.by_asin[a])) for a, _s, _r in coarse]
        scores = self._reranker_model.predict(pairs)
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        scored = sorted(zip(coarse, scores), key=lambda x: -float(x[1]))
        top1_conf = float(scored[0][1])
        reranked = [(a, float(s), r) for (a, _s, r), s in scored]
        passed = top1_conf >= SEM_PROBE_THRESHOLD
        return {"passed": passed, "top1_conf": top1_conf, "reranked": reranked}

    def _retrieve(self, req, meta, n=12):
        """多路召回 → 路由 → 排序（真实场景，非候选池）→ 后置硬过滤（质地/遮瑕/预算/缺陷证据）。
        Phase-MVP（2026-09-01）：检索走 RecallRouter——有结构化约束（含隐式意图）路由到
        tagfirst（字段路=硬约束通过全集，与旧全库 tagfirst 字节级一致，锚点零漂移）；
        无约束路由到语义通道（BM25+向量，向量未加载自动降级）。route_trace 落 meta["route"]
        → _record → harness_trace.jsonl（可观测：各路由了多少、走哪个通道）。
        2026-08-29：去掉无条件 enable_vectors()——tagfirst 排序不用向量，rule/hybrid 路径
        都不依赖 bge；需向量的模式（vec/mixed）由 eval 脚本显式 enable，vec_sim 自持惰性加载。
        省掉 rule/hybrid 每次进程一次性 ~16s 模型载入 + 几百 MB 内存（diag_system_layer.py 实测）。"""
        ch, cands, rr = self.recall_router.route_and_recall(req)
        meta["route"] = {**rr, "cands": len(cands)}   # 含 channel/field/text/hot/vector/union
        if meta.get("_sem_reranked"):
            # 语义试探已产出 reranker 精排结果 → 直接复用（走后置硬过滤，不重复检索排序）
            scored = meta.pop("_sem_reranked")
        elif ch == "semantic":   # 无结构化约束 → 语义通道（D 通道口径，锚点题全走 tagfirst）
            scored = self.idx.score_candidates("mixed", req, cands, SEM_WEIGHTS)
        else:
            scored = self.ranker.rank(cands, req, meta)   # 精排接口：换精排只改 config.RANKER

        # 中文路径推荐质量护栏（2026-08-31 用户实测）：显式妆效已由下方硬过滤保证只推该妆效款，
        # 这里再兜一层口碑——不把低口碑商品推第一。只在 meta["cjk"]（中文 query）生效 → 英文锚点零漂移。
        # 口碑护栏：评分 <3.0 降 2 分；评论 <5 条且评分 <4.0 降 1 分——避免把 1.0 分/1 条
        # 的商品推第一（FACE Atelier 实测案例），水光款库内本就少，靠排序兜底不硬过滤。
        if meta.get("cjk"):
            def _cjk_rerank(item):
                asin, score, reasons = item
                p = self.idx.by_asin[asin]
                avg = _to_float(p.get("average_rating"))
                rn = _to_float(p.get("rating_number")) or 0
                if avg is not None and avg < 3.0:
                    score -= 2.0
                elif rn < 5 and (avg is None or avg < 4.0):
                    score -= 1.0
                # 热销加分（2026-08-31 用户：尽可能推荐热销，选 A 排序加分）——
                # 评论量按热度分档两档加分：≥200 +1.0 / ≥50 +0.5。只在中文路径生效
                # （英文锚点零漂移），妆效/质地等硬过滤不动（热销款只在约束内升序）。
                if rn >= HEAT_HI:
                    score += 1.0
                elif rn >= HEAT_MID:
                    score += 0.5
                return (asin, score, reasons)
            scored = sorted((_cjk_rerank(x) for x in scored),
                            key=lambda t: t[1], reverse=True)

        viable, excluded = [], []
        sink_pool = []   # B 分支无该 hard 轴信息品：单独收集，最后补位（带 risk_note）
        for asin, score, reasons in scored:
            p = self.idx.by_asin[asin]
            why = []
            p_skins = set(s for s in str(p.get("skin_tags") or "").split(";") if s)
            # 沉底品识别（2026-09-03 hard 轴三段 B 分支）：user 有 hard 轴要求但该品缺该轴标签
            # （能走到这里 = 未被 tag_score 的 -inf 排除 = 无该轴真雷缺陷证据 → 客观无信息）
            sink_missing = (sorted(h for h in req["hard"] if not (p_skins & {h, "全肤质"}))
                            if req["hard"] else [])
            # 死链拦截（2026-08-31）：链接失效（404）→ 推送前硬过滤。
            # dead_asins 由 web/harness 层传入（默认空 → eval 锚点零影响）
            if asin in meta.get("_dead", set()):
                excluded.append((asin, (f"dead link (404)"
                                        if self.reply_lang == "en" else
                                        f"商品链接已失效（404）")))
                continue
            # 质地硬约束：form_tag 值必须匹配（KLAIRS 标题 cushion 实际乳霜 → 排除）
            if req["form"] and p.get("form_tag") != req["form"]:
                excluded.append((asin, (f"texture mismatch (wanted {self._tag(req['form'])}, "
                                        f"it's {self._tag(p.get('form_tag') or '?')})"
                                        if self.reply_lang == "en" else
                                        f"质地不匹配（要{req['form']}，实际{p.get('form_tag') or '未标'}）")))
                continue
            # 遮瑕硬约束：显式指定遮瑕级时，已知不同级别排除；
            # 未标保留 + 诚实标注（不硬吹、不误排，对齐 id22 KLAIRS coverage 未标教训）
            if req["coverage"] and p.get("coverage_tag") and p["coverage_tag"] != req["coverage"]:
                excluded.append((asin, (f"coverage mismatch (wanted {self._tag(req['coverage'])}, "
                                        f"it's {self._tag(p['coverage_tag'])})"
                                        if self.reply_lang == "en" else
                                        f"遮瑕不匹配（要{req['coverage']}，实际{p['coverage_tag']}）")))
                continue
            # 妆效硬约束（2026-08-31 用户反馈「说了水光质地却原样推荐」→ 显式妆效必须被执行）：
            # 只对「中文原文显式说出妆效」生效（meta["cjk_finish_explicit"]）——英文评测锚点零漂移，
            # LLM 场景派生妆效（海边→哑光）不硬排未标、避免误伤。与质地/遮瑕同族，但更严一格：
            # 显式妆效 = 硬条件 → **未标 + 不匹配一并排除**（只推该妆效款）。
            # 实测教训：未标商品（Revlon/Sweet）肤质+质地分(3.0) 会压过水光款(2.0)，
            # 不清未标 = 用户说水光仍拿到两瓶非水光（复现过 top-3 只有 1 款水光）。
            # 水光库内仅 12 款 → 候选池收窄后由开头 CJK 分支的口碑护栏兜底排序。
            if meta.get("cjk_finish_explicit") and req["finish"] and p.get("finish_tag") != req["finish"]:
                excluded.append((asin, (f"finish mismatch (wanted {self._tag(req['finish'])}, "
                                        f"it's {self._tag(p.get('finish_tag') or 'unlabeled')})"
                                        if self.reply_lang == "en" else
                                        f"妆效不匹配（要{req['finish']}，实际{p.get('finish_tag') or '未标'}）")))
                continue
            # 口碑硬护栏（2026-08-31 用户再反馈）：评分 <3.0 = 差评区，不推。
            # 用户原话：「评分1分的要说清楚为什么1分，如果都是差评就别推了」——
            # 差评区直接排除，排除原因写进 excluded → 前端「已避开」块可见原因。
            # 只在 meta["cjk"] 生效 → 英文评测锚点零漂移（口碑劣质商品不因被剔而改排序）。
            if meta.get("cjk"):
                _avg = _to_float(p.get("average_rating"))
                if _avg is not None and _avg < 3.0:
                    _rn = int(_to_float(p.get("rating_number")) or 0)
                    excluded.append((asin, (f"poor reviews（rating {_avg:.1f} / {_rn}）"
                                            if self.reply_lang == "en" else
                                            f"差评区口碑（评分 {_avg:.1f}分 / {_rn} 条）")))
                    continue
            # 预算硬约束：有价且超预算太多 → 排除（缺价商品保留，标「待核实」）
            if req["budget"] and req["budget"] > 0:
                pr = _to_float(p.get("price"))
                if pr is not None and pr > req["budget"] * 1.3:
                    excluded.append((asin, (f"over budget (${pr:.2f} > ${req['budget']:.0f}×1.3)"
                                            if self.reply_lang == "en" else
                                            f"超预算（${pr:.2f} > ${req['budget']:.0f}×1.3）")))
                    continue
            # 缺陷证据负约束：query 明确「不要卡粉/刺激/闷痘」→ 命中对应缺陷的商品排除
            if meta["negative_axes"]:
                de = self.defect.get(asin, set())
                hit = de & meta["negative_axes"]
                if hit:
                    hit_txt = (", ".join(DEFECT_EN.get(a, a) for a in sorted(hit))
                               if self.reply_lang == "en"
                               else "、".join(sorted(hit)))
                    excluded.append((asin, ("flagged for: " + hit_txt if self.reply_lang == "en"
                                            else "命中缺陷证据：" + hit_txt)))
                    continue
            # B 分支沉底：无该 hard 轴信息（无缺陷证据）→ 不进精确款池，
            # 收进 sink_pool 备用（只在精确款+fill_in 仍不足 3 时补位，带 risk_note）
            if sink_missing:
                sink_pool.append((asin, score, reasons, p, sink_missing))
                continue
            viable.append((asin, score, reasons, p, why))
            if len(viable) >= n:
                break
        # 精确款不足 3 → 补接近款（2026-08-31 用户定「硬/软约束分层，权重排序，不要全硬约束」）：
        # 放宽序 = 质地 → 遮瑕（妆效是用户明确要的视觉感，fill-in 也不破）；
        # 安全硬约束（死链/预算/缺陷/差评区/敏感痘痘肌）fill-in 也绝不破。
        # fill-in 款排在精确款之后、按分数排；why 带 fill_in 标记供回复自然呈现（不暴露「库」）。
        # 2026-08-31 用户：fill-in 先补同质地（质地没放宽的），再放宽质地——显式说「要液体」
        # 也不至于一上来就拿棒状/气垫凑数。组内仍按分数降序（scored 本就降序，稳定）。
        if len(viable) < 3:
            seen = {a for a, *_ in viable}
            _fill_cands = []
            for asin, score, reasons in scored:
                if asin in seen:
                    continue
                p = self.idx.by_asin[asin]
                # fill-in 也绝不破 hard 轴（敏感痘痘肌）：无该轴信息品不参与质地放宽补位
                # （它已在主循环收进 sink_pool，由最后一步兜底，不走 fill_in）
                _ps = set(s for s in str(p.get("skin_tags") or "").split(";") if s)
                if req["hard"] and any(not (_ps & {h, "全肤质"}) for h in req["hard"]):
                    continue
                # 安全硬约束
                if asin in meta.get("_dead", set()):
                    continue
                if req["budget"] and req["budget"] > 0:
                    _pr = _to_float(p.get("price"))
                    if _pr is not None and _pr > req["budget"] * 1.3:
                        continue
                if meta["negative_axes"] and (self.defect.get(asin, set()) & meta["negative_axes"]):
                    continue
                if meta.get("cjk"):
                    _fa = _to_float(p.get("average_rating"))
                    if _fa is not None and _fa < 3.0:
                        continue
                # 妆效硬：fill-in 也不放宽
                if meta.get("cjk_finish_explicit") and req["finish"] and p.get("finish_tag") != req["finish"]:
                    continue
                # 放宽质地/遮瑕（构造自然的原因文本，供回复话术）
                fills = []
                if req["form"] and p.get("form_tag") != req["form"]:
                    fills.append(f"质地({self._tag(req['form'])}→{self._tag(p.get('form_tag') or '未标')})")
                if req["coverage"] and p.get("coverage_tag") and p["coverage_tag"] != req["coverage"]:
                    fills.append(f"遮瑕({self._tag(req['coverage'])}→{self._tag(p['coverage_tag'])})")
                if not fills:
                    continue
                _fill_cands.append((asin, score, reasons, p, fills))
            _fill_cands.sort(key=lambda c: (1 if any(f.startswith("质地") for f in c[4]) else 0,
                                            -c[1]))
            for asin, score, reasons, p, fills in _fill_cands[: max(0, 3 - len(viable))]:
                viable.append((asin, score, reasons, p, ["fill_in:" + "、".join(fills)]))
        # B 分支沉底补位（2026-09-03 hard 轴三段）：精确款 + fill_in 仍不足 3 → 才用无该轴信息品
        # 补足推荐数；why 带 sink_hard: 标记 → run() 据此给这些品加 risk_note 风险提示（卡片）。
        # 沉底品按原顺序（tagfirst 已按热度/分数排，负分全在尾部）取，绝不挤掉精确款。
        if len(viable) < 3 and sink_pool:
            _need = 3 - len(viable)
            _seen = {a for a, *_ in viable}
            for asin, score, reasons, p, missing in sink_pool:
                if asin in _seen:
                    continue
                viable.append((asin, score, reasons, p,
                               ["sink_hard:" + "、".join(missing)]))
                _seen.add(asin)
                if len(viable) >= 3:
                    break
        return viable, excluded

    def decide_retry(self, req, meta, viable):
        """改写重试：硬过滤后无解（<2 个可用候选）→ 注入隐式词改写 → 重试一次。"""
        if len(viable) >= 2:
            return {"triggered": False, "rewrite": ""}, req
        # 收集隐式改写词（场景/控油/保湿等），改写 vec_text 扩展召回
        intent_hint = "控油" if meta["control_oil"] else ""
        _imp, rewrites, _hits = infer_implicit(intent_hint, req["qtext"])
        if not rewrites:
            return {"triggered": False, "rewrite": ""}, req
        new_text = rewrite_query(req["qtext"], rewrites)
        if new_text == req["vec_text"]:
            return {"triggered": False, "rewrite": ""}, req
        req2 = dict(req)
        req2["vec_text"] = new_text
        return {"triggered": True, "rewrite": new_text}, req2

    def decide_fallback(self, req, meta, viable):
        """诚实兜底：无解矛盾直接全兜底；有解但需诚实说明 → 附注。"""
        if meta["unsolvable"]:
            # 仍检索「最接近」的平衡型作为替代方向（软推，不进推荐列表）
            alt_req = dict(req)
            alt_req["soft"] = {"干皮", "油皮", "混合肌"}
            alt_req["hard"] = set()
            alt_v, _ = self._retrieve(alt_req, {"negative_axes": set()}, n=5)
            alts = [{"asin": a, "title": self.idx.by_asin[a]["title"][:40],
                     "tags": self._tags_text(self.idx.by_asin[a], req)} for a, *_ in alt_v]
            return {"triggered": True, "level": "full",
                    "message": self._t(
                        "强控油和强保湿很难靠一瓶粉底同时做到——"
                        "让一瓶粉底既控住 T 区出油、又滋润脸颊起皮不现实。",
                        "No single foundation is both strongly oil-controlling and deeply "
                        "hydrating — asking one bottle to fix an oily T-zone and dry cheeks "
                        "isn't realistic."),
                    "alternatives": alts}, []
        # 换季「一件到底」：没有粉底会自动调肤 → 诚实附注，但仍推荐平衡型
        if meta["seasonal"]:
            return {"triggered": True, "level": "honest_note",
                    "message": self._t(
                        "没有一款粉底会自动调肤：冬天干夏天油时，"
                        "建议选平衡型 + 极端季节微调护肤/分区用（T 区哑光定妆、脸颊保湿打底）。",
                        "No foundation adapts on its own: if you're dry in winter and oily in "
                        "summer, pick a balanced formula + adjust skincare by season "
                        "(matte powder on the T-zone, hydrating primer on the cheeks)."),
                    "alternatives": []}, viable
        return {"triggered": False, "level": "", "message": "", "alternatives": []}, viable

    # ------------------------------------------------------------------
    # ⑤ 生成（grounding：只用命中商品事实 + tag_score 命中原因 + 四件套 + 诚实标注）
    # ------------------------------------------------------------------
    def _tags_text(self, p, req):
        parts = []
        for col in ("form_tag", "skin_tags", "finish_tag", "coverage_tag"):
            if p.get(col):
                if self.reply_lang == "en":
                    # skin_tags 是「;」分隔的多值 → 逐词英译
                    toks = [self._tag(t.strip()) for t in str(p[col]).split(";")]
                    parts.append(";".join(toks))
                else:
                    parts.append(str(p[col]))
        # 色号：只在 query 指定色号方向时才展示（id3 用户批「没答色号直接判断白皙」）
        if req["shade_dir"] and p.get("shade_tag"):
            parts.append(str(p["shade_tag"]))
        return "+".join(parts) if parts else self._t("（标签未标注）", "(no tags listed)")

    def _human_blurb(self, p):
        """一句话简介：把库内标签拼成人话（质地/妆效/肤质/遮瑕）。
        coverage_tag 已含「遮瑕」后缀（如「高遮瑕」）；skin_tags 用「;」分隔 → 换顿号。
        reply_lang="en" 时走英文词典，确定性零 LLM。"""
        parts = []
        if self.reply_lang == "en":
            if p.get("form_tag"):
                parts.append(f"{self._tag(p['form_tag'])} texture")
            if p.get("finish_tag"):
                parts.append(f"{self._tag(p['finish_tag'])} finish")
            if p.get("skin_tags"):
                skins = ", ".join(self._tag(t.strip()) for t in str(p["skin_tags"]).split(";"))
                parts.append(f"for {skins}")
            if p.get("coverage_tag"):
                parts.append(self._tag(p["coverage_tag"]))
            return ", ".join(parts) if parts else "Great overall reviews — check the product page for specs"
        parts = []
        if p.get("form_tag"):
            parts.append(f"{p['form_tag']}质地")
        if p.get("finish_tag"):
            parts.append(f"{p['finish_tag']}妆效")
        if p.get("skin_tags"):
            parts.append("适合" + str(p["skin_tags"]).replace(";", "、"))
        if p.get("coverage_tag"):
            parts.append(str(p["coverage_tag"]))
        return "、".join(parts) if parts else "整体口碑不错，具体参数建议看商品页"

    def _build_evidence(self, p, reasons, req, budget):
        asin = str(p["parent_asin"])
        price_raw = _to_float(p.get("price"))
        rn = _to_float(p.get("rating_number")) or 0
        avg = _to_float(p.get("average_rating"))

        # 价格 + 预算状态（用户定 2026-08-29：缺价就不显示，绝不说「价格待核实」，会伤信任感）
        if price_raw is not None:
            price_txt = f"${price_raw:.2f}"
            if budget and budget > 0:
                if price_raw <= budget:
                    status = "预算内" if self.reply_lang != "en" else "within budget"
                elif price_raw <= budget * 1.3:
                    status = (f"微超${price_raw - budget:.2f}" if self.reply_lang != "en"
                              else f"slightly over ${price_raw - budget:.2f}")
                else:
                    status = "超预算" if self.reply_lang != "en" else "over budget"
            else:
                status = ""
        else:
            price_txt = ""   # 缺价：回复/卡片里直接省略价格，不露「待核实」
            status = ""

        # 口碑 + 热度分档（评论量 高≥200/中 50-199/低<50）
        if self.reply_lang == "en":
            rating_txt = f"{avg:.1f}★ / {int(rn)} ratings" if avg is not None else "rating pending"
            heat = "high" if rn >= HEAT_HI else ("medium" if rn >= HEAT_MID else "low")
        else:
            rating_txt = f"{avg:.1f}分/{int(rn)}条" if avg is not None else "口碑待核实"
            heat = "高" if rn >= HEAT_HI else ("中" if rn >= HEAT_MID else "低")

        # 依据 = tag_score 命中原因（只含 query 实际指定的轴；reasons 由引擎按 req 生成）
        if self.reply_lang == "en":
            citation = "; ".join(REASON_EN.get(r, r) for r in reasons) if reasons \
                else "(matched by keywords/popularity)"
        else:
            citation = "；".join(reasons) if reasons else "（无标签命中，靠关键词/热度匹配）"

        # 诚实标注：query 指定了但商品未标注的轴（对外不暴露「库」字眼，2026-08-31 用户定）
        honest = []
        if req["coverage"] and not p.get("coverage_tag"):
            honest.append(self._t("该商品遮瑕度未标注，需确认",
                                  "Coverage not listed — worth a check"))
        if req["hard"] and not p.get("skin_tags"):
            honest.append(self._t("该商品肤质未标注", "Skin type not listed"))
        if req["form"] and not p.get("form_tag"):
            honest.append(self._t("该商品质地未标注", "Texture not listed"))
        if req["finish"] and not p.get("finish_tag"):
            honest.append(self._t("该商品妆效未标注，需确认",
                                  "Finish not listed — worth a check"))

        # 评论区反馈 + 销量话术（导购口径，2026-08-29 用户两次定：挑着说好话、促成购买；
        # 绝不说「一般/慎入/待核实」等泄气字眼。缺陷证据只用于避雷**排除**商品（excluded），
        # 推荐文本里不出现任何劝退话——否则用户第一个就不考虑了）
        # 口碑话术必须诚实（2026-08-31 用户再反馈）：差评区（avg<3.0）绝不吹「好评/值得入手」——
        # FACE Atelier 1.0 分被夸「好评口碑，值得入手」是打脸。差评区明说差评、给原因；低评论量
        # 但口碑不差才用鼓励话术。
        if avg is not None and avg < 3.0:
            comment_fb = (f"评分仅 {avg:.1f} 分 / {int(rn)} 条评价，口碑较差，建议谨慎"
                          if self.reply_lang != "en"
                          else f"Only {avg:.1f}★ / {int(rn)} ratings — poor reviews, be cautious")
        elif avg is not None and rn >= 5:
            comment_fb = (f"评论区用户反馈不错，{avg:.1f} 分 / {int(rn)} 条评价"
                          if self.reply_lang != "en"
                          else f"Buyers rate it well — {avg:.1f}★ / {int(rn)} ratings")
        else:
            comment_fb = self._t("评论区用户反馈不错，值得入手", "Buyers love it")

        if self.reply_lang == "en":
            if rn >= HEAT_HI:
                sales = "Top seller: 200+ reviews, flying off the shelf"
            elif rn >= HEAT_MID:
                sales = f"Popular: {int(rn)} reviews, plenty of buyers"
            elif rn >= 5:
                sales = (f"Great reviews: {avg:.1f}★ / {int(rn)} ratings, buyers are happy"
                         if avg is not None else f"Great reviews: {int(rn)} ratings, buyers are happy")
            else:
                sales = (f"Rated {avg:.1f}★ — poor reviews" if avg is not None and avg < 3.0
                         else (f"Rated {avg:.1f}★ — solid choice" if avg is not None
                               else "Well-reviewed — worth a try"))
        else:
            if rn >= HEAT_HI:
                sales = "爆款：评论 200+，卖得特别好"
            elif rn >= HEAT_MID:
                sales = f"卖得不错：{int(rn)} 条评论，很多人入手"
            elif rn >= 5:
                sales = (f"口碑不错：{avg:.1f} 分 / {int(rn)} 条评价，买过的基本都满意"
                         if avg is not None else f"口碑不错：{int(rn)} 条评价，买过的基本都满意")
            else:
                sales = (f"评分仅 {avg:.1f} 分（{int(rn)} 条），差评为主，慎入"
                         if avg is not None and avg < 3.0
                         else (f"评分 {avg:.1f} 分，好评口碑，值得入手"
                               if avg is not None else "好评口碑，值得入手"))

        return {
            "tags": self._tags_text(p, req),
            "price": price_txt,
            "budget_status": status,
            "rating": rating_txt,
            "heat": heat,
            "blurb": self._human_blurb(p),
            "comment_fb": comment_fb,
            "sales": sales,
            "link": f"🔗{asin}",
            "citation": ("based on: " + citation if self.reply_lang == "en"
                         else "依据：" + citation),
            "honest": honest,
        }

    # ------------------------------------------------------------------
    # 同族去重（2026-08-31 用户实测：同公式异色号被重复推荐，如两瓶 Becca
    # Ultimate Coverage 24 Hour 不同色号 → 用户问「为何有两个一模一样的商品」）
    # ------------------------------------------------------------------
    _FAMILY_KW = re.compile(
        r"\b(foundation|makeup|powder|cushion|primer|concealer|highlighter|"
        r"blush|cream|mousse|stick|serum|bb cream|base)\b", re.I)

    def _family_key(self, p):
        """族标签 = 品牌 + 标题公式核心（剥掉色号/容量/套装后，截到产品类型词为止）。
        Becca 各色号 → ('becca', 'ultimate coverage 24 hour foundation')，
        同公式异色号归一族；水光/哑光、液/棒等不同产品不会误合并。
        只用于推荐去重展示，不参与检索/排序/评测。"""
        b = str(p.get("brand") or "").lower().strip()
        t = str(p.get("title") or "").lower()
        t = t.replace(b, "")                        # 先剥品牌名（防 Milk Makeup/Make Up 截断错位）
        t = re.sub(r"(\d+\.?\d*\s*(fl\.?\s*oz|oz|ml|ounce|g|gr|pounds|lb|grams?))", " ", t, flags=re.I)
        t = re.sub(r"\b\d+(\.\d+)?\s*(ml|oz|g|fl oz)\b", " ", t, flags=re.I)
        t = re.sub(r"\(.*?\)", " ", t)
        t = re.sub(r"\b(pack of \d+|\d+\s*pack|\d+\s*x\s*\d+|2in1|mini)\b", " ", t, flags=re.I)
        m = self._FAMILY_KW.search(t)
        if m:
            t = t[:m.end()]
        t = re.sub(r"[^a-z0-9]+", " ", t).strip()
        return (b, t)

    def _pick_recs(self, req, meta, viable, n=3):
        """预算查询按「2 预算内 + 1 微超升级位」结构选；其余取 top-N。
        5 元组含 why（fill_in 标记）→ 供回复判断接近款，生成自然话术。
        2026-08-31 同族去重：品牌 + 公式核心相同 → 只保留分数最高一款，保证
        推荐列表里的商品彼此不同（Becca 异色号只出一瓶）。"""
        deduped, seen = [], set()
        for item in viable:                       # viable 已是分数降序
            k = self._family_key(item[3])          # item[3] = product dict
            if k in seen:
                continue
            seen.add(k)
            deduped.append(item)
        if not req["budget"] or req["budget"] <= 0:
            return deduped[:n]
        within, over = [], []
        for a, s, r, p, why in deduped:
            pr = _to_float(p.get("price"))
            if pr is None:
                within.append((a, s, r, p, why))          # 缺价：诚实标「待核实」
            elif pr <= req["budget"]:
                within.append((a, s, r, p, why))
            elif pr <= req["budget"] * 1.3:
                over.append((a, s, r, p, why))
        return (within + over)[:n]

    # ------------------------------------------------------------------
    # 主编排
    # ------------------------------------------------------------------
    def run(self, query, qid=None, query_type=None, profile=None, dead_asins=None):
        req, meta = self.extract_constraints(query)
        # 死链拦截（2026-08-31）：web/harness 层传入已失效商品链接（404）清单，
        # _retrieve 硬过滤。默认 None → eval/contract 路径零影响（锚点零漂移）。
        meta["_dead"] = set(dead_asins or ())

        # 中文显式约束规则层：中文 query 先直抽显式约束（肤质/妆效/遮瑕/质地/色号/预算/负约束），
        # 抽到即答（毫秒级，不用等 LLM 2-12s），且 LLM 失败/超时时用户明说的约束不丢。
        # 内部按 query 是否含中文门控 → 英文 query 零影响（eval 锚点零漂移）。
        self._cjk_explicit(req, meta, query)

        # LLM 兜底：规则盲区才介入，必须在追问决策之前
        # （命中的隐式意图进 strong，避免被 ask_all 截胡——坑②）
        llm_info = {"fired": False, "intent_source": "none", "evidence": ""}
        if self.intent_mode == "hybrid":
            req, meta, llm_info = self._llm_merge(req, meta, query)

        # 跨会话记忆：用户上次说过的肤质 → 本轮未明说肤质时自动采用。
        # 顺序关键：必须在 _llm_merge 之后——若先注入，should_fallback 会因 req["soft"]
        # 非空判定「规则已有信号」→ 中文 query 不再走 LLM 抽其他约束，中文体验退化。
        # 只在 profile 非 None 时执行（web 路径），eval/contract 走无 profile 路径 → 锚点零回归。
        memory_applied = []
        if profile and not meta.get("stated_skin") and profile.get("skins"):
            for s in profile["skins"]:
                (req["hard"] if s in ("敏感肌", "痘痘肌") else req["soft"]).add(s)
                memory_applied.append(s)
            meta["stated_skin"] = True   # 有记忆肤质 → 不再当缺失约束追问

        # ① 追问决策
        ask = self.decide_ask(req, meta)

        # 追问时先不推荐（等用户回答），直接进决策记录。
        # 2026-09-02 语义试探闸门（§8.18）：decide_ask 判 ask_all/ask_first 后先做一轮低成本
        # 语义试探——reranker top-1 置信度 ≥ θ 说明 query 有明确语义指向（词表外但语义可挖），
        # 走语义推荐；< θ 才是真模糊（锚点 q4/q5/q6）→ 保持追问。
        if ask["decision"] in ("ask_all", "ask_first"):
            probe = self._semantic_probe(req, meta)
            meta["semantic_probe"] = ({"entered": False, "passed": False, "top1_conf": None,
                                       "block": meta.get("_sem_block")}
                                      if probe is None else
                                      {"entered": True, "passed": probe["passed"],
                                       "top1_conf": probe["top1_conf"]})
            if probe is not None and probe["passed"]:
                # 语义试探通过 → 有语义指向 → 走语义推荐（不再追问）
                meta["_sem_reranked"] = probe["reranked"]     # _retrieve 语义分支直接复用
                ask = {"decision": "no_ask", "questions": []} # 语义推荐不追问
            else:
                return self._record(query, qid, query_type, req, meta, ask,
                                    {"triggered": False, "rewrite": ""},
                                    {"triggered": False, "level": "", "message": "", "alternatives": []},
                                    [], [], llm_info, memory_applied)

        # ② 检索（全库）
        viable, excluded = self._retrieve(req, meta)

        # ③ 改写重试节点
        retry, req = self.decide_retry(req, meta, viable)
        if retry["triggered"]:
            viable, excluded = self._retrieve(req, meta)

        # ④ 诚实兜底节点
        fallback, viable = self.decide_fallback(req, meta, viable)

        # ⑤ 选推荐 + 生成
        recs = self._pick_recs(req, meta, viable)
        recommendations = []
        for asin, _s, reasons, p, why in recs:
            # fill_in 标记：接近款（放宽质地/遮瑕补足推荐数），回复自然呈现、不暴露「库」
            fill_in = next((w.split(":", 1)[1] for w in (why or [])
                            if w.startswith("fill_in:")), None)
            # risk_note（hard 轴三段 C 分支，2026-09-03 用户批准）：补位进来的沉底品
            # 客观缺少该肤质轴用户反馈 → 卡片附风险提示（只出现在覆盖品不足被补位时）
            sink_hard = next((w.split(":", 1)[1] for w in (why or [])
                              if w.startswith("sink_hard:")), None)
            risk_note = None
            if sink_hard:
                _axes_en = " / ".join(self._tag(a.strip()) for a in sink_hard.split("、") if a.strip())
                risk_note = (f"⚠️ This item has no {_axes_en} user feedback yet — "
                             f"treat the match as a reference only"
                             if self.reply_lang == "en"
                             else f"⚠️ 该商品暂无{sink_hard}相关用户反馈，肤质匹配仅供参考")
            recommendations.append({
                "asin": asin,
                "title": str(p.get("title"))[:70],
                # 中文标题（批量翻译脚本写入 CSV/MySQL 后自动生效，未翻译前回退英文）
                "title_zh": str(p.get("title_zh") or p.get("title"))[:70],
                "evidence": self._build_evidence(p, reasons, req, req["budget"]),
                "fill_in": fill_in,
                "risk_note": risk_note,
            })

        # 避雷说明：被硬过滤排除的（excluded）挑前 3 条作为避雷记录
        avoided = [{"asin": a, "title": self.idx.by_asin[a]["title"][:40], "reason": r}
                   for a, r in excluded[:3]]

        return self._record(query, qid, query_type, req, meta, ask, retry, fallback,
                            recommendations, avoided, llm_info, memory_applied)

    def _record(self, query, qid, query_type, req, meta, ask, retry, fallback,
                recommendations, avoided, llm_info=None, memory_applied=None):
        llm_info = llm_info or {"fired": False, "intent_source": "none", "evidence": ""}
        memory_applied = memory_applied or []
        # 意图来源：llm（LLM 兜底采信）/ rule（规则推理命中）/ none（未命中）
        intent_source = ("llm" if (llm_info["fired"] and llm_info["intent_source"] == "llm")
                         else ("rule" if req["implicit"] else "none"))
        # 边问边推荐（软追问，2026-08-29 用户定：推荐的同时问偏好，前端渲染成可点击选项）。
        # 妆感优先（影响最大）；妆感已定再问色号（原 ask_shade_soft）。
        soft_question = None
        if not req["finish"]:
            soft_question = {"text": self._t("顺便问下，你喜欢什么妆感？",
                                             "By the way, what finish do you like?"),
                             "options": (["Matte", "Dewy", "Natural"] if self.reply_lang == "en"
                                         else ["哑光", "水光", "自然"])}
        elif ask["decision"] == "ask_shade_soft" and ask.get("questions"):
            soft_question = {"text": ask["questions"][0],
                             "options": (["Natural", "Fair"] if self.reply_lang == "en"
                                         else ["偏自然", "偏白"])}
        record = {
            "id": qid,
            "query_type": query_type,
            "query": query,
            "intent_source": intent_source,
            "llm_evidence": llm_info.get("evidence") or llm_info.get("degraded") or "",
            "route": meta.get("route"),   # 多路召回路由决策（Phase-MVP，落 harness_trace.jsonl）
            "constraints": {
                "hard": sorted(req["hard"]), "soft": sorted(req["soft"]),
                "finish": req["finish"], "coverage": req["coverage"],
                "form": req["form"], "shade_dir": req["shade_dir"],
                "shade_family": req.get("shade_family"),
                "shade_stated": bool(meta.get("shade_stated")),
                "budget": req["budget"],
                "implicit": req["implicit"],
                "unsolvable": meta["unsolvable"], "seasonal": meta["seasonal"],
                "newbie": meta["newbie"], "control_oil": meta["control_oil"],
                "negative_axes": sorted(meta["negative_axes"]),
            },
            "ask": ask,
            "retry": retry,
            "fallback": fallback,
            "recommendations": recommendations,
            "avoided": avoided,
            "soft_question": soft_question,
            "skins_stated": sorted(meta["skins_stated"]),   # 本轮用户明确说过的肤质 → 记忆候选
            "memory": {"applied": bool(memory_applied), "skins": memory_applied},
            "semantic_probe": meta.get("semantic_probe"),   # 语义试探埋点（§8.18）：entered/passed/top1_conf
            "reply": self._build_reply(record_view={
                "ask": ask, "retry": retry, "fallback": fallback,
                "recommendations": recommendations, "avoided": avoided,
                "soft_question": soft_question,
                "semantic_probe": meta.get("semantic_probe"),
                "constraints": {k: v for k, v in {
                    "hard": sorted(req["hard"]), "soft": sorted(req["soft"]),
                    "finish": req["finish"], "coverage": req["coverage"],
                    "form": req["form"], "shade_dir": req["shade_dir"],
                    "budget": req["budget"], "implicit": req["implicit"]}.items() if v},
                "newbie": meta["newbie"]}),
        }
        return record

    def _build_reply(self, record_view):
        ask, fallback = record_view["ask"], record_view["fallback"]
        recs, avoided = record_view["recommendations"], record_view["avoided"]
        en = self.reply_lang == "en"

        if ask["decision"] in ("ask_all", "ask_first"):
            head = ("To pick the best match, a couple quick questions:\n" if en
                    else "为了更好帮您筛选商品，请先回答几个问题：\n")
            return head + "\n".join(f"{i}. {q}" for i, q in enumerate(ask["questions"], 1))

        lines = []
        # 2026-08-31 用户：ask_shade_soft 的「（先按已有条件推荐，色号稍后帮您收窄）」话术永不呈现——
        # 页面不出现任何「先按已有条件/稍后收窄」措辞，推荐与色号追问直接呈现。
        if fallback["triggered"] and fallback["level"] == "full":
            # 兜底句与替代方向由前端黄色高亮块统一呈现（2026-08-31 用户：正文不再重复），
            # 正文只留黄色块里没有的分区处理提示。
            lines.append(self._t(
                "分区处理：T 区用哑光控油定妆，脸颊保湿打底后再上妆——「一瓶」让位于「一瓶+分区」。",
                "A pro move: matte oil-control powder on the T-zone, hydrating primer on the "
                "cheeks — sometimes one bottle can't do it all."))
            return "\n".join(lines)

        if not recs:
            return self._t(
                "目前没有完全符合您需求的产品。您可以补充肤质/妆效/预算信息，我帮您再筛一次。",
                "No perfect match right now — tell me your skin type, finish, "
                "or budget and I'll re-filter.")

        # 语义试探推荐提示（2026-09-02 §8.18）：词表外语义意图走语义试探产出 → 先加提示，
        # 让用户知道「这是语义理解得到的推荐，可进一步补充偏好精准筛选」（不暴露标签缺失细节）
        sp = record_view.get("semantic_probe")
        if sp and sp.get("passed"):
            lines.append(self._t(
                "未识别到明确的肤质/妆效标签，以下为语义理解得到的推荐，告诉我您的偏好可以进一步精准筛选。",
                "No explicit skin-type or finish labels detected — here are recommendations from "
                "understanding your request. Tell me your preferences and I can narrow it down."))

        # 人性化导语 + 每款 2 行（一句话简介 + 💬📈💰），短、暖、直给
        n = len(recs)
        lines.append(self._t(f"好嘞，帮您挑了 {n} 款：", f"Great — here are {n} picks for you:"))

        for i, r in enumerate(recs, 1):
            e = r["evidence"]
            title = r.get("title_zh") or r["title"]
            sep = "—" if en else "——"
            lines.append(f"{i}. {title} {sep} {e['blurb']}")
            bits = [f"💬 {e['comment_fb']}", f"📈 {e['sales']}"]
            if e["price"]:  # 缺价不显示（见 _build_evidence），保持信任感
                if en and e.get("budget_status"):
                    bits.append(f"💰 {e['price']} ({e['budget_status']})")
                else:
                    bits.append(f"💰 {e['price']}"
                                + (f"（{e['budget_status']}）" if e.get("budget_status") else ""))
            lines.append("  " + "｜".join(bits))

        # 2026-08-31 用户：fill-in「接近款」提示永不呈现——不要让用户知道背后有补款逻辑。
        # 补足数量的款照常出现在推荐里、不标注「接近款」；fill_in 标记保留在记录 dict 内部
        # （决策透明面板/调试用，前端卡片不渲染）。

        # 边问边推荐：软追问（前端渲染成可点击选项 chips）
        sq = record_view.get("soft_question")
        if sq:
            lines.append(f"\n💡 {sq['text']}（{'/'.join(sq['options'])}）")
        # 2026-08-31 用户：honest_note 兜底句由前端黄色高亮块统一呈现，正文不再重复
        return "\n".join(lines)

    def _short(self, asin):
        return str(self.idx.by_asin.get(asin, {}).get("title", asin))[:28]


def _to_float(x):
    try:
        f = float(str(x).replace("$", "").strip())
        return f if f == f else None
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
def main():
    ap = __import__("argparse").ArgumentParser(description="RAG 导购 Agent（3 决策节点）")
    ap.add_argument("--case", help="单条 query，打印结构化决策记录")
    ap.add_argument("--chat", help="交互对话（含追问），如 --chat \"I have dry skin...\"")
    ap.add_argument("--hybrid", action="store_true",
                    help="hybrid 模式：规则盲区 → LLM 兜底（二期 A/B 实验组 B）")
    ap.add_argument("--en", action="store_true",
                    help="英文回复：reply_lang=en（多语种分层路由的非中文回复用）")
    args = ap.parse_args()

    agent = GuideAgent(intent_mode="hybrid" if args.hybrid else "rule",
                       reply_lang="en" if args.en else "zh")

    if args.case:
        rec = agent.run(args.case)
        print(json_dump(rec))
        print("\n---- 回复 ----\n" + rec["reply"])
        return

    if args.chat:
        query = args.chat
        for turn in range(3):
            rec = agent.run(query)
            print(f"\n用户：{query}\n")
            print("导购：" + rec["reply"])
            if rec["ask"]["decision"] not in ("ask_all", "ask_first"):
                break
            ans = input("\n您的回答：").strip()
            if not ans:
                break
            query = f"{query} User says: {ans}"
        return

    # 无参数：打印一段示例
    for q in ["I have dry, dehydrated skin so I'm always looking for moisture.",
              "I'm a student on a tight budget — what's the cheapest foundation under $10 that still has good coverage?",
              "My skin is both very oily and very dry at the same time — oily T-zone, flaky cheeks. What single foundation works?"]:
        rec = agent.run(q)
        print("=" * 70)
        print(f"Q: {q}\n")
        print(rec["reply"])


def json_dump(rec):
    return __import__("json").dumps(rec, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
