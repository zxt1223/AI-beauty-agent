# -*- coding: utf-8 -*-
"""
harness.py — beauty_agent · 驾驭层（Harness）轻量中间件
====================================================
Harness 不是推理大脑，是 Agent 的运行管控底座（驾驭层）。现状 beauty_agent 已有 ~70%
Harness 基因（3 决策节点=确定性管控层、user_profiles=会话状态、避雷/诚实标注=护栏、
_record=可观测、LLM 超时/降级=降级链），本模块补齐缺失的四块，凑齐五大能力：

  ① 工具拦截 + 权限门  gate()：所有 agent 调用过这一个口，query 先过校验
                        （必须字符串/非空/长度有界）——不满足就在调用 agent 前拦下，
                        这就是「工具执行必须经过管控层」的最小落地。
  ② 会话状态与记忆    session_budget()：会话计数器（次数/LLM 触发/窗口），
                        画像/肤质记忆已在 user_profiles.json（web_server 侧），不重复。
  ③ 行为预算          pick_mode()：单会话 LLM 调用上限（成本控制），超限自动降级
                        纯规则模式 + 向用户明说原因，而不是悄悄变慢或拒绝。
  ④ 护栏栈            medical_note()：医疗越界校验——护肤品导购不是医疗建议，命中
                        治疗类话题（祛痘/祛斑/用药/处方等）在回复外附免责声明；
                        coerce_num()：数据有效性，预算/价格强制数字型，防「数字传给
                        .replace 崩」（预算宕机根因即前端把数字传给字符串函数）。
  ⑤ 全链路埋点        data/harness_trace.jsonl：输入→路由→约束→决策→推荐→耗时，
                        一条流水账，可回溯「这条推荐为什么出来」。

search_note：把「检索真实发生」讲给用户——「已从知识库 N 款中按条件筛出 M 款」，
对齐用户三条反馈之「太快像固定回复、怀疑全硬编码」：搜索过程可见，不神秘化。

用法：web_server.py 里
    from harness import Harness
    _harness = Harness()
    mode, warn = _harness.pick_mode(uid, routing)     # 预算感知路由
    agent = get_agent(mode, reply_lang)
    h = _harness.process(agent, q, uid=uid, profile=profile)   # 驾驭层执行
    rec = h["record"]; search_note = h["search_note"]; ...
零依赖，标准库；锚点零风险——纯中间层，不碰 agent 内部排序逻辑。
"""
import io
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:  # 在线探测依赖 requests；缺失则只认静态清单，不做在线复核（零依赖降级）
    import requests as _requests
except ImportError:
    _requests = None

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

ROOT = Path(__file__).resolve().parent.parent
TRACE_PATH = ROOT / "data" / "harness_trace.jsonl"

# ---- 会话预算（③）：单会话上限，超限降级而非拒绝（阈值单一真相源在 config.py）----
from config import SESSION_MAX_QUERIES, SESSION_MAX_LLM, SESSION_WINDOW_SEC

# ---- 医疗越界关键词（④）：护肤品 ≠ 医疗建议 ----
# 只收「治疗/用药」语义，不误伤日常选品——痘痘肌/敏感肌/遮痘印（遮盖）都是正常选品词，
# 不入表；「祛痘/祛痘印（治疗）」「烂脸」「用药」等才触发免责。
MEDICAL_HINT = [
    # 中文
    "祛痘", "祛斑", "祛痘印", "治疗", "用药", "处方", "激素", "医美",
    "烂脸", "皮炎", "湿疹", "皮肤科", "看医生", "口服", "内服", "激光手术",
    # 英文
    "prescription", "dermatologist", "medication", "see a doctor",
    "eczema", "rosacea", "laser treatment", "acne scar treatment",
]
MEDICAL_DISCLAIMER = {
    "zh": "⚠️ 我只能做护肤品选品建议，皮肤疾病或用药请以皮肤科医生意见为准。",
    "en": "⚠️ I can only recommend cosmetics — for skin conditions or medication, "
          "please follow your dermatologist's advice.",
}

# ------------------------------------------------------------------
# 死链拦截（2026-08-31 用户实测）：推送前最后一道拦截
# ------------------------------------------------------------------
_PROBE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}
_CANON = re.compile(r'rel="canonical"[^>]*href="([^"]+)"', re.I)


def _probe(asin):
    """GET Amazon 详情页 → live | dead | unknown。**自证探测**（2026-08-31 教训）：
    光看状态码会把死链误判 live——Amazon 对机器人/限流/404 常回 HTTP 200 的
    「兜底页」或「机器人页」。必须 200 且页面 canonical 指向该 asin（真商品页的
    canonical 就是商品链接）才判 live；404/410 = dead；其余一律 unknown
    （机器人页/兜底页/超时，绝不误判，这是红线）。"""
    if _requests is None:
        return "unknown"
    try:
        r = _requests.get(f"https://www.amazon.com/dp/{asin}", headers=_PROBE_HEADERS,
                          timeout=5, allow_redirects=True)
        if r.status_code in (404, 410):
            return "dead"
        if r.status_code != 200:
            return "unknown"
        m = _CANON.search(r.text[:300000])
        if m and f"/dp/{asin}" in m.group(1):
            return "live"
        return "unknown"   # 200 但不自证 = 机器人页/兜底页 → 不可信
    except Exception:
        return "unknown"


class LinkGuard:
    """死链拦截闸门。两层：
    ① 静态清单 data/dead_asins.json（预扫已确认 404 + 用户实测 3 款）= 硬过滤真源，
       随 agent.run(dead_asins=...) 在检索层剔除——永远生效，不受网络状态影响。
    ② 运行时在线复核：对推荐 top-N 轻量探测，新死链增量落盘 → 下次会话即生效。

    安全护栏（防限流误杀）：Amazon 对高频请求会返回机器人页/404（本机 IP 曾整片
    被挡）。探测前先探一个已知在线控制商品——控制品不 live → 本轮探测不可信，
    跳过（宁可不拦新死链，也绝不误杀在线商品）。静态清单不依赖此闸门。"""
    DEAD_FILE = ROOT / "data" / "dead_asins.json"
    CONTROL_ASIN = "B017U9AY4A"   # 已知长期在线商品（扫描时实测多次 200）
    CONTROL_TTL = 120             # 控制探测结果缓存 2 分钟
    LIVE_TTL = 3600               # 确认在线的商品 1 小时内不重复探
    PROBE_TIMEOUT = 5.0           # 单次探测上限（并行，TOP_N=3 → 最坏 ~5s，一般 <1s）
    TOP_N = 3                     # 只探推荐前 3 款（用户最可能点击的链接）

    def __init__(self):
        self._dead = set()
        self._live_ts = {}      # asin -> last confirmed-live ts
        self._trust_ts = 0.0
        self._trust_ok = False
        self._load()

    def _load(self):
        try:
            self._dead = set(json.loads(self.DEAD_FILE.read_text(encoding="utf-8")))
        except Exception:
            self._dead = set()

    def dead_set(self):
        """当前死链清单（硬过滤真源，供 agent.run(dead_asins=...)）。"""
        return self._dead

    def _ip_trusted(self):
        """控制商品探针：不 live → 当前 IP 被机器人页/限流挡，探测不可信。"""
        now = time.time()
        if now - self._trust_ts < self.CONTROL_TTL:
            return self._trust_ok
        self._trust_ok = (_probe(self.CONTROL_ASIN) == "live")
        self._trust_ts = now
        return self._trust_ok

    def verify(self, recs):
        """对推荐 asins 在线复核，返回本次新确认的死链 asins（并入清单 + 落盘）。
        recs 为空 / 探测不可信 / 无未验证款 → 秒回，零延迟。"""
        new_dead = set()
        if not recs:
            return new_dead
        now = time.time()
        todo = [r.get("asin") for r in recs[:self.TOP_N]
                if r.get("asin") and r["asin"] not in self._dead
                and self._live_ts.get(r["asin"], 0) < now - self.LIVE_TTL]
        if not todo or not self._ip_trusted():
            return new_dead
        with ThreadPoolExecutor(max_workers=min(4, len(todo))) as ex:
            futs = {ex.submit(_probe, a): a for a in todo}
            for fut, a in futs.items():
                try:
                    st = fut.result(timeout=self.PROBE_TIMEOUT + 1)
                except Exception:
                    st = "unknown"
                if st == "dead":
                    new_dead.add(a)
                    self._dead.add(a)
                elif st == "live":
                    self._live_ts[a] = now
        if new_dead:
            try:
                self.DEAD_FILE.write_text(
                    json.dumps(sorted(self._dead), ensure_ascii=False, indent=1),
                    encoding="utf-8")
            except Exception:
                pass  # 落盘失败不影响本次拦截
        return new_dead


class Harness:
    """驾驭层中间件：权限门 / 会话预算 / 护栏 / 埋点，包住 agent.run。"""

    def __init__(self, trace_path=None):
        self.trace_path = trace_path or TRACE_PATH
        self._sessions = {}   # uid -> {"queries": n, "llm": n, "t0": ts}
        self.links = LinkGuard()   # 死链拦截闸门（静态清单 + 运行时在线复核）

    # ------------------------------------------------------------------
    # ⑤ 全链路埋点：data/harness_trace.jsonl（只追加，绝不写坏既有行）
    # ------------------------------------------------------------------
    def _trace(self, entry):
        try:
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.trace_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # 埋点绝不影响主流程

    # ------------------------------------------------------------------
    # ① 工具拦截 + 权限门：不满足条件 → 在调用 agent 之前拦下
    # ------------------------------------------------------------------
    def gate(self, query):
        """返回 (ok, reason)。query 必须是合法字符串且有界。"""
        if query is None or not isinstance(query, str):
            return False, "query 必须是字符串"
        q = query.strip()
        if not q:
            return False, "query 不能为空"
        if len(q) > 500:
            return False, "query 过长（>500 字符，请精简后再试）"
        return True, ""

    # ------------------------------------------------------------------
    # ② 会话状态：计数器（查询次数 / LLM 触发次数 / 窗口）
    # ------------------------------------------------------------------
    def session_budget(self, uid):
        now = time.time()
        key = uid or "_anon"
        s = self._sessions.get(key)
        if s is None or now - s.get("t0", 0) > SESSION_WINDOW_SEC:
            s = {"queries": 0, "llm": 0, "t0": now}
            self._sessions[key] = s
        return s

    # ------------------------------------------------------------------
    # ③ 行为预算：预算感知路由。返回 (mode, warning)。
    #     LLM 超限 → 强制 rule（纯规则，确定性、零成本、更保守），并明说。
    #     查询超限 → 仍可处理（导购场景用户多问几句是正常的），只记账。
    # ------------------------------------------------------------------
    def pick_mode(self, uid, routing):
        s = self.session_budget(uid)
        if s["llm"] >= SESSION_MAX_LLM:
            return "rule", ("本会话 AI 深度分析次数已用完，已为您切换到轻量规则模式"
                            "（更保守，但依然按您的条件精确筛选）")
        return routing["mode"], None

    # ------------------------------------------------------------------
    # ④ 护栏栈
    # ------------------------------------------------------------------
    @staticmethod
    def coerce_num(v, default=None):
        """数据有效性：任何地方来的数字字段都强制 float，解析失败给 default——
        绝不让数字流进字符串函数（预算宕机根因：10.0 传给 .replace）。"""
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def medical_note(query, reply_lang):
        """医疗越界校验：命中治疗/用药语义 → 返回免责说明（附在回复外，不改 agent 正文）。"""
        q = str(query).lower()
        if any(k in q for k in MEDICAL_HINT):
            return MEDICAL_DISCLAIMER.get(reply_lang, MEDICAL_DISCLAIMER["en"])
        return None

    _EN_TAG = {  # 条件标签 → 英文（search_note 用；导购标签有界，词典兜底 .get 不崩）
        "混油": "combo-oily", "混干": "combo-dry", "油皮": "oily", "干皮": "dry",
        "敏感肌": "sensitive", "痘痘肌": "acne-prone", "混合肌": "combination", "中性": "normal",
        "水光": "dewy", "哑光": "matte", "自然": "natural", "光泽": "glow", "缎面": "satin",
        "高遮瑕": "full", "中度遮瑕": "medium", "轻遮瑕": "light",
        "液体": "liquid", "粉状": "powder", "乳霜": "cream", "棒状": "stick", "气垫": "cushion",
    }

    @classmethod
    def _cond_list(cls, rec, reply_lang="zh"):
        """把 constraints 拼成人话条件，供 search_note 展示「按什么筛的」。
        英文回复走英文标签（皮肤类型/妆效/质地等），不混中文。"""
        c = rec.get("constraints") or {}
        en = reply_lang == "en"
        tag = (lambda x: cls._EN_TAG.get(x, x)) if en else (lambda x: x)
        parts = []
        skins = (list(c.get("hard") or []) + list(c.get("soft") or []))
        if skins:
            parts.append(("skin=" if en else "肤质=") + "+".join(tag(s) for s in skins))
        if c.get("finish"):
            parts.append(("finish=" if en else "妆效=") + tag(c["finish"]))
        if c.get("coverage"):
            parts.append(("coverage=" if en else "遮瑕=") + tag(c["coverage"]))
        if c.get("form"):
            parts.append(("form=" if en else "质地=") + tag(c["form"]))
        if c.get("budget"):
            parts.append(("budget≤$" if en else "预算≤$") + f"{Harness.coerce_num(c['budget'], 0):.0f}")
        if c.get("shade_dir"):
            parts.append(("shade=" if en else "色号=") + ("fair" if c["shade_dir"] == "fair" else "dark"))
        if c.get("negative_axes"):
            parts.append(("avoid=" if en else "避雷=") + "+".join(c["negative_axes"]))
        return parts

    # ------------------------------------------------------------------
    # 主入口：权限门 → agent.run → 护栏（医疗）→ search_note → 埋点
    # ------------------------------------------------------------------
    def process(self, agent, query, uid=None, profile=None):
        t0 = time.time()
        trace_id = f"h-{int(t0 * 1000)}-{abs(hash(query)) % 100000}"

        ok, reason = self.gate(query)
        if not ok:
            self._trace({"trace_id": trace_id, "event": "gate_rejected", "uid": uid,
                         "query": query, "reason": reason,
                         "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            return {"error": reason, "trace_id": trace_id, "elapsed_ms": 0}

        s = self.session_budget(uid)
        try:
            # 死链拦截：静态清单（dead_asins.json）随首跑在检索层硬过滤（agent._retrieve）
            rec = agent.run(query, profile=profile, dead_asins=self.links.dead_set())
        except Exception as e:  # 兜住任何内部异常：埋点后上抛，web 层回 500 语义
            self._trace({"trace_id": trace_id, "event": "run_error", "uid": uid,
                         "query": query, "error": repr(e),
                         "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            raise
        s["queries"] += 1
        if rec.get("intent_source") == "llm":
            s["llm"] += 1

        # 运行时在线复核（LinkGuard.verify）：对推荐 top-N 轻量探测，发现新死链 →
        # 用更新后的清单重跑一次（回复文本重建，被拦款从推荐里消失）。最多重跑 1 轮，
        # 不无限迭代；探测不可信（IP 被机器人页挡）/无未验证款 → 秒回零延迟。
        # 重跑后仍可能引入新候选未验证——第二轮 verify 会再探，但不再重跑（边界可控）。
        dead_rerun = False
        for _ in range(2):
            new_dead = self.links.verify(rec.get("recommendations") or [])
            if not new_dead:
                break
            dead_rerun = True
            rec = agent.run(query, profile=profile, dead_asins=self.links.dead_set())
        if dead_rerun:
            s["queries"] += 1   # 重跑也算一次查询（防接口刷量口径）

        # search_note：检索真实发生 → 讲给用户（不神秘化）
        n_total = len(agent.idx.by_asin) if getattr(agent, "idx", None) else 0
        n_recs = len(rec.get("recommendations") or [])
        reply_lang = getattr(agent, "reply_lang", "zh")
        conds = self._cond_list(rec, reply_lang)
        if conds:
            cond_txt = ("（" + "、".join(conds) + "）") if reply_lang != "en" else (" (" + ", ".join(conds) + ")")
        else:
            cond_txt = ""
        if reply_lang == "en":
            search_note = (f"🔍 Found {n_recs} options matching your criteria{cond_txt}"
                           if n_recs else "🔍 No match yet — tell me more to refine.")
        else:
            search_note = (f"🔍 已按您的条件筛出 {n_recs} 款{cond_txt}"
                           if n_recs else "🔍 暂未找到合适款，补充信息我再帮您筛")

        medical = self.medical_note(query, reply_lang)

        self._trace({
            "trace_id": trace_id, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "uid": uid or "_anon", "query": query, "reply_lang": reply_lang,
            "gate": "ok", "budget": {"queries": s["queries"], "llm": s["llm"]},
            "intent_source": rec.get("intent_source"),
            "constraints": rec.get("constraints"),
            "ask": (rec.get("ask") or {}).get("decision"),
            "retry_triggered": bool((rec.get("retry") or {}).get("triggered")),
            "fallback": (rec.get("fallback") or {}).get("level"),
            "n_scanned": n_total, "n_recs": n_recs,
            "dead_rerun": dead_rerun,
            "route": rec.get("route"),   # 多路召回路由决策（Phase-MVP：channel + 各路召回数）
            "medical": bool(medical), "memory_applied": bool((rec.get("memory") or {}).get("applied")),
            "elapsed_ms": int((time.time() - t0) * 1000),
            "recommended_asins": [r.get("asin") for r in (rec.get("recommendations") or [])],
            "excluded_asins": [a.get("asin") for a in (rec.get("avoided") or [])],
        })
        return {"record": rec, "search_note": search_note, "medical_note": medical,
                "trace_id": trace_id, "elapsed_ms": int((time.time() - t0) * 1000)}


if __name__ == "__main__":
    # 自测：一条中文水光 + 一条医疗边界，直连 rule 实例（不走 web）
    from agent import GuideAgent
    h = Harness()
    ag = GuideAgent(intent_mode="rule", reply_lang="zh")
    for q in ["我是混油，要水光质地的粉底液", "我烂脸了，要祛痘的粉底液"]:
        r = h.process(ag, q, uid="u_test")
        print("=" * 60)
        print("query:", q)
        print("search_note:", r.get("search_note"))
        print("medical_note:", r.get("medical_note"))
        print("trace_id:", r.get("trace_id"))
