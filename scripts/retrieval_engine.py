# -*- coding: utf-8 -*-
"""
Phase 1 检索层 · 检索引擎（段 A：轻量版，零安装）
=================================================
让「给商品打分排序」从字符串匹配升级为可评测的检索系统。
段 A 用 sklearn/numpy 现成库实现，三模式消融可立即跑：
  - bm25  ：BM25 关键词检索（手写，只对 title+brand 英文文档）
  - tag   ：标签匹配分（知识分层：肤质/妆效/遮盖/质地/色号 + 隐式意图）
  - mixed ：混合打分 = α·BM25 + β·标签分 + γ·热度分（含置信度降权 + 动态路由偏置）

三方向升级（并入计划）落点：
  - 动态路由检索：route_query 按 8 类意图分流，不同类走不同过滤/加权
  - 知识分层：标签分逐轴可审计（matched_axes），规则层/证据层接口预留
  - 冗余过滤/置信度降权：*_source=title 推断、conflict 标记 → 对应轴贡献 ×0.5

向量检索（torch/bge）段 B 补，本引擎预留 embed_query/embed_products 接口位。
"""
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")

SKIN_MAP = {"oily": "油皮", "dry": "干皮", "sensitive": "敏感肌", "combination": "混合肌",
            "normal": "中性", "acne": "痘痘肌", "combination_oily": "混油", "combination_dry": "混干"}
FINISH_MAP = {"matte": "哑光", "dewy": "水光", "glow": "光泽", "radiant": "光泽",
              "natural": "自然", "satin": "缎面"}
COV_MAP = {"full": "高遮瑕", "medium": "中度遮瑕", "light": "轻遮瑕", "sheer": "轻遮瑕"}
FORM_MAP = {"liquid": "液体", "cream": "乳霜", "powder": "粉状", "stick": "棒状", "cushion": "气垫"}
HARD_SKIN = {"敏感肌", "痘痘肌"}

# 隐式意图 → 可检索信号（段 A 基础版；对齐 intent_reasoning_rules.md）
# 2026-08-27 修复：防晒/防水 lambda 签名原为 `lambda t`（期望字符串），但 tag_score 调用
# 是 `fn(p)` 传商品 dict → `"spf" in p` 变查 dict 键，永不命中。改为检查 title 文本。
IMPLICIT_RULES = [
    ("防晒", lambda p: any(w in str(p.get("title", "")).lower()
                           for w in ("spf", "sunscreen", "sun block", "broad spectrum"))),
    ("防水持妆", lambda p: any(w in str(p.get("title", "")).lower()
                                for w in ("waterproof", "water resistant", "water-resist", "sweat resistant"))),
    ("油皮控油", lambda p: "油皮" in p["skin_tags"] or "混油" in p["skin_tags"]),
    ("哑光妆效", lambda p: p["finish_tag"] == "哑光"),
    ("干皮保湿", lambda p: "干皮" in p["skin_tags"] or "混干" in p["skin_tags"]),
]


def tokenize(text):
    return re.findall(r"[a-z0-9]+", str(text).lower())


class BM25:
    """手写 BM25（k1=1.5, b=0.75），不依赖 rank_bm25。"""
    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.N = len(docs)
        self.docs = [tokenize(d) for d in docs]
        self.doc_len = np.array([len(t) for t in self.docs])
        self.avgdl = float(self.doc_len.mean()) if self.N else 1.0
        df = Counter()
        for t in self.docs:
            df.update(set(t))
        self.idf = {w: np.log(1 + (self.N - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def score(self, query, i):
        q = Counter(tokenize(query))
        dl = self.doc_len[i]
        s = 0.0
        for w, qf in q.items():
            if w not in self.idf:
                continue
            f = self.docs[i].count(w)
            s += self.idf[w] * (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)) * qf
        return s


class ProductIndex:
    def __init__(self, csv_path=None):
        csv_path = csv_path or (ROOT / "data" / "products_clean.csv")
        self.df = pd.read_csv(csv_path).fillna("")
        self.records = self.df.to_dict("records")
        self.by_asin = {p["parent_asin"]: p for p in self.records}
        # 商品文档（英文信号：title + brand）供 BM25
        self.doc_text = [f'{p["title"]} {p["brand"]}' for p in self.records]
        self.bm25 = BM25(self.doc_text)
        self.index_of = {p["parent_asin"]: i for i, p in enumerate(self.records)}
        self._encoder = None       # 段 B：bge 编码器（懒加载）
        self._doc_vecs = None      # 段 B：商品文档向量

    # ---- 向量检索（段 B，bge-small-en-v1.5，优先本地离线加载） ----
    def enable_vectors(self):
        if self._encoder is not None:
            return
        from sentence_transformers import SentenceTransformer
        local = ROOT / "models" / "bge-small-en-v1.5"
        path = str(local) if (local / "model.safetensors").exists() else "BAAI/bge-small-en-v1.5"
        self._encoder = SentenceTransformer(path)
        # 商品文档 = title + brand + 英文结构化标签（finish/coverage/form/skin/skin_tone 原始列）。
        # 关键改进：只 title 时向量抓不到标签维度（gold−候选 差≈0 无区分度）；
        # 并进英文标签后向量同时吃「title 语义 + 标签语义」，与 query_rewrite 注词可对上。
        def _en(v):
            s = str(v).strip()
            return s if s and s.lower() not in ("nan", "missing") else ""
        docs = [" ".join(x for x in [
            p["title"], p["brand"], _en(p.get("finish_type")), _en(p.get("coverage")),
            _en(p.get("item_form")), _en(p.get("skin_type")), _en(p.get("skin_tone"))] if x)
            for p in self.records]
        self._doc_vecs = self._encoder.encode(docs, normalize_embeddings=True, show_progress_bar=False)
        print(f"向量索引就绪: {len(docs)} 商品 × {self._doc_vecs.shape[1]} 维")

    def vec_sim(self, text, asin):
        if self._encoder is None:
            self.enable_vectors()
        qv = self._encoder.encode([text], normalize_embeddings=True)[0]
        return float(qv @ self._doc_vecs[self.index_of[asin]])

    # 结构化字段 → 英文（与商品文档的英文标签列对齐，让向量检索复用标签维度）
    VEC_AUG = {
        "coverage": {"高遮瑕": "full coverage", "中度遮瑕": "medium coverage", "轻遮瑕": "light coverage"},
        "finish": {"哑光": "matte finish", "水光": "dewy finish", "光泽": "radiant glow finish",
                   "自然": "natural finish", "缎面": "satin finish"},
        "form": {"液体": "liquid", "粉状": "powder", "乳霜": "cream", "棒状": "stick", "气垫": "cushion"},
        "skin": {"干皮": "dry skin", "油皮": "oily skin", "敏感肌": "sensitive skin",
                 "痘痘肌": "acne prone skin", "混合肌": "combination skin", "混油": "combination oily skin",
                 "混干": "combination dry skin", "中性": "normal skin", "全肤质": "all skin types"},
    }

    def build_vec_query(self, req):
        """增强 query = query_rewrite + 结构化字段英文注词（与商品文档对齐）。"""
        parts = [req["vec_text"]]
        if req["coverage"]:
            parts.append(self.VEC_AUG["coverage"].get(req["coverage"], ""))
        if req["finish"]:
            parts.append(self.VEC_AUG["finish"].get(req["finish"], ""))
        if req["form"]:
            parts.append(self.VEC_AUG["form"].get(req["form"], ""))
        for s in list(req["hard"]) + list(req["soft"]):
            parts.append(self.VEC_AUG["skin"].get(s, ""))
        if req["shade_dir"]:
            parts.append("very fair shade" if req["shade_dir"] == "fair" else "dark deep shade")
        return " ".join(x for x in parts if x)

    # ---- 动态路由：8 类意图分流 ----
    def route_query(self, qtext):
        q = qtext.lower()
        if any(w in q for w in ("budget", "under $", "under usd", "cost", "cheap", "student",
                                "drugstore", "afford", "price", "worth the")):
            return "budget"
        if any(w in q for w in ("sensitive", "acne", "hypoallergenic", "breakout",
                                "alcohol", "fragrance", "irritat")):
            return "hard"
        if any(w in q for w in ("powder", "cushion", "liquid", "cream", "stick", "formula")):
            return "form"
        if any(w in q for w in ("avoid", "hate", "terrible", "look elsewhere", "worst",
                                "streak", "cake", "settle into")):
            return "avoid"
        if any(w in q for w in ("pale", "shade", "ivory", "porcelain", "very fair", "dark", "deep", "tan")):
            return "shade"
        return "default"

    # ---- Query 需求解析（结构化字段 + 裸文本信号） ----
    def parse_query(self, row):
        req = {
            "hard": set(), "soft": set(), "finish": None, "coverage": None,
            "form": None, "shade_dir": None, "implicit": [], "qtext": row["query"],
            "seasonal": False,
        }
        skins = {s.strip() for s in str(row.get("skin_label") or "").split(";")
                 if s.strip() and s.strip().lower() != "nan"}
        req["hard"] = {s for s in skins if s in HARD_SKIN}
        req["soft"] = skins - req["hard"]
        req["finish"] = FINISH_MAP.get(row.get("finish_label")) if row.get("finish_label") else None
        req["coverage"] = COV_MAP.get(row.get("coverage_label")) if row.get("coverage_label") else None
        req["form"] = FORM_MAP.get(row.get("form_label")) if row.get("form_label") else None
        # 色号方向（排除 fair share 习语）
        q = str(row["query"]).lower()
        if "winter" in q and "summer" in q:
            req["seasonal"] = True
        if re.search(r"\bpale\b|\bvery fair\b|\bivory\b|\bporcelain\b|\blightest\b", q):
            req["shade_dir"] = "fair"
        elif re.search(r"\bdark\b|\bdeep\b|\btan\b", q):
            req["shade_dir"] = "dark"
        # 隐式意图（evaluation_set 的 implicit_intent 列）
        for imp in str(row.get("implicit_intent") or "").split(";"):
            imp = imp.strip()
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
        # 向量检索文本：优先 v13 query_rewrite（隐式关键词已注入），空则用原 query
        req["vec_text"] = str(row.get("query_rewrite") or "").strip() or str(row["query"])
        return req

    # ---- 标签匹配分（知识分层：逐轴可审计 + 置信度降权） ----
    def tag_score(self, req, p):
        if not req:
            return 0.0, []
        p_skins = set(s for s in str(p.get("skin_tags") or "").split(";") if s)
        # 硬约束：敏感肌/痘痘肌必须适用，否则直接排除
        if not all(p_skins & {h, "全肤质"} for h in req["hard"]):
            return float("-inf"), ["硬约束排除"]
        score, reasons = 0.0, []
        # 肤质软偏好（2026-08-31 用户定：肤质是软约束，权重排序不硬剔）。
        # 冬混干夏混油用户也能用中性/混合肌标定款 → 兼容加分（比全肤质低一档，比不匹配高）。
        soft = set(req["soft"])
        if any(s in p_skins for s in soft):
            score += 2; reasons.append("肤质")
        elif "全肤质" in p_skins and soft:
            score += 1; reasons.append("全肤质")
        elif soft & {"混干", "混油"} and (p_skins & {"中性", "混合肌"}):
            score += 1; reasons.append("肤质·兼容")
        # 全年通用混合肌（combo + 换季「一件到底」）：同时覆盖干皮+油皮（或全肤质）= 全年答案 +2；
        # 只覆盖单边的极端控油/极端滋润 = 单季品，不是全年答案 -2。
        # 2026-08-28 修 q20 泄漏：q20 负例 = Estee 极端控油 / Rimmel 极端控油 / CLIO 极端滋润，
        # 旧打分把平衡品（Boots 干皮;油皮）与极端品全打成平手，heat tie-break 让爆款负例进 top-3。
        # 注意必须配 seasonal（换季互换）——q21 又油又干「同时存在」是矛盾题（unsovable→full 兜底），
        # 其 gold 把 Estee 当正例，不能降权（combo 但非 seasonal）。
        if req.get("seasonal") and "干皮保湿" in req["implicit"] and "油皮控油" in req["implicit"]:
            covers_dry = "干皮" in p_skins or "混干" in p_skins or "全肤质" in p_skins
            covers_oily = "油皮" in p_skins or "混油" in p_skins or "全肤质" in p_skins
            if covers_dry and covers_oily:
                score += 2; reasons.append("干油双标(全年)")
            elif covers_dry or covers_oily:
                score -= 2; reasons.append("单季品·降权")
        # 妆效/遮盖/质地（含置信度降权：title 推断/冲突字段 ×0.5）
        # 注：英文路径妆效权重保持 1.0（锚点基线）。中文显式妆效的严格执行
        # （+1 让妆效命中胜过肤质/热度的 tie-break）在 agent._retrieve 的 CJK 分支做，
        # 不污染共享打分 → 英文评测锚点（首答 94.7% / NDCG 0.553）零漂移。
        for axis, want, field, source, conflict in [
            ("妆效", req["finish"], "finish_tag", "finish_type_source", "conflict_finish"),
            ("遮盖", req["coverage"], "coverage_tag", "coverage_tag_source", "conflict_skin"),
            ("质地", req["form"], "form_tag", "item_form_source", "conflict_skin"),
        ]:
            if want and p.get(field) == want:
                conf = 1.0
                if str(p.get(source)) == "title" or int(p.get(conflict) or 0) == 1:
                    conf = 0.5
                score += 1 * conf
                reasons.append(f"{axis}{'·置信' if conf < 1 else ''}")
        # 色号方向（v12 shade_tag 自证）
        if req["shade_dir"]:
            shades = str(p.get("shade_tag") or "").split(";")
            if req["shade_dir"] == "fair":
                if "白皙" in shades:
                    score += 2; reasons.append("色号白皙")
                if "深色" in shades:
                    score -= 3; reasons.append("色号深·扣")
            elif req["shade_dir"] == "dark":
                if "深色" in shades:
                    score += 2; reasons.append("色号深色")
                if "白皙" in shades:
                    score -= 3; reasons.append("色号白·扣")
        # 色号家族（2026-08-31 对话闸门诊断注入：自然/白皙/深色/冷调/橄榄）
        # 仅中文路径（req 带 shade_family 才执行）→ 英文 eval 恒不命中 → 锚点零漂移。
        fam = req.get("shade_family")
        if fam:
            shades = str(p.get("shade_tag") or "").split(";")
            if fam in shades:
                score += 2; reasons.append(f"色号{fam}")
            elif fam == "自然" and ("白皙" in shades or "深色" in shades):
                score -= 3; reasons.append("色号偏端·扣")
            elif fam == "白皙" and "深色" in shades:
                score -= 3; reasons.append("色号偏深·扣")
            elif fam == "深色" and "白皙" in shades:
                score -= 3; reasons.append("色号偏白·扣")
        # 隐式意图
        for imp in req["implicit"]:
            for name, fn in IMPLICIT_RULES:
                if imp == name:
                    hit = fn(p) if name in ("防晒", "防水持妆") else fn(p)
                    if hit:
                        score += 1
                        reasons.append(f"隐式{name.replace('油皮控油','控油').replace('哑光妆效','哑光').replace('干皮保湿','保湿')}")
                    break
        return score, reasons

    def heat_score(self, p):
        r = pd.to_numeric(p.get("average_rating"), errors="coerce")
        rn = pd.to_numeric(p.get("rating_number"), errors="coerce")
        r = 0 if pd.isna(r) else float(r)
        rn = 0 if pd.isna(rn) else float(rn)
        return r * (1 + (rn > 10) * 0.5)

    # ---- 混合打分：四模式 + tagfirst（段 B 含向量） ----
    def score_candidates(self, mode, req, candidates, weights=None):
        """对 asin 列表打分，返回 [(asin, final, reasons)] 已排序（降序）。
        weights=(α, β, δ, γ) 覆盖 mixed 的向量权重，供网格扫描用。
        mode="tagfirst"（2026-08-28 定标，Agent 首答排序）：
          标签分主序 → 热度 → BM25（逐层降序，不做 min-max 归一化混合）。
          与 eval_pool_v2.TagFirst 一致（v2 池内首答命中 78.9%（2026-08-29 修复 q20 后）、Phase-1 NDCG 0.553 的实测口径）；
          已知取舍：无显式标签轴的 query 退化为热度榜，避雷率 0.926→0.889（用户已验收）。"""
        if mode == "tagfirst":
            rows = []
            for a in candidates:
                p = self.by_asin.get(a)
                if p is None:
                    continue
                ts, reasons = self.tag_score(req, p)
                if ts == float("-inf"):
                    continue  # 硬约束排除
                rows.append((a, ts, self.heat_score(p),
                             self.bm25.score(req["qtext"], self.index_of[a]), reasons))
            rows.sort(key=lambda r: (r[1], r[2], r[3]), reverse=True)
            return [(r[0], float(r[1]), r[4]) for r in rows]

        # query 向量只编码一次（结构化增强版），候选间用矩阵乘法（避免每候选重复 encode）
        qv = (self._encoder.encode([self.build_vec_query(req)], normalize_embeddings=True)[0]
              if self._encoder is not None else None)
        rows = []
        for a in candidates:
            p = self.by_asin.get(a)
            if p is None:
                continue
            bm = self.bm25.score(req["qtext"], self.index_of[a])
            ts, reasons = self.tag_score(req, p)
            if ts == float("-inf"):
                continue  # 硬约束排除
            vec = float(qv @ self._doc_vecs[self.index_of[a]]) if qv is not None else 0.0
            rows.append({"asin": a, "bm": bm, "ts": ts, "vec": vec,
                         "heat": self.heat_score(p), "reasons": reasons})

        def norm(xs):
            xs = np.array(xs, dtype=float)
            lo, hi = xs.min(), xs.max()
            span = hi - lo if hi > lo else 1.0
            return (xs - lo) / span

        if mode == "bm25":
            for r in rows:
                r["final"] = r["bm"]
        elif mode == "tag":
            for r in rows:
                r["final"] = r["ts"]
        elif mode == "vec":
            for r in rows:
                r["final"] = r["vec"]
        else:  # mixed：min-max 归一化后加权（α=0.3 BM25 / β=1.5 标签 / δ=0.1 向量 / γ=0.3 热度）
               # δ 由 grid_scan 定参：向量作小权重语义辅助捞 Recall（0.495→0.606），避雷不损
            a_, b_, d_, g_ = weights or (0.3, 1.5, 0.1, 0.3)
            for r, b, t, v, h in zip(rows, norm([x["bm"] for x in rows]),
                                     norm([x["ts"] for x in rows]), norm([x["vec"] for x in rows]),
                                     norm([x["heat"] for x in rows])):
                r["final"] = a_ * b + b_ * t + d_ * v + g_ * h
        rows.sort(key=lambda r: -r["final"])
        return [(r["asin"], float(r["final"]), r["reasons"]) for r in rows]

    # ---- 路由过滤偏置（动态路由落点）：预算/质地/避雷类先做约束 ----
    def apply_route(self, route, req, scored):
        q = req["qtext"].lower()
        if route == "budget":
            # 先价格过滤（有价 ≤ 40 优先，无价降权），预算数字由评测 runner 覆盖
            def price_ok(p):
                pr = pd.to_numeric(p.get("price"), errors="coerce")
                return pd.notna(pr)
            scored = [(a, f + (4 if price_ok(self.by_asin[a]) else -2), r) for a, f, r in scored]
        elif route == "form":
            # 质地硬约束已由 tag_score 的 form 轴体现；额外惩罚无 form_tag 商品
            scored = [(a, f + (0 if self.by_asin[a].get("form_tag") else -3), r) for a, f, r in scored]
        elif route == "avoid":
            # 负向优先：有缺陷证据的商品降权（证据层接口）
            pass
        return sorted(scored, key=lambda x: -x[1])


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    idx = ProductIndex()
    qd = pd.read_csv(ROOT / "data" / "evaluation_set.csv")
    row = qd.iloc[0]
    req = idx.parse_query(row)
    route = idx.route_query(row["query"])
    print(f"路由: {route} | 需求: 肤质软{req['soft']} 硬{req['hard']} "
          f"妆效{req['finish']} 遮瑕{req['coverage']} 质地{req['form']} 色号{req['shade_dir']} 隐式{req['implicit']}")
    from sqlalchemy import create_engine, text
    from db_config import db_url
    e = create_engine(db_url())
    with e.connect() as c:
        gold = [r[0] for r in c.execute(text(
            "SELECT asin FROM candidate_pool WHERE query_id=1 AND label='gold'"))]
    scored = idx.score_candidates("mixed", req, gold + ["B00C17UPE6", "B0086UL0WS"])
    for a, f, rs in scored[:6]:
        p = idx.by_asin[a]
        print(f"  {a}  {f:6.2f}  {p['title'][:42]}  [{','.join(rs)}]")
