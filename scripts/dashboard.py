# -*- coding: utf-8 -*-
"""dashboard.py — 数据看板（企业级可观测，零依赖）
===============================================================================
读 `data/harness_trace.jsonl` 全链路埋点 → 聚合 → 生成自包含静态页
`data/dashboard.html`（双击浏览器打开，零第三方库）。

统计口径（对齐 harness._trace 字段）：
  - 总览：请求数 / 平均耗时 / P50·P90·P95 / LLM 触发 / 追问率 / 兜底率
  - 检索耗时分布：分桶直方图 + 慢查询 top-5
  - 路由通道分布：tagfirst vs semantic + 各召回路平均召回数（route 自 2026-09-01 起记录，
    旧行无该字段单独标注）
  - 决策分布：ask（no_ask/ask_all/ask_first/ask_shade_soft）/ fallback / retry
  - 意图来源 / 语言分布 / 预算与护栏（queries·llm / medical）
  - 热推商品 top-10（rec + products_clean.csv 标题映射）

事件行（gate_rejected / run_error）单独计拒绝/错误，不进主统计（缺字段）。

用法：python dashboard.py          # 生成 data/dashboard.html
      python dashboard.py --open   # 生成后自动开浏览器
"""
import io
import json
import sys
import time
import statistics
import webbrowser
from pathlib import Path

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")
TRACE = ROOT / "data" / "harness_trace.jsonl"
OUT = ROOT / "data" / "dashboard.html"
PRODUCTS = ROOT / "data" / "products_clean.csv"

TITLE = "beauty_agent · 全链路数据看板"


def load_traces():
    rows, events = [], []
    if not TRACE.exists():
        return rows, events
    for line in TRACE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line)
        except Exception:
            continue
        if t.get("event") in ("gate_rejected", "run_error"):
            events.append(t)
        else:
            rows.append(t)
    return rows, events


def _pct(values, q):
    if not values:
        return 0
    s = sorted(values)
    idx = min(len(s) - 1, int(len(s) * q))
    return s[idx]


def load_titles():
    """parent_asin → 标题（失败返回空 dict，只显示 asin）。"""
    if not PRODUCTS.exists():
        return {}
    import csv
    try:
        with open(PRODUCTS, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        return {r.get("parent_asin", ""): r.get("title", "") for r in rows}
    except Exception:
        return {}


# ------------------------------------------------------------------ 聚合 ----
def aggregate(rows):
    agg = {"total": len(rows)}
    times = [t.get("elapsed_ms", 0) for t in rows if isinstance(t.get("elapsed_ms"), (int, float))]
    agg["times"] = times
    agg["avg_ms"] = statistics.mean(times) if times else 0
    agg["p50"] = _pct(times, 0.5)
    agg["p90"] = _pct(times, 0.9)
    agg["p95"] = _pct(times, 0.95)

    # 路由通道（route 自 2026-09-01 起）
    channels = {}
    recalls = {"field": [], "text": [], "hot": [], "vector": [], "union": []}
    no_route = 0
    for t in rows:
        r = t.get("route")
        if not isinstance(r, dict) or "channel" not in r:
            no_route += 1
            continue
        c = r.get("channel")
        channels[c] = channels.get(c, 0) + 1
        for k in recalls:
            v = r.get(k)
            if isinstance(v, (int, float)):
                recalls[k].append(v)
    agg["channels"] = channels
    agg["no_route"] = no_route
    agg["recalls"] = {k: (statistics.mean(v) if v else 0) for k, v in recalls.items()}

    # 决策分布
    ask = {}
    for t in rows:
        a = t.get("ask") or "?"
        ask[a] = ask.get(a, 0) + 1
    agg["ask"] = ask
    agg["ask_ratio"] = (ask.get("ask_all", 0) + ask.get("ask_first", 0)) / max(1, len(rows))

    fallback = {}
    for t in rows:
        f = t.get("fallback") or "none"
        fallback[f] = fallback.get(f, 0) + 1
    agg["fallback"] = fallback
    agg["fallback_ratio"] = (fallback.get("honest_note", 0) + fallback.get("full", 0)) / max(1, len(rows))
    agg["retry"] = sum(1 for t in rows if t.get("retry_triggered"))

    intent = {}
    for t in rows:
        i = t.get("intent_source") or "none"
        intent[i] = intent.get(i, 0) + 1
    agg["intent"] = intent

    lang = {}
    for t in rows:
        l = t.get("reply_lang") or "?"
        lang[l] = lang.get(l, 0) + 1
    agg["lang"] = lang

    agg["llm_calls"] = sum((t.get("budget") or {}).get("llm", 0) for t in rows)
    agg["queries_budget"] = sum((t.get("budget") or {}).get("queries", 0) for t in rows)
    agg["medical"] = sum(1 for t in rows if t.get("medical"))

    # 强制降级会话数：LLM 预算累计 ≥ SESSION_MAX_LLM(20) 的会话 → pick_mode 强制纯规则。
    # 用现有 budget.llm 反推，零代码改动；真实演示通常不触顶，是诚实信号而非凑数。
    downgraded = set()
    for t in rows:
        llm = (t.get("budget") or {}).get("llm", 0)
        if llm >= 20:
            downgraded.add(t.get("uid"))
    agg["downgraded_sessions"] = len(downgraded)

    # 热推商品
    rec_count = {}
    for t in rows:
        for a in t.get("recommended_asins") or []:
            rec_count[a] = rec_count.get(a, 0) + 1
    agg["top_recs"] = sorted(rec_count.items(), key=lambda x: -x[1])[:10]

    # 时间范围
    ts = sorted(t.get("ts", "") for t in rows if t.get("ts"))
    agg["ts_first"], agg["ts_last"] = (ts[0], ts[-1]) if ts else ("", "")

    # 慢查询 top-5
    slow = sorted(rows, key=lambda t: -(t.get("elapsed_ms") or 0))[:5]
    agg["slow"] = [(t.get("elapsed_ms"), t.get("query", ""), t.get("reply_lang")) for t in slow if t.get("elapsed_ms")]
    return agg


# ------------------------------------------------------------------ 渲染 ----
# 设计语言：明亮风 SaaS 中后台（用户固定记忆 bright-saas-admin-design）——
# 浅紫蓝渐变背景 + 白色大圆角面板 + 蓝紫主色 #4F6BFF + 状态胶囊 + 纯 CSS/SVG 图表。
def bar(label, num, denom, color="#4f6bff", display=None, pill=None):
    pct = (num / denom * 100) if denom else 0
    d = display if display is not None else num
    p = f'<span class="pill {pill}">{label}</span>' if pill else f'<span>{label}</span>'
    return (f'<div class="row"><span class="lb">{p}</span>'
            f'<span class="bar"><span class="fill" style="width:{pct:.1f}%;background:{color}"></span></span>'
            f'<span class="vl">{d} · {pct:.0f}%</span></div>')


def hist_bars(agg):
    buckets = [["≤100ms", 0], ["100-500ms", 0], ["500ms-2s", 0], [">2s", 0]]
    for t in agg["times"]:
        if t <= 100:
            buckets[0][1] += 1
        elif t <= 500:
            buckets[1][1] += 1
        elif t <= 2000:
            buckets[2][1] += 1
        else:
            buckets[3][1] += 1
    denom = max(1, len(agg["times"]))
    return "".join(bar(f"{k}", v, denom, display=f"{v} 条") for k, v in buckets)


def render(agg, events, titles):
    total = agg["total"]
    css = """
      :root {
        --primary:#4F6BFF; --primary-hover:#3D57E6; --primary-light:#E8EDFF;
        --ink:#1A2233; --ink-2:#6B7280; --ink-3:#9CA3AF;
        --line:#EEF0F5; --bg-soft:#F8FAFF; --track:#F1F3F9;
        --success:#16A34A; --success-bg:#E7F8EF;
        --danger:#DC2626; --danger-bg:#FEE7E7;
        --warn:#F59E0B; --warn-bg:#FFF6E0;
        --info:#2563EB; --info-bg:#E8F0FE;
        --r-sm:8px; --r-md:12px; --r-lg:16px; --r-xl:24px;
        --shadow:0 20px 60px -20px rgba(95,108,255,.18), 0 8px 24px -8px rgba(95,108,255,.10);
      }
      * { box-sizing: border-box; margin: 0; padding: 0; }
      body { font-family: 'Inter','PingFang SC','Microsoft YaHei',system-ui,sans-serif;
             background: linear-gradient(135deg,#EEF1FF 0%,#F5F7FF 55%,#FAFAFE 100%);
             color: var(--ink); padding: 48px 24px 72px; min-height: 100vh; }
      .wrap { max-width: 1100px; margin: 0 auto; background:#fff;
              border-radius: var(--r-xl); box-shadow: var(--shadow);
              padding: 40px 44px 44px; }
      h1 { font-size: 30px; font-weight: 700; letter-spacing:.2px; }
      .sub { color: var(--ink-2); font-size: 14px; margin: 8px 0 28px; }
      .cards { display: grid; grid-template-columns: repeat(auto-fit,minmax(150px,1fr));
               gap: 14px; margin-bottom: 28px; }
      .card { background: var(--bg-soft); border: 1px solid var(--line);
              border-radius: var(--r-lg); padding: 18px 18px 16px;
              transition: box-shadow .2s ease-out; }
      .card:hover { box-shadow: 0 12px 28px -12px rgba(95,108,255,.25); }
      .card .k { font-size: 13px; color: var(--ink-2); margin-bottom: 8px; }
      .card .v { font-size: 34px; font-weight: 700; font-variant-numeric: tabular-nums;
                 line-height: 1.05; }
      .card .v small { font-size: 14px; color: var(--ink-3); font-weight: 400; }
      .card .sub2 { margin-top: 8px; font-size: 12.5px; color: var(--ink-3);
                    font-variant-numeric: tabular-nums; }
      section { background: #fff; border: 1px solid var(--line);
                border-radius: var(--r-lg); padding: 22px 24px; margin-bottom: 18px; }
      section h2 { font-size: 16px; font-weight: 600; margin-bottom: 6px; }
      section .note { color: var(--ink-2); font-size: 13px; margin-bottom: 18px; }
      .row { display: flex; align-items: center; gap: 12px; margin: 8px 0; font-size: 13.5px; }
      .lb { width: 178px; color: var(--ink); flex: none; display: flex; align-items: center; gap: 8px; }
      .pill { padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 500;
              white-space: nowrap; }
      .pill.info { background: var(--info-bg); color: var(--info); }
      .pill.success { background: var(--success-bg); color: var(--success); }
      .pill.warn { background: var(--warn-bg); color: #B45309; }
      .pill.muted { background: var(--track); color: var(--ink-3); }
      .pill.danger { background: var(--danger-bg); color: var(--danger); }
      .pill.brand { background: var(--primary-light); color: var(--primary); }
      .bar { flex: 1; background: var(--track); border-radius: 6px; height: 14px; overflow: hidden; }
      .fill { display: block; height: 100%; border-radius: 6px; }
      .vl { width: 118px; text-align: right; color: var(--ink-2); flex: none;
            font-size: 12.5px; font-variant-numeric: tabular-nums; }
      table { width: 100%; border-collapse: collapse; font-size: 13.5px; margin-top: 4px; }
      th { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--line);
           color: var(--ink-2); font-size: 12px; font-weight: 500; text-transform: uppercase;
           letter-spacing: .04em; }
      td { padding: 10px 12px; border-bottom: 1px solid var(--line); }
      tr:hover td { background: var(--bg-soft); box-shadow: inset 3px 0 0 var(--primary); }
      .mono { font-family: Consolas,'Courier New',monospace; font-size: 12.5px;
              color: var(--primary-hover); }
      .empty { color: var(--ink-3); font-size: 13px; padding: 12px 0; }
    """
    html = [f"<!DOCTYPE html><html lang=\"zh\"><head><meta charset=\"utf-8\">",
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
            f"<title>{TITLE}</title><style>{css}</style></head><body><div class=\"wrap\">",
            f"<h1>{TITLE}</h1>",
            f"<div class=\"sub\">数据源 data/harness_trace.jsonl · {agg['ts_first']} → {agg['ts_last']} · 生成 {time.strftime('%Y-%m-%d %H:%M:%S')}</div>"]

    q_ratio = agg["ask_ratio"] * 100
    fb_ratio = agg["fallback_ratio"] * 100
    ask_n = agg["ask"].get("ask_all", 0) + agg["ask"].get("ask_first", 0)
    fb_n = agg["fallback"].get("honest_note", 0) + agg["fallback"].get("full", 0)
    html += [f"<div class=\"cards\">",
             f"<div class=\"card\"><div class=\"k\">请求数</div><div class=\"v\">{total}</div><div class=\"sub2\">全链路埋点 · {agg['ts_first'][:10]} 起</div></div>",
             f"<div class=\"card\"><div class=\"k\">平均耗时</div><div class=\"v\">{agg['avg_ms']:.0f}<small> ms</small></div><div class=\"sub2\">P50 {agg['p50']}ms · P95 {agg['p95']}ms</div></div>",
             f"<div class=\"card\"><div class=\"k\">追问率</div><div class=\"v\">{q_ratio:.1f}<small> %</small></div><div class=\"sub2\">{ask_n} 次触发追问</div></div>",
             f"<div class=\"card\"><div class=\"k\">兜底率</div><div class=\"v\">{fb_ratio:.1f}<small> %</small></div><div class=\"sub2\">{fb_n} 次兜底</div></div>",
             f"<div class=\"card\"><div class=\"k\">AI 深度分析</div><div class=\"v\">{agg['llm_calls']}<small> 次</small></div><div class=\"sub2\">一次会话最多 20 次</div></div>",
             f"<div class=\"card\"><div class=\"k\">医疗免责</div><div class=\"v\">{agg['medical']}<small> 次</small></div><div class=\"sub2\">触到治病类话题 → 提示就医</div></div>",
             f"<div class=\"card\"><div class=\"k\">自动降级</div><div class=\"v\">{agg['downgraded_sessions']}<small> 会话</small></div><div class=\"sub2\">AI 次数用满 → 切轻量模式</div></div>",
             "</div>"]

    # 1 耗时
    html += ["<section><h2>① 检索耗时</h2>",
             f"<div class=\"note\">平均 {agg['avg_ms']:.0f}ms · P50 {agg['p50']}ms · P90 {agg['p90']}ms · P95 {agg['p95']}ms · 共 {len(agg['times'])} 条。P50=一半请求在此时间内完成；P90=90% 的请求在此时间内完成（超时不常见，若 P90 明显大于平均，说明有少数请求很慢）。</div>",
             hist_bars(agg)]
    if agg["slow"]:
        html += ["<table><tr><th>耗时</th><th>Query</th><th>语言</th></tr>"]
        for ms, q, l in agg["slow"]:
            html.append(f"<tr><td class=\"mono\">{ms} ms</td><td style=\"max-width:520px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap\">{q}</td><td>{l}</td></tr>")
        html.append("</table>")
    html.append("</section>")

    # 2 路由通道
    denom = max(1, total - agg["no_route"])
    html += ["<section><h2>② 检索方式</h2>",
             f"<div class=\"note\">看每轮需求是「按条件硬筛」还是「语义匹配」找商品：用户说清楚了肤质/预算/质地 → 按条件硬筛（精确优先）；只说了个大概 → 语义匹配。旧记录 {agg['no_route']} 条没有这个统计。</div>"]
    if agg["channels"]:
        html += [bar("按条件硬筛", agg["channels"].get("tagfirst", 0), denom, color="#4f6bff", display=f"{agg['channels'].get('tagfirst',0)} 次", pill="brand"),
                 bar("语义匹配", agg["channels"].get("semantic", 0), denom, color="#8c9eff", display=f"{agg['channels'].get('semantic',0)} 次", pill="muted")]
        html += ["<table><tr><th>检索通路</th><th>平均找出</th><th>说明</th></tr>",
                 f"<tr><td>按条件筛选</td><td>{agg['recalls']['field']:.0f}</td><td>符合肤质/预算/质地等条件的全部商品</td></tr>",
                 f"<tr><td>关键词匹配</td><td>{agg['recalls']['text']:.0f}</td><td>按描述词打分找出最相关的</td></tr>",
                 f"<tr><td>热门推荐</td><td>{agg['recalls']['hot']:.0f}</td><td>按评分和评价人数找出口碑好的</td></tr>",
                 f"<tr><td>语义联想</td><td>{agg['recalls']['vector']:.0f}</td><td>按意思相近找出（未启用=0，自动跳过）</td></tr>",
                 f"<tr><td>合在一起</td><td>{agg['recalls']['union']:.0f}</td><td>上面几路去重后的候选总数</td></tr></table>"]
    else:
        html.append("<div class=\"empty\">暂无统计——请跑几轮真实对话（web 演示）后重新生成看板。</div>")
    html.append("</section>")

    # 3 决策
    ask_label = {"no_ask": "直接推荐", "ask_all": "追问", "ask_first": "追问缺失项",
                 "ask_shade_soft": "软询问色号"}
    ask_pill = {"no_ask": "success", "ask_all": "info", "ask_first": "info",
                "ask_shade_soft": "warn"}
    fb_label = {"none": "正常", "honest_note": "诚实兜底", "full": "全兜底"}
    fb_pill = {"none": "muted", "honest_note": "warn", "full": "warn"}
    html += ["<section><h2>③ 每轮怎么处理</h2>",
             f"<div class=\"note\">信息不够 → 追问用户；AI 分析没把握 → 用保守兜底保证不崩、不发错推荐。运营重点看：追问率（高=用户需求没说清楚，可优化引导话术）、兜底率（高=AI 不稳定，建议查一下）。改写重试=AI 没答好重新答的次数。</div>"
             f"<div class=\"note\" style=\"margin-top:-6px\">追问率 {agg['ask_ratio']*100:.1f}% · 兜底率 {agg['fallback_ratio']*100:.1f}% · 改写重试 {agg['retry']} 次</div>",
             "<div style=\"margin-bottom:14px\">"]
    for k, v in sorted(agg["ask"].items(), key=lambda x: -x[1]):
        html.append(bar(ask_label.get(k, k), v, total, display=f"{v} 次",
                        pill=ask_pill.get(k, "muted")))
    html.append("</div><div>")
    for k, v in sorted(agg["fallback"].items(), key=lambda x: -x[1]):
        html.append(bar(fb_label.get(k, k), v, total, display=f"{v} 次",
                        pill=fb_pill.get(k, "muted")))
    html.append("</div></section>")

    # 4 语言与识别方式（内部字段全翻译成运营用语）
    lang_label = {"zh": "中文", "en": "英文", "other": "其他语言", "?": "未知"}
    intent_label = {"none": "规则直达（未触发 AI）", "rule": "规则识别", "llm": "AI 识别",
                    "cjk": "中文规则层"}
    html += ["<section><h2>④ 用户语言 & 识别方式</h2>",
             "<div class=\"note\">语言=用户用中文还是英文提问（运营看用户画像用）。识别方式=这轮需求怎么被理解的：没触发 AI 就是规则直达，触发 AI 就是 AI 识别——AI 识别越多成本越高，运营据此控制预算。</div>",
             "<div style=\"margin-bottom:14px\">"]
    for k, v in sorted(agg["lang"].items(), key=lambda x: -x[1]):
        html.append(bar(f"用户语言 · {lang_label.get(k, k)}", v, total, display=f"{v} 次"))
    html.append("</div><div>")
    for k, v in sorted(agg["intent"].items(), key=lambda x: -x[1]):
        html.append(bar(f"识别方式 · {intent_label.get(k, k)}", v, total, display=f"{v} 次"))
    html.append("</div></section>")

    # 5 预算与护栏
    html += ["<section><h2>⑤ 成本与安全护栏</h2>",
             "<div class=\"note\">导购不是无底洞：一次会话的提问次数和 AI 分析次数都有上限，用满自动降级成轻量规则（保证能继续服务、成本可控）；触到治病/用药类话题自动附「请以医生意见为准」提示（护肤品不越界当医疗建议）。</div>",
             "<table><tr><th>指标</th><th>累计</th><th>说明</th></tr>",
             f"<tr><td>提问次数上限</td><td>{agg['queries_budget']}</td><td>一次会话最多 100 次提问</td></tr>",
             f"<tr><td>AI 分析次数</td><td>{agg['llm_calls']}</td><td>一次会话最多 20 次，用满自动切轻量规则</td></tr>",
             f"<tr><td>自动降级会话</td><td>{agg['downgraded_sessions']}</td><td>AI 次数用满被降级的会话数</td></tr>",
             f"<tr><td>医疗免责</td><td>{agg['medical']}</td><td>触到治病/用药话题 → 附就医提示</td></tr></table>"]
    if events:
        rejected = sum(1 for e in events if e.get("event") == "gate_rejected")
        errored = sum(1 for e in events if e.get("event") == "run_error")
        html += [f"<div class=\"note\" style=\"margin-top:10px\">事件行：权限门拒绝 {rejected} · 运行错误 {errored}（未计入主统计）</div>"]
    html.append("</section>")

    # 6 热推
    html += ["<section><h2>⑥ 最常被推荐的 10 款</h2>",
             "<div class=\"note\">被导购推荐次数最多的商品（运营看主推方向：哪些款最容易被用户选中）。</div>",
             "<table><tr><th>#</th><th>商品 ID</th><th>标题</th><th>推荐次数</th></tr>"]
    if agg["top_recs"]:
        for i, (a, c) in enumerate(agg["top_recs"], 1):
            title = (titles.get(a) or "—").strip()
            if len(title) > 90:
                title = title[:90] + "…"
            html.append(f"<tr><td>{i}</td><td class=\"mono\">{a}</td><td style=\"max-width:560px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap\">{title}</td><td>{c}</td></tr>")
    else:
        html.append("<tr><td colspan=4 class=\"empty\">暂无推荐记录</td></tr>")
    html.append("</table></section>")

    html += ["</div></body></html>"]
    return "\n".join(html)


def main():
    rows, events = load_traces()
    if not rows and not events:
        print(f"[dashboard] 无数据：{TRACE} 为空或不存在。跑几轮对话后重试。")
        return 1
    agg = aggregate(rows)
    titles = load_titles()
    html = render(agg, events, titles)
    OUT.write_text(html, encoding="utf-8")
    print(f"[dashboard] 已生成 {OUT}（{len(rows)} 条主记录 + {len(events)} 条事件行）")
    if "--open" in sys.argv:
        webbrowser.open(OUT.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
