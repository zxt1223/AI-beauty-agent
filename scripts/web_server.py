# -*- coding: utf-8 -*-
"""
web_server.py — beauty_agent AI 导购 · 零依赖 Web 前端（可运行演示 + 真实可用）
============================================================================
标准库 http.server，不装任何新依赖。常驻进程持有两个 GuideAgent 实例：
  rule   纯规则（确定性、毫秒级、离线零网络）
  hybrid 规则盲区（英文隐式意图 / 全部中文）→ DeepSeek LLM 兜底（key 只在 scripts/.env）

自动路由（前端不感知模式）：POST /api/chat 带 {query, user_id?}——中文 → hybrid，
英文/其他 → rule（其他先翻译成英文）。用户只管说需求。

多轮对话：前端把「原始需求 + User says: 回答」拼成完整 query 发来（对齐 agent.py --chat
的交互模式），后端无状态，只跑一次 run() 返回结构化 record。

跨会话用户记忆：user_id 匿名识别（前端 localStorage 生成），画像落 data/user_profiles.json——
语言偏好（重开界面/回复跟记忆语言）、肤质（本轮明说则覆盖记忆，未说则自动采用记忆肤质）、
时间感知问候（隔很久重开 → 前端拉画像出「还是以混油肤质为您推荐粉底液吗？」）。

用法：python web_server.py [--port 7860]   # 自动开浏览器 http://127.0.0.1:7860
"""
import io
import json
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

INDEX_HTML = ROOT / "web" / "index.html"
HOST = "127.0.0.1"
PORT = 7860
FEEDBACK = ROOT / "data" / "ui_feedback.jsonl"  # 用户反馈落盘（运行时零依赖，不写 MySQL）
PROFILE = ROOT / "data" / "user_profiles.json"  # 跨会话用户画像（匿名 userId 键控）
MAX_PROFILES = 100                              # 画像上限，超出按 last_visit 淘汰最久

# 多语种分层路由（方案1）：英文→规则 / 中文→hybrid / 其他语种→LLM 翻译成英文→规则。
# 前端不感知模式，用户只管说需求。lang_router.route 返回 (mode, query, lang, translated)。
from lang_router import route as lang_route  # noqa: E402

# 驾驭层（Harness）：所有 /api/chat 请求过这一层——权限门 → 会话预算（LLM 超限降级规则）
# → agent.run → 医疗越界护栏 → search_note → data/harness_trace.jsonl 全链路埋点。
from harness import Harness  # noqa: E402

_agents = {"rule_en": None, "hybrid_zh": None}   # 分层路由实际只用这两档
_lock = threading.Lock()
_harness = Harness()                            # 驾驭层单例（会话预算/埋点/护栏）


# ---------------------------------------------------------------------------
# 跨会话用户画像（data/user_profiles.json，_lock 内读写）
#   {uid: {"lang": "zh|en|None", "skins": [...], "last_visit": "...", "created": "..."}}
#   lang=最近一次回复语言（zh/en）；skins=用户明确说过的肤质（中文标签，最近一次显式声明覆盖）。
#   匿名 + 无 key/无敏感数据 → 用户画像数据层 + 匿名隐私。
# ---------------------------------------------------------------------------
def _load_profiles():
    if PROFILE.exists():
        try:
            return json.loads(PROFILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_profiles(profiles):
    try:
        PROFILE.parent.mkdir(parents=True, exist_ok=True)
        PROFILE.write_text(json.dumps(profiles, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    except Exception:
        pass


def _get_profile(uid):
    if not uid:
        return None
    return _load_profiles().get(uid)


def _touch(uid, **updates):
    """加载 → 建/改 → 淘汰超限 → 落盘，返回该用户最新画像。调用方须持 _lock。"""
    profiles = _load_profiles()
    p = profiles.get(uid)
    if p is None:
        p = {"lang": None, "skins": [],
             "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        profiles[uid] = p
    for k, v in updates.items():
        p[k] = v
    if len(profiles) > MAX_PROFILES:
        for old in sorted(profiles, key=lambda u: profiles[u].get("last_visit") or "")[:-MAX_PROFILES]:
            profiles.pop(old, None)
    _save_profiles(profiles)
    return p


def _same_lang(plang, qlang):
    """本轮 reply_lang 是否仍与记忆一致：记忆 zh → 仅 query 也是中文；
    记忆 en → query 非中文（en/other 都是英文回复）。不一致 = 用户切了语种。"""
    if plang == "zh":
        return qlang == "zh"
    return qlang != "zh"


def get_agent(mode, reply_lang="zh"):
    """懒加载：按 (mode, reply_lang) 建实例，启动时预建避免首问冷启动。
    分层路由只产出两档：中文 → hybrid_zh；英文/其他 → rule_en。"""
    key = f"{mode}_{reply_lang}"
    with _lock:
        if _agents.get(key) is None:
            from agent import GuideAgent
            _agents[key] = GuideAgent(intent_mode=mode, reply_lang=reply_lang)
        return _agents[key]


def get_gate():
    """对话意图闸门（llm_gate.route）。懒 import：只在多轮追问时引入，首答零开销。"""
    from llm_gate import route as gate_route
    return gate_route


def _store_convo(agent, query, convo, ai_text, diag_family=None):
    """压缩对话记忆（结构化，2026-08-31 用户定）：原始需求 + 抽取约束 + 最近 N 轮双方对话
    + 诊断色号家族。每轮落盘（user_profiles.json 的 convo 字段），换浏览器重开也记得。
    这是「联系上下文」的数据层：闸门/推荐器都从这取数，不再靠前端拼长字符串。"""
    user_all = str(query or "")
    orig = user_all.split("User says:")[0].strip()
    req = {}
    try:
        r, m = agent.extract_constraints(user_all)
        req = {"hard": sorted(r["hard"]), "soft": sorted(r["soft"]),
               "finish": r["finish"], "coverage": r["coverage"], "form": r["form"],
               "shade_dir": r["shade_dir"], "shade_family": r.get("shade_family"),
               "budget": r["budget"], "implicit": r["implicit"]}
    except Exception:
        pass
    recent = [dict(x) for x in (convo or []) if isinstance(x, dict)]
    recent.append({"r": "u", "t": user_all.rsplit("User says:", 1)[-1].strip()})
    recent.append({"r": "a", "t": str(ai_text)[:500]})
    return {"orig": orig, "req": req, "recent": recent[-6:],
            "diag_family": diag_family, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}


def handle_chat(payload):
    """POST /api/chat {query, user_id?} → 跑一次 Agent，返回结构化 record + 画像 + 记忆信息。

    多语种分层路由（前端不感知）：中文 → hybrid（LLM 直抽意图）；英文 → rule（离线秒回）；
    法语/阿拉伯语等其他语种 → LLM 翻译成英文 → 英文规则检索（语言桥）。
    跨会话用户记忆：带 user_id → 载入画像；reply_lang 跟用户已建立的语言；
    agent.run(q, profile=profile) 注入记忆肤质（无肤质词时自动采用）；本轮明说肤质 → 覆盖记忆。
    整轮加锁：画像 JSON 读写 + LLM 缓存文件写均非线程安全，串行化最稳。
    """
    query = str(payload.get("query") or "").strip()
    if not query:
        return {"error": "query 不能为空"}
    uid = str(payload.get("user_id") or "").strip() or None
    with _lock:
        profile = _get_profile(uid) if uid else None
        routing = lang_route(query)      # 语言桥（other → 翻译成英文）也在此锁内，防缓存写竞态
        mode, q = routing["mode"], routing["query"]
        # 语言记忆：多轮续答（User says:）沿用记忆语言（用户没新意图）；
        # 新意图 → query 与记忆一致沿用，不一致（用户切语种）跟随 query 并更新记忆。
        plang = (profile or {}).get("lang")
        is_followup = "User says:" in query
        if plang and (is_followup or _same_lang(plang, routing["lang"])):
            reply_lang = plang
        else:
            reply_lang = "zh" if routing["lang"] == "zh" else "en"
    # 行为预算（驾驭层③）：单会话 LLM 触发超限 → 降级纯规则模式（成本可控），并向用户明说。
    # 预算感知路由在此介入，get_agent 用最终 mode 建/取实例。
    mode, budget_warning = _harness.pick_mode(uid, routing)
    # get_agent 必须在锁外调用：它内部自带 with _lock（冷构建用），若在本函数外层锁内
    # 二次 acquire 非重入锁 → 同一线程死锁（曾导致 /api/chat 必挂）。预热后读缓存无需锁，
    # 冷构建走内部锁串行，两者都不与外层锁嵌套。
    agent = get_agent(mode, reply_lang)

    # ---- 对话记忆层（2026-08-31 用户定）：前端每轮传最近对话（用户+AI 双方）----
    # 闸门因此看得到「AI 上一条说了什么」→ 系统化识别「用户回答 AI 的提问」（诊断续答等），
    # 不再逐个加关键词。同时读取上次压缩记忆（原始需求/约束/诊断色号家族）供确认轮注入。
    convo = payload.get("convo") or []
    mem = ((profile or {}).get("convo") or {}) if uid else {}
    diag_family = mem.get("diag_family")

    # ---- 多轮追问 → 对话意图闸门（Harness「工具拦截 + 置信度分支」落地）----
    # 先听懂「这句话是什么」：只有「推荐/调整需求」才进商品库（走 agent.run）；
    # 对比/查色号/求助/闲聊 → LLM 应答 + 置信度门（>85 直出 / 60-85 人工复核 / <60 转人工）。
    # 闸门在锁外跑（LLM 秒级，不阻塞其他请求）；eval/contract 全英文首答不经过它 → 锚点零影响。
    if is_followup:
        gate = get_gate()(query, agent,
                          last_asins=payload.get("last_asins") or [],
                          reply_lang=reply_lang,
                          convo=convo, diag_family=diag_family)
        if gate is not None:
            if gate.get("kind") == "confirm_recommend":
                # 用户点头挑款 → 带诊断色号走商品库（原需求 + 色号家族），不返回闸门回执
                family = gate.get("shade_family")
                orig = query.split("User says:")[0].strip()
                if orig:
                    q = (orig + f"，色号{family}") if family else orig
                with _lock:
                    if uid:
                        _touch(uid, last_visit=time.strftime("%Y-%m-%d %H:%M:%S"),
                               convo=_store_convo(agent, query, convo,
                                                  gate.get("text") or "",
                                                  diag_family=family))
            else:
                with _lock:
                    if uid:
                        _touch(uid, last_visit=time.strftime("%Y-%m-%d %H:%M:%S"),
                               convo=_store_convo(agent, query, convo,
                                                  gate.get("text") or "",
                                                  diag_family=gate.get("shade_family")))
                return {
                    "gate": gate,
                    "mode": mode,
                    "lang": routing["lang"],
                    "translated": routing["translated"],
                    "profile": _get_profile(uid) if uid else None,
                    # 医疗越界免责对闸门轮同样生效（求助/闲聊也可能踩治疗语义）
                    "medical_note": _harness.medical_note(
                        query.rsplit("User says:", 1)[-1], reply_lang),
                    "budget_warning": budget_warning,
                }

    # 会话内色号记忆（2026-08-31 用户）：诊断出的色号家族像肤质记忆一样，后续推荐自动带上。
    # 仅中文路径生效（英文评测锚点零漂移）。跳过条件 = 本轮已有**具体**色号家族/方向
    # （色号自然/白皙/偏白/黄二白/冷调…）→ 听用户的；裸「色号」两字（如「怎么选择自己的色号」）
    # 不是家族陈述，不阻止注入；confirm_recommend 已带「色号自然」会被「色号自然」分支跳过。
    if (diag_family and reply_lang == "zh"
            and not re.search(r"色号(自然|白皙|深色|冷调|橄榄)|白皙|偏白|白皮|浅色|偏深|深色|"
                              r"黄[一二三]?白|暖调|冷调|橄榄|偏黄|偏自然", q)):
        q = f"{q}，色号{diag_family}"

    with _lock:
        # 驾驭层执行：权限门 → agent.run → 医疗护栏 → search_note → harness_trace.jsonl 埋点
        try:
            h = _harness.process(agent, q, uid=uid, profile=profile)
        except Exception as e:  # 服务不崩：任何异常都回 500 语义，前端兜住
            return {"error": f"服务内部错误：{e!r}", "mode": mode}
        if "error" in h:        # 权限门拦截（如 query 超长）——不调 agent，直接回
            return {"error": h["error"], "mode": mode}
        rec = h["record"]
        # 更新画像：本轮用户明确说的肤质覆盖记忆；新意图才更新语言记忆；
        # 对话记忆（压缩）每轮落盘：含软追问（AI 上一条问了什么，供下轮闸门看上下文）
        if uid:
            ai_text = str(rec.get("reply") or "")
            sq = (rec.get("soft_question") or {}).get("text")
            if sq:
                ai_text = (ai_text + "\n" + str(sq)).strip()
            # 色号家族记忆：本轮抽取到新家族（用户新说/注入生效）→ 覆盖记忆；
            # 否则沿用旧诊断家族（会话内自动带，2026-08-31 用户）。
            fam_fwd = ((rec.get("constraints") or {}).get("shade_family")
                       or (mem or {}).get("diag_family"))
            upd = {"last_visit": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "convo": _store_convo(agent, query, convo, ai_text, diag_family=fam_fwd)}
            if rec.get("skins_stated"):
                upd["skins"] = rec["skins_stated"]
            if not is_followup:
                upd["lang"] = reply_lang
            profile = _touch(uid, **upd)
    asked = rec["ask"]["decision"] in ("ask_all", "ask_first")
    return {
        "rec": rec,
        "mode": mode,
        "lang": routing["lang"],
        "translated": routing["translated"],
        "asked": asked,
        "elapsed_ms": h["elapsed_ms"],
        "profile": profile,
        "memory_applied": bool((rec.get("memory") or {}).get("applied")),
        # 驾驭层输出：搜索过程可见（search_note）+ 医疗越界免责 + 预算降级说明 + 埋点 id
        "search_note": h["search_note"],
        "medical_note": h["medical_note"],
        "budget_warning": budget_warning,
        "trace_id": h["trace_id"],
    }


def handle_profile(payload):
    """POST /api/profile {user_id, update:{lang?/skins?}} → 覆盖画像字段（如「不是，我肤质变了」清空 skins）。"""
    uid = str(payload.get("user_id") or "").strip() or None
    if not uid:
        return {"error": "缺少 user_id"}
    update = payload.get("update") or {}
    with _lock:
        profiles = _load_profiles()
        p = profiles.get(uid)
        if p is None:
            p = {"lang": None, "skins": [],
                 "created": time.strftime("%Y-%m-%d %H:%M:%S")}
            profiles[uid] = p
        for k in ("lang", "skins"):
            if k in update:
                p[k] = update[k]
        p["last_visit"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save_profiles(profiles)
        return {"ok": True, "profile": p}


def handle_feedback(payload):
    """POST /api/feedback {query, asins, reply, vote, note} → 追加 data/ui_feedback.jsonl。

    反馈闭环（AI Native 进化原生）：前端「这个推荐对吗 👍/👎」→ 运行时只落 JSONL
    （零依赖、不写 MySQL）；飞轮回灌由 scripts/feedback_to_eval.py 把 jsonl 转成
    MySQL 待审表（ui_feedback_review），人工确认后再回灌 gold —— 半自动，防噪声污染金标准。
    """
    query = str(payload.get("query") or "").strip()
    vote = str(payload.get("vote") or "").strip()
    if not query or vote not in ("up", "down"):
        return {"error": "参数不完整（query + vote ∈ up/down）"}
    rec = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "query": query,
        "asins": payload.get("asins") or [],
        "reply": str(payload.get("reply") or "")[:2000],
        "vote": vote,
        "note": str(payload.get("note") or "").strip()[:500],
    }
    try:
        with _lock:  # 与 chat 共用锁，避免并发写文件交错
            FEEDBACK.parent.mkdir(parents=True, exist_ok=True)
            with open(FEEDBACK, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        return {"error": f"反馈写入失败：{e!r}"}
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # 静默访问日志，终端干净

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            if INDEX_HTML.exists():
                self._send(200, INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, "index.html 未找到（应在 web/ 目录）")
        elif self.path == "/health":
            try:
                has_key = bool(__import__("llm_intent")._load_api_key())
            except Exception:
                has_key = False
            self._send(200, json.dumps(
                {"ok": True, "hybrid_key": has_key, "agents": list(_agents)}, ensure_ascii=False))
        elif self.path.startswith("/api/profile"):
            qs = parse_qs(urlparse(self.path).query)
            uid = (qs.get("user_id") or [""])[0]
            with _lock:  # 画像读写与 chat 共用锁，避免并发读写交错
                self._send(200, json.dumps(
                    {"ok": True, "profile": _get_profile(uid)}, ensure_ascii=False))
        else:
            self._send(404, "not found")

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._send(400, json.dumps({"error": "请求体不是合法 JSON"}, ensure_ascii=False))
            return
        if self.path == "/api/chat":
            result = handle_chat(payload)
        elif self.path == "/api/feedback":
            result = handle_feedback(payload)
        elif self.path == "/api/profile":
            result = handle_profile(payload)
        else:
            self._send(404, "not found")
            return
        self._send(200 if "error" not in result else 500,
                   json.dumps(result, ensure_ascii=False))


def main():
    ap = __import__("argparse").ArgumentParser(description="beauty_agent AI 导购 Web")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    srv = ThreadingHTTPServer((HOST, args.port), Handler)
    url = f"http://{HOST}:{args.port}"
    print("=" * 56)
    print("🛍️  beauty_agent AI 导购已启动")
    print(f"   地址：{url}")
    print("   模式：rule_en（英文/其他语种，规则+英文回复，离线秒回） /")
    print("         hybrid_zh（中文，LLM 直抽意图+中文回复）")
    print("   多语种：法语/阿拉伯语等 → 翻译成英文 → 英文规则检索，英文回复")
    print("   用户记忆：语言偏好 + 肤质画像（匿名 userId），隔久重开出记忆问候")
    print("   驾驭层：权限门 → 会话预算（LLM 超限降级规则）→ 医疗护栏 → search_note → 埋点")
    print("   停止：Ctrl+C")
    print("=" * 56)
    # 预建两个 Agent：索引构建（~秒级）放启动时，避免首问冷启动卡顿
    try:
        get_agent("rule", "en")
        get_agent("hybrid", "zh")
        print("   ✓ 商品索引已预热（rule_en / hybrid_zh）")
    except Exception as e:
        print(f"   ⚠ 预热失败：{e!r}（首问会现建）")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        srv.shutdown()


if __name__ == "__main__":
    main()
