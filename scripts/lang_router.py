# -*- coding: utf-8 -*-
"""
lang_router.py — 多语种分层路由（方案1：英文规则 / 中文 hybrid / 其他语种翻译桥）
================================================================================
  英文 → 规则引擎（离线毫秒，零 LLM，确定性）
  中文 → hybrid（LLM 直抽意图，保留「有点干/不要太贵」这类模糊表达质量）
  其他语种（法语/阿拉伯语/俄语/西语…）→ LLM 翻译成英文 → 英文规则检索

铁律：**规则引擎永远是检索决策权威**。LLM 只当「语言桥」（翻译或意图直抽），
绝不替规则做检索决定 → 评测锚点（首答 94.7%、NDCG@5 0.547）不受影响。

语言检测是启发式（零依赖，字符范围 + 英文信号词），不做昂贵的大模型判语：
  含 CJK → zh；含阿拉伯/西里尔/希伯来/泰文 → other；
  拉丁脚本带变音（é/à/ç/ñ/ß…）→ other；
  其余拉丁脚本：英文功能词命中，或 ≥2 个英文化妆品内容词 → en；否则 other。
误判是优雅降级（规则抽空 → ask_all 反问），不崩。

用法：
  python lang_router.py --test "Je veux un fond de teint mat pour peau sèche"
    → 打印 detect_lang / 路由结果（mode + 翻译后的英文 query）
"""
import io
import re
import sys
from pathlib import Path

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_intent import translate_to_english  # noqa: E402

CJK = re.compile(r"[一-鿿]")
ARABIC = re.compile(r"[؀-ۿ]")
CYRILLIC = re.compile(r"[Ѐ-ӿ]")
HEBREW = re.compile(r"[֐-׿]")
THAI = re.compile(r"[฀-๿]")
# 拉丁脚本 + 变音符号 → 大概率非法语/西语/德语/意语/葡语…
ACCENT = re.compile(r"[àâäáçèéêëìíîïñòóôöùúûüßæœÿžš]")
# 英文「功能词」为主——化妆内容词容易与外语撞词（base/mat/cream…），功能词才硬。
# 法语「Je veux une base mat」不含 the/my/want → 不会误判英文。
EN_FUNC = re.compile(
    r"\b(the|and|my|your|want|need|please|looking|would|for|with|that|this|"
    r"have|you|under|over|doesnt|dont|im)\b", re.IGNORECASE)
# 英文「化妆品内容词」（与外文基本不撞；单个命中不充分，≥2 才采信英文）。
# 刻意排除 base（法语/西语「base」= 粉底）、mat（法语「mat」= 哑光）、
# cream（法语 crème）、skin 保留（法语 peau / 西语 piel / 德语 Haut 都不撞）。
EN_CONTENT = re.compile(
    r"\b(foundation|makeup|matte|oily|dry|dewy|coverage|budget|price|primer|"
    r"tint|sensitive|acne|shade|long[- ]?wear|waterproof|powder|skin)\b",
    re.IGNORECASE)


def detect_lang(query):
    """返回 'zh' | 'en' | 'other'。'other' = 需翻译成英文再走规则检索。"""
    q = query.strip()
    if not q:
        return "en"
    if CJK.search(q):
        return "zh"
    if ARABIC.search(q) or CYRILLIC.search(q) or HEBREW.search(q) or THAI.search(q):
        return "other"
    if ACCENT.search(q):
        return "other"
    if EN_FUNC.search(q):
        return "en"
    if len(EN_CONTENT.findall(q)) >= 2:
        return "en"
    return "other"


def route(query):
    """分层路由 → dict(mode, query, lang, translated)。

    - zh  → hybrid，原文照走（LLM 直抽意图）
    - en  → rule，原文照走（离线毫秒，零 LLM）
    - other → LLM 翻译成英文 → rule；翻译失败降级回 rule（抽空 → ask_all 反问）
    """
    query = str(query or "").strip()
    if not query:
        return {"mode": "rule", "query": "", "lang": "en", "translated": ""}
    lang = detect_lang(query)
    if lang == "zh":
        return {"mode": "hybrid", "query": query, "lang": "zh", "translated": ""}
    if lang == "other":
        en = translate_to_english(query)
        if en:
            return {"mode": "rule", "query": en, "lang": "other", "translated": en}
        return {"mode": "rule", "query": query, "lang": "other", "translated": ""}
    return {"mode": "rule", "query": query, "lang": "en", "translated": ""}


def _main():
    ap = __import__("argparse").ArgumentParser(description="多语种分层路由 · 单条调试")
    ap.add_argument("--test", help="单条 query 走 detect_lang → route")
    ap.add_argument("--detect-only", action="store_true", help="只打印 detect_lang，不调翻译桥")
    args = ap.parse_args()
    if not args.test:
        ap.error("需要 --test <query>")
    lang = detect_lang(args.test)
    print(f"Q: {args.test}")
    print(f"detect_lang = {lang}")
    if args.detect_only:
        return
    r = route(args.test)
    print(f"route → mode={r['mode']} lang={r['lang']}")
    if r["translated"]:
        print(f"  🌐 翻译桥 → {r['translated']}")
    print(f"  进入 Agent 的 query: {r['query']}")


if __name__ == "__main__":
    _main()
