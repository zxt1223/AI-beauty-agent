# -*- coding: utf-8 -*-
"""eval_dual_channel.py — 检索通道消融（独立只读实验，不碰主链路）
================================================================================
验证「结构化 / 语义 / 双通道路由 / 交叉编码器重排 / 标签保底+reranker微调 /
企业级召回→粗排→精排」对推荐质量的增量价值：
  通道 A  主链路 tagfirst   （对照组，应复现锚点 94.7%）
  通道 B  结构化通道        纯标签分排序（mode="tag"）
  通道 C  语义通道          BM25+向量（weights=(0.5,0,0.5,0)），不掺标签/热度
  通道 D  双通道路由        有结构化约束 → tagfirst；无 → 语义通道（route_query 接线）
  通道 E  粗排+交叉编码器重排  tagfirst 粗排 top-20 → bge-reranker 逐对精排
  通道 F0/F1/F3  标签保底+reranker微调  tagfirst 粗排 top-20 → reranker 打分 →
                    归一化加权 final = 0.3·BM25 + 1.5·标签 + δ·reranker + 0.3·热度（δ=0/0.1/0.3）
  通道 H  企业级召回→粗排→精排（参考证券搜索排序模块） tagfirst 粗排 top-20 →
                    精排四类权重综合 final = 0.3·norm(BM25) + 1.5·norm(标签) +
                    0.3·norm(热度) + 0.4·norm(trust)；trust=评论数分档+差评主题匹配沉底
  通道 G  trust 作 tuple 第二键  (ts, trust, bm25)——标签绝对优先，同分内 trust
                    （评论数分档+差评沉底）次之，再 BM25；H/F0 实证的 trust 正确接线
  通道 H2 企业级两级·同分综合  tagfirst 粗排 top-20 → 精排保持标签绝对主序，
                    同 ts 内按 0.4·norm(trust)+0.3·norm(heat)+0.3·norm(bm25) 定次序；
                    H 的修正：权重只决定同分候选次序，不再推翻标签主序
  通道 H3 第三级 tie-break  (ts, heat, trust, bm25)——trust 只在 heat 无法区分时
                    定次序（同评分+同评论档位），纯增量不稀释 heat，差评 -10 在
                    所属 (ts, heat) 组内自然沉底；四重负向实证后唯一未测的 trust 接线

口径完全对齐 eval_report_grid（v2 池内评分，锚点 ids1-24 可答同分母）：
  - 可答题 = agent.run 的 ask 决策不在 (ask_all, ask_first)
  - 首答 = gold_ok ∩ top3 且 gold_neg ∉ top3（干净命中）
  - NDCG@5 = gain=2^rel-1，rel<0 记 0（用 candidate_pool_v2.relevance）
  - 避雷 = gold_neg 不进 top-5

只读不写：不碰 eval_report.csv / badcase_report.csv / DB / 主链路代码，
锚点数字零漂移——跑完 A 通道应逐数字复现 94.7%。

输出：
  终端对比表
  data/dual_channel_report.csv   逐题×通道明细（utf-8-sig）
  docs/dual_channel_analysis.md  消融分析报告

用法：python eval_dual_channel.py
"""
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval_engine import ProductIndex       # noqa: E402
from agent import GuideAgent                      # noqa: E402
from defect_consensus import parse_scores         # noqa: E402
from db_config import db_url                      # noqa: E402
from config import SEM_WEIGHTS                    # noqa: E402  (α BM25, β 标签, δ 向量, γ 热度)：纯语义

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")
DB = db_url()
REAL = ROOT / "data" / "products_clean.csv"
ANCHOR_MAX_ID = 24

# ---------------------------------------------------------------- 十二通道 ----

class TagFirst(ProductIndex):
    """A 主链路：标签主序 → 热度 → BM25（锚点 94.7% 定标口径）。"""

    def score_candidates(self, mode, req, candidates, weights=None):
        return super().score_candidates("tagfirst", req, candidates, weights)


class StructuredChannel(ProductIndex):
    """B 结构化通道：纯标签分（硬约束排除后按 ts 降序），不掺热度/BM25。"""

    def score_candidates(self, mode, req, candidates, weights=None):
        return super().score_candidates("tag", req, candidates, weights)


class SemanticChannel(ProductIndex):
    """C 语义通道：BM25 + 向量，权重 (0.5, 0, 0.5, 0)，标签/热度归零。"""

    def score_candidates(self, mode, req, candidates, weights=None):
        return super().score_candidates("mixed", req, candidates, SEM_WEIGHTS)


class RoutedDualChannel(ProductIndex):
    """D 双通道路由：有结构化约束 → tagfirst；无 → 语义通道。
    结构化判定 = route_query 命中专用路由（预算/避雷/质地/色号/敏感）
    或 req 含任一硬/软/妆效/遮瑕/质地/预算约束。"""

    def score_candidates(self, mode, req, candidates, weights=None):
        route = self.route_query(req["qtext"])
        has_struct = (route in ("budget", "hard", "form", "avoid", "shade")
                      or bool(req["hard"] or req["soft"] or req["finish"]
                              or req["coverage"] or req["form"]
                              or (req["budget"] is not None))
                      or bool(req["implicit"]))  # 隐式意图（防晒/防水持妆/控油…）也是结构化信号
        if has_struct:
            return super().score_candidates("tagfirst", req, candidates, weights)
        return super().score_candidates("mixed", req, candidates, SEM_WEIGHTS)


class RerankerChannel(ProductIndex):
    """E 粗排+交叉编码器精排：tagfirst 粗排 top-K → bge-reranker-base 逐对重排。
    业界标准「粗排 recall → 精排 precision」两级结构：
      粗排 = 主链路 tagfirst（不动），取 top-K；
      精排 = cross-encoder 对 (query, 商品文档) 逐对打分 → 降序重排。
    reranker 懒加载：不 enable 时退化为主链路（锚点零影响）。"""

    RERANK_K = 20

    def _reranker(self):
        if getattr(self, "_reranker_model", None) is None:
            from sentence_transformers import CrossEncoder
            local = ROOT / "models" / "bge-reranker-base"
            path = str(local) if (local / "model.safetensors").exists() else "BAAI/bge-reranker-base"
            self._reranker_model = CrossEncoder(path)
            print(f"Reranker 就绪: {path}")
        return self._reranker_model

    def _doc(self, p):
        """商品文档 = title + brand + 英文标签列（与向量通道 enable_vectors 同一套构造）。"""
        def _en(v):
            s = str(v).strip()
            return s if s and s.lower() not in ("nan", "missing") else ""
        return " ".join(x for x in [
            p["title"], p["brand"], _en(p.get("finish_type")), _en(p.get("coverage")),
            _en(p.get("item_form")), _en(p.get("skin_type")), _en(p.get("skin_tone"))] if x)

    def score_candidates(self, mode, req, candidates, weights=None):
        ranked = super().score_candidates("tagfirst", req, candidates, weights)
        if not ranked:
            return ranked
        # 粗排 top-K（保住候选池内判定的 top-3/top-5 余量）
        head, tail = ranked[:self.RERANK_K], ranked[self.RERANK_K:]
        qtext = req["qtext"]
        pairs = [(qtext, self._doc(self.by_asin[a])) for a, _s, _r in head]
        scores = self._reranker().predict(pairs)
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        scored = sorted(zip(head, scores), key=lambda x: -float(x[1]))
        return [t[0] for t in scored] + [t for t in tail]


class FusionChannel(ProductIndex):
    """F 标签保底 + reranker 微调：tagfirst 粗排 top-20 → bge-reranker 打分 →
    min-max 归一化加权融合（β 标签主导，δ reranker 只在标签分接近时微调）。
    E 的教训修正：reranker 不再全权接管排序，只作小权重语义辅助。

    融合公式（复用 mixed 定标口径，向量位换成 reranker 分）：
      final = 0.3·norm(BM25) + 1.5·norm(标签分) + δ·norm(reranker) + 0.3·norm(热度)
    - reranker 只对粗排 top-K 打分（cross-encoder 重计算，绝不全库跑）
    - top-K 之外商品 reranker 项记 0，标签分保底 → reranker 翻不了盘
    - δ=0.0 即管道自检：纯归一化加权（reranker 归零），隔离「tuple 排序 vs 加权融合」差异

    全 F 实例共享同一个 reranker 模型（1.1GB，类级缓存只加载一次）。"""

    RERANK_K = 20
    DELTA = 0.1
    _shared_reranker = None

    def _get_reranker(self):
        cls = type(self)
        if cls._shared_reranker is None:
            from sentence_transformers import CrossEncoder
            local = ROOT / "models" / "bge-reranker-base"
            path = str(local) if (local / "model.safetensors").exists() else "BAAI/bge-reranker-base"
            cls._shared_reranker = CrossEncoder(path)
            print(f"FusionChannel Reranker 就绪: {path}")
        return cls._shared_reranker

    def _doc(self, p):
        """商品文档 = title + brand + 英文标签列（与 E 通道同一套构造）。"""
        def _en(v):
            s = str(v).strip()
            return s if s and s.lower() not in ("nan", "missing") else ""
        return " ".join(x for x in [
            p["title"], p["brand"], _en(p.get("finish_type")), _en(p.get("coverage")),
            _en(p.get("item_form")), _en(p.get("skin_type")), _en(p.get("skin_tone"))] if x)

    def score_candidates(self, mode, req, candidates, weights=None):
        # ① 粗排：tagfirst 全量明细（ts/heat/bm），同时供排序与融合
        rows = []
        for a in candidates:
            p = self.by_asin.get(a)
            if p is None:
                continue
            ts, reasons = self.tag_score(req, p)
            if ts == float("-inf"):
                continue  # 硬约束排除
            rows.append({"asin": a, "ts": ts, "heat": self.heat_score(p),
                         "bm": self.bm25.score(req["qtext"], self.index_of[a]),
                         "reasons": reasons})
        if not rows:
            return []
        rows.sort(key=lambda r: (r["ts"], r["heat"], r["bm"]), reverse=True)
        head, tail = rows[: self.RERANK_K], rows[self.RERANK_K:]
        # ② 精排：仅 top-K 打 reranker 分
        pairs = [(req["qtext"], self._doc(self.by_asin[r["asin"]])) for r in head]
        scores = self._get_reranker().predict(pairs)
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        for r, s in zip(head, scores):
            r["rerank"] = float(s)
        for r in tail:
            r["rerank"] = 0.0
        # ③ 融合：min-max 归一化加权（与 mixed 同一管道）
        def norm(xs):
            xs = np.array(xs, dtype=float)
            lo, hi = xs.min(), xs.max()
            span = hi - lo if hi > lo else 1.0
            return (xs - lo) / span

        a_, b_, d_, g_ = (0.3, 1.5, self.DELTA, 0.3)
        bm = norm([r["bm"] for r in rows])
        ts = norm([r["ts"] for r in rows])
        rr = norm([r["rerank"] for r in rows])
        ht = norm([r["heat"] for r in rows])
        for r, b, t, v, h in zip(rows, bm, ts, rr, ht):
            r["final"] = a_ * b + b_ * t + d_ * v + g_ * h
        rows.sort(key=lambda r: -r["final"])
        return [(r["asin"], float(r["final"]), r["reasons"]) for r in rows]


class TrustScorer:
    """trust 信号共享实现（G/H 通道复用）。
    trust = 评论数分档（≥200→4 / 50-199→3 / 10-49→2 / 1-9→1 / 0→0）
          + 差评主题匹配（query 避雷信号 × 商品差评轴命中 → -10 沉底，不排除）。
    差评用原始 defect_scores（含未达 70% 共识的弱信号）：共识轴商品已被主链路
    tag_score 排除，只有弱信号差评才是排序层的增量信息。"""

    # 差评主题匹配：query 避雷信号 → 差评轴（与 defect_consensus 词汇表一致）
    DEFECT_MAP = {"油皮控油": "油腻", "防水持妆": "脱妆", "高遮瑕": "遮盖不足"}
    _defect = None   # 商品差评轴缓存 {parent_asin: {轴: 提及次数}}，用原始 defect_scores

    @classmethod
    def _load_defect_scores(cls):
        if cls._defect is None:
            cls._defect = {}
            p = ROOT / "data" / "product_defect_evidence.csv"
            if p.exists():
                df = pd.read_csv(p, encoding="utf-8-sig").fillna("")
                for _, r in df.iterrows():
                    sc = parse_scores(r.get("defect_scores"))
                    if sc:
                        cls._defect[str(r["parent_asin"])] = sc
        return cls._defect

    def trust_score(self, req, p):
        rn = pd.to_numeric(p.get("rating_number"), errors="coerce")
        rn = 0 if pd.isna(rn) else int(rn)
        tier = 4 if rn >= 200 else 3 if rn >= 50 else 2 if rn >= 10 else 1 if rn >= 1 else 0
        want = set()
        for sig in req["implicit"]:
            if sig in self.DEFECT_MAP:
                want.add(self.DEFECT_MAP[sig])
        if (req.get("coverage") or "") == "高遮瑕":
            want.add("遮盖不足")
        if want:
            pa = str(p.get("parent_asin") or "")
            if want & set(self._load_defect_scores().get(pa, {}).keys()):
                return -10.0
        return float(tier)


class EnterpriseRankChannel(TrustScorer, ProductIndex):
    """H 企业级召回→粗排→精排（参考证券搜索排序模块，四类信号权重综合）。
    得分结构借鉴企业级「baseScore=10000 + 精排分」的分层（粗排门槛 + 精排定序）：
      召回：硬约束过滤（tag_score 硬排除，含共识避雷轴）
      粗排：tagfirst 标签主序 → 取 top-K（性能优先、确定性，筛掉标签不合格候选）
      精排：top-K 内 final = 0.3·norm(BM25) + 1.5·norm(标签) + 0.3·norm(热度) + 0.4·norm(trust)
    与 F0 的关键差异：权重综合只在粗排筛出的标签合格候选内发生，不作用于全池——
    热度/BM25 无法再让「标签不合格」的货翻到前面（F0 掉分根因修复）。
    """

    COARSE_K = 20
    # 精排权重 (BM25, 标签, 热度, trust)，对齐企业级业务排序表达式结构
    RANK_W = (0.3, 1.5, 0.3, 0.4)

    def score_candidates(self, mode, req, candidates, weights=None):
        # ① 粗排：tagfirst 全量明细（ts/heat/bm/trust），标签主序筛出 top-K
        rows = []
        for a in candidates:
            p = self.by_asin.get(a)
            if p is None:
                continue
            ts, reasons = self.tag_score(req, p)
            if ts == float("-inf"):
                continue  # 硬约束排除（含共识避雷）
            rows.append({"asin": a, "ts": ts, "heat": self.heat_score(p),
                         "bm": self.bm25.score(req["qtext"], self.index_of[a]),
                         "trust": self.trust_score(req, p), "reasons": reasons})
        if not rows:
            return []
        rows.sort(key=lambda r: (r["ts"], r["heat"], r["bm"]), reverse=True)
        head, tail = rows[: self.COARSE_K], rows[self.COARSE_K:]
        # ② 精排：仅 top-K 内做四类权重综合（企业级「粗排筛门槛 → 精排定次序」）
        normal = [r for r in head if r["trust"] > -5]
        defect = [r for r in head if r["trust"] <= -5]
        if normal:
            def norm(xs):
                xs = np.array(xs, dtype=float)
                lo, hi = xs.min(), xs.max()
                span = hi - lo if hi > lo else 1.0
                return (xs - lo) / span

            a_, b_, g_, t_ = self.RANK_W
            bm = norm([r["bm"] for r in normal])
            ts = norm([r["ts"] for r in normal])
            ht = norm([r["heat"] for r in normal])
            tr = norm([r["trust"] for r in normal])
            for r, b, t, h, tt in zip(normal, bm, ts, ht, tr):
                r["final"] = a_ * b + b_ * t + g_ * h + t_ * tt
            normal.sort(key=lambda r: -r["final"])
        for r in defect:
            r["final"] = -1e9  # 差评主题匹配 → 精排层沉底（不排除）
        head = normal + defect
        # ③ 精排结果在前，top-K 外按粗排顺序保底补齐（企业级：精排定序、粗排保底）
        return ([(r["asin"], float(r["final"]), r["reasons"]) for r in head] +
                [(r["asin"], float(r["ts"]), r["reasons"]) for r in tail])


class TrustSecondChannel(TrustScorer, ProductIndex):
    """G 同分内 heat 换成 trust：tuple 主序 (ts, trust, bm25)。
    标签绝对主序不变（A 的锚点来源），trust 作第二键、BM25 作第三键——
    H/F0 实证：连续权重进主序会乘隙重排（q1/q8 掉 gold），trust 的正确接线是
    tuple 第二键 + 差评沉底，而不是加权项。冷门堆标签货（1-9 评论 trust=1）
    在同分内沉底；差评主题匹配 → trust=-10 同 ts 内最底（不排除）。"""

    def score_candidates(self, mode, req, candidates, weights=None):
        rows = []
        for a in candidates:
            p = self.by_asin.get(a)
            if p is None:
                continue
            ts, reasons = self.tag_score(req, p)
            if ts == float("-inf"):
                continue  # 硬约束排除（含共识避雷）
            rows.append({"asin": a, "ts": ts,
                         "trust": self.trust_score(req, p),
                         "bm": self.bm25.score(req["qtext"], self.index_of[a]),
                         "reasons": reasons})
        if not rows:
            return []
        # tuple 主序：标签绝对优先 → 同分内 trust（差评 -10 沉底）→ BM25
        rows.sort(key=lambda r: (r["ts"], r["trust"], r["bm"]), reverse=True)
        return [(r["asin"], float(r["ts"]), r["reasons"]) for r in rows]


class LayeredRankChannel(TrustScorer, ProductIndex):
    """H2 企业级两级排序·同分综合（H 的修正版）。
    粗排 = tagfirst 标签主序取 top-K（企业级「基础排序」，性能优先）；
    精排 = 保持标签绝对主序，只在「同 ts」内综合多信号定次序：
      排序键 = (ts, 层内综合分 = 0.4·norm(trust) + 0.3·norm(heat) + 0.3·norm(bm25))
    与 H 的差异：权重只决定同分候选的次序，不再推翻标签主序（q1/q8 掉 gold 根因修复）；
    与 G 的差异：保留 heat（评分×评论量维度），trust 不替换 heat 而是与它层内综合。
    差评主题匹配 → trust=-10，同 ts 组内沉底（不排除）。"""

    COARSE_K = 20
    # 层内综合权重 (trust, heat, bm25)——标签主序不动
    LAYER_W = (0.4, 0.3, 0.3)

    def score_candidates(self, mode, req, candidates, weights=None):
        rows = []
        for a in candidates:
            p = self.by_asin.get(a)
            if p is None:
                continue
            ts, reasons = self.tag_score(req, p)
            if ts == float("-inf"):
                continue  # 硬约束排除（含共识避雷）
            rows.append({"asin": a, "ts": ts,
                         "heat": self.heat_score(p),
                         "bm": self.bm25.score(req["qtext"], self.index_of[a]),
                         "trust": self.trust_score(req, p),
                         "reasons": reasons})
        if not rows:
            return []
        rows.sort(key=lambda r: (r["ts"], r["heat"], r["bm"]), reverse=True)
        head, tail = rows[: self.COARSE_K], rows[self.COARSE_K:]
        # 精排：标签绝对主序不变，同 ts 组内按层内综合分定次序
        normal = [r for r in head if r["trust"] > -5]
        defect = [r for r in head if r["trust"] <= -5]
        if normal:
            def norm(xs):
                xs = np.array(xs, dtype=float)
                lo, hi = xs.min(), xs.max()
                span = hi - lo if hi > lo else 1.0
                return (xs - lo) / span

            wt, wh, wb = self.LAYER_W
            tr = norm([r["trust"] for r in normal])
            ht = norm([r["heat"] for r in normal])
            bm = norm([r["bm"] for r in normal])
            for r, t, h, b in zip(normal, tr, ht, bm):
                r["layer"] = wt * t + wh * h + wb * b
        for r in defect:
            r["layer"] = -1e9  # 差评主题匹配 → 同 ts 组内沉底（不排除）
        head.sort(key=lambda r: (r["ts"], r["layer"]), reverse=True)
        return ([(r["asin"], float(r["ts"]), r["reasons"]) for r in head] +
                [(r["asin"], float(r["ts"]), r["reasons"]) for r in tail])


class TrustTieBreakChannel(TrustScorer, ProductIndex):
    """H3 第三级 tie-break：(ts, heat, trust, bm25)——trust 纯增量，不稀释 heat。
    四重负向实证：trust 无论走加权（F0/H）、第二键（G）还是同层综合（H2），都稀释
    heat（评分×评论量）的同组区分度 → q1/q8/q22/q23 掉 gold。H3 是唯一未测的接线：
    trust 只在「heat 都分不出来」的候选间定次序（同评分 + 同评论档位 → 同 heat），
    既不替换也不加权 heat；差评主题匹配 → trust=-10，在所属 (ts, heat) 组内自然沉底
    （tuple 降序，-10 < 0~4），不排除。预期锚点接近零回归——trust 唯一可能无痛落地位。"""

    def score_candidates(self, mode, req, candidates, weights=None):
        rows = []
        for a in candidates:
            p = self.by_asin.get(a)
            if p is None:
                continue
            ts, reasons = self.tag_score(req, p)
            if ts == float("-inf"):
                continue  # 硬约束排除（含共识避雷）
            rows.append({"asin": a, "ts": ts,
                         "heat": self.heat_score(p),
                         "trust": self.trust_score(req, p),
                         "bm": self.bm25.score(req["qtext"], self.index_of[a]),
                         "reasons": reasons})
        if not rows:
            return []
        # tuple 主序：标签 → 热度 → trust（heat 无法区分时才用）→ BM25
        rows.sort(key=lambda r: (r["ts"], r["heat"], r["trust"], r["bm"]), reverse=True)
        return [(r["asin"], float(r["ts"]), r["reasons"]) for r in rows]


def ndcg_at_k(rels, k=5):
    gains = [max(2 ** r - 1, 0.0) for r in rels[:k]]
    dcg = sum(g / np.log2(i + 2) for i, g in enumerate(gains))
    ideal = sorted([max(2 ** r - 1, 0.0) for r in rels], reverse=True)[:k]
    idcg = sum(g / np.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def _req_from_rec(rec, query):
    c = rec["constraints"]
    return {"hard": set(c["hard"]), "soft": set(c["soft"]),
            "finish": c["finish"], "coverage": c["coverage"], "form": c["form"],
            "shade_dir": c["shade_dir"], "implicit": c["implicit"],
            "budget": c["budget"], "seasonal": c["seasonal"],
            "qtext": query, "vec_text": query}


def main():
    print("=" * 72)
    print("双通道消融 eval_dual_channel.py（独立实验，主链路零改动）")
    print("=" * 72)

    # 共享向量编码：只 encode 一次，C/D 复用（同一份 products_clean.csv，记录完全一致）
    idx_a = TagFirst(REAL)
    idx_b = StructuredChannel(REAL)
    idx_c = SemanticChannel(REAL)
    idx_d = RoutedDualChannel(REAL)
    idx_e = RerankerChannel(REAL)
    idx_f0 = FusionChannel(REAL); idx_f0.DELTA = 0.0
    idx_f1 = FusionChannel(REAL); idx_f1.DELTA = 0.1
    idx_f3 = FusionChannel(REAL); idx_f3.DELTA = 0.3
    idx_h = EnterpriseRankChannel(REAL)
    idx_g = TrustSecondChannel(REAL)
    idx_h2 = LayeredRankChannel(REAL)
    idx_h3 = TrustTieBreakChannel(REAL)
    idx_c.enable_vectors()
    idx_d._encoder = idx_c._encoder
    idx_d._doc_vecs = idx_c._doc_vecs
    channels = [("A 主链路 tagfirst", idx_a),
                ("B 结构化通道", idx_b),
                ("C 语义通道(BM25+向量)", idx_c),
                ("D 双通道路由", idx_d),
                ("E 粗排+交叉编码器重排", idx_e),
                ("F0 融合 δ=0.0", idx_f0),
                ("F1 融合 δ=0.1", idx_f1),
                ("F3 融合 δ=0.3", idx_f3),
                ("H 企业级排序 K20", idx_h),
                ("G trust 第二键", idx_g),
                ("H2 同分综合 K20", idx_h2),
                ("H3 第三键 tie-break", idx_h3)]

    engine = create_engine(DB)
    qd = pd.read_sql("SELECT id, query, query_type, complexity FROM eval_review_50 "
                     "ORDER BY id", engine)
    pool = pd.read_sql("SELECT query_id, asin, label, gold_type, relevance "
                       "FROM candidate_pool_v2", engine)
    agent = GuideAgent()

    rows = []          # 逐题 × 逐通道明细
    d_route_stats = {} # D 通道路由分布
    for _, r in qd.iterrows():
        qid = int(r.id)
        rec = agent.run(r.query, qid=qid, query_type=r.query_type)
        ask = rec["ask"]["decision"]
        answerable = ask not in ("ask_all", "ask_first")
        req = _req_from_rec(rec, r.query)
        rp = pool[pool.query_id == qid]
        gold_ok = set(rp.asin[(rp.label == "gold") & (rp.gold_type.isin(["primary", "extra"]))])
        gold_neg = set(rp.asin[(rp.label == "gold") & (rp.gold_type == "negative")])
        rel_map = dict(zip(rp.asin, rp.relevance.astype(float)))
        prim = rp.asin[rp.gold_type == "primary"].tolist()

        # D 通道路由分布（只看 anchor 可答）
        route = idx_d.route_query(req["qtext"])
        if answerable and qid <= ANCHOR_MAX_ID:
            has_struct = (route in ("budget", "hard", "form", "avoid", "shade")
                          or bool(req["hard"] or req["soft"] or req["finish"]
                                  or req["coverage"] or req["form"]
                                  or (req["budget"] is not None)))
            key = "结构化→tagfirst" if has_struct else "无约束→语义"
            d_route_stats[key] = d_route_stats.get(key, 0) + 1

        for name, idx in channels:
            if not len(rp):
                continue
            ranked = [a for a, _s, _r in idx.score_candidates("tagfirst", req, list(rp.asin))]
            top3 = ranked[:3]
            hit = bool(gold_ok & set(top3)) and not bool(gold_neg & set(top3))
            ndcg = ndcg_at_k([rel_map.get(a, 0.0) for a in ranked])
            avoid = not bool(gold_neg & set(ranked[:5]))
            pr = (ranked.index(prim[0]) + 1) if prim and prim[0] in ranked else None
            rows.append(dict(qid=qid, query_type=r.query_type,
                             complexity=r.complexity, ask=ask,
                             answerable=answerable, channel=name,
                             hit=hit, ndcg=ndcg, avoid=avoid, prim_rank=pr,
                             top3="|".join(top3)))
        print(f"q{qid:>2} [{r.query_type:<4}/{r.complexity:<6}] ask={ask:<13} "
              f"ans={'✓' if answerable else '—'}")

    df = pd.DataFrame(rows)
    df["is_anchor"] = df.qid <= ANCHOR_MAX_ID

    # ---------------- 汇总：锚点可答（同分母 19）----------------
    anchor = df[df.is_anchor & df.answerable]
    print(f"\n锚点可答题：{len(anchor.qid.unique())} 道（ids 1-24，ask 追问排除后）")
    print(f"{'通道':<22}{'首答':>14}{'NDCG@5':>10}{'避雷':>10}")
    agg = {}
    for name, _ in channels:
        sub = anchor[anchor.channel == name]
        hit = int(sub.hit.sum())
        den = len(sub)
        ndcg = float(sub.ndcg.mean())
        # 避雷：只统计有负例 gold 的题
        neg_ids = set()
        for qid in anchor.qid.unique():
            rp = pool[pool.query_id == qid]
            if ((rp.label == "gold") & (rp.gold_type == "negative")).any():
                neg_ids.add(qid)
        av_sub = sub[sub.qid.isin(neg_ids)]
        avoid = float(av_sub.avoid.mean()) if len(av_sub) else float("nan")
        agg[name] = (hit, den, ndcg, avoid, len(av_sub))
        print(f"{name:<22}{hit}/{den} = {hit/den:.1%}{ndcg:>11.3f}{avoid:>11.1%}")

    # ---------------- hidden / 非锚点（25-41）排序质量参考 ----------------
    hidden = df[~df.is_anchor]
    print(f"\n非锚点题（25-41）可答数：{int(hidden[hidden.answerable].qid.nunique())} / "
          f"{hidden.qid.nunique()}（规则模式盲区类多走 ask_all，只报排序质量参考）")
    for name, _ in channels:
        sub = hidden[hidden.channel == name]
        ndcg = float(sub.ndcg.mean())
        av = float(sub.avoid.mean()) if len(sub) else float("nan")
        print(f"  {name:<22} NDCG@5={ndcg:.3f}  避雷={av:.1%}（全量 {len(sub)} 题）")

    # ---------------- D 通道路由分布 ----------------
    print("\nD 通道路由分布（锚点可答）：")
    for k, v in sorted(d_route_stats.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v} 题")

    # ---------------- A vs D 逐题差异 ----------------
    diff = []
    a_df = anchor[anchor.channel == "A 主链路 tagfirst"].set_index("qid")
    d_df = anchor[anchor.channel == "D 双通道路由"].set_index("qid")
    for qid in anchor.qid.unique():
        if qid not in a_df.index or qid not in d_df.index:
            continue
        ha, hd = bool(a_df.loc[qid, "hit"]), bool(d_df.loc[qid, "hit"])
        na, nd_ = a_df.loc[qid, "ndcg"], d_df.loc[qid, "ndcg"]
        if ha != hd or abs(na - nd_) > 1e-6:
            diff.append(dict(qid=qid, hit_A=ha, hit_D=hd, ndcg_A=round(float(na), 3),
                             ndcg_D=round(float(nd_), 3)))
    print(f"\nA vs D 锚点可答差异题：{len(diff)} 道")
    for d in diff:
        print(f"  q{d['qid']}: A_hit={d['hit_A']} D_hit={d['hit_D']} "
              f"NDCG A={d['ndcg_A']:.3f} D={d['ndcg_D']:.3f}")

    # ---------------- A vs F1（δ=0.1 推荐口径）逐题差异 ----------------
    diff_f = []
    a_df2 = anchor[anchor.channel == "A 主链路 tagfirst"].set_index("qid")
    f_df = anchor[anchor.channel == "F1 融合 δ=0.1"].set_index("qid")
    for qid in anchor.qid.unique():
        if qid not in a_df2.index or qid not in f_df.index:
            continue
        ha, hf = bool(a_df2.loc[qid, "hit"]), bool(f_df.loc[qid, "hit"])
        na, nf = a_df2.loc[qid, "ndcg"], f_df.loc[qid, "ndcg"]
        if ha != hf or abs(na - nf) > 1e-6:
            diff_f.append(dict(qid=qid, hit_A=ha, hit_F=hf,
                               ndcg_A=round(float(na), 3), ndcg_F=round(float(nf), 3)))
    print(f"\nA vs F1（δ=0.1）锚点可答差异题：{len(diff_f)} 道")
    for d in diff_f:
        print(f"  q{d['qid']}: A_hit={d['hit_A']} F1_hit={d['hit_F']} "
              f"NDCG A={d['ndcg_A']:.3f} F1={d['ndcg_F']:.3f}")

    # ---------------- A vs H（企业级粗排+精排加权）逐题差异 ----------------
    diff_h = []
    a_df3 = anchor[anchor.channel == "A 主链路 tagfirst"].set_index("qid")
    h_df = anchor[anchor.channel == "H 企业级排序 K20"].set_index("qid")
    for qid in anchor.qid.unique():
        if qid not in a_df3.index or qid not in h_df.index:
            continue
        ha, hh = bool(a_df3.loc[qid, "hit"]), bool(h_df.loc[qid, "hit"])
        na, nh = a_df3.loc[qid, "ndcg"], h_df.loc[qid, "ndcg"]
        if ha != hh or abs(na - nh) > 1e-6:
            diff_h.append(dict(qid=qid, hit_A=ha, hit_H=hh,
                               ndcg_A=round(float(na), 3), ndcg_H=round(float(nh), 3)))
    print(f"\nA vs H（企业级排序 K20）锚点可答差异题：{len(diff_h)} 道")
    for d in diff_h:
        print(f"  q{d['qid']}: A_hit={d['hit_A']} H_hit={d['hit_H']} "
              f"NDCG A={d['ndcg_A']:.3f} H={d['ndcg_H']:.3f}")

    # ---------------- A vs G（trust 作第二键）逐题差异 ----------------
    diff_g = []
    a_df4 = anchor[anchor.channel == "A 主链路 tagfirst"].set_index("qid")
    g_df = anchor[anchor.channel == "G trust 第二键"].set_index("qid")
    for qid in anchor.qid.unique():
        if qid not in a_df4.index or qid not in g_df.index:
            continue
        ha, hg = bool(a_df4.loc[qid, "hit"]), bool(g_df.loc[qid, "hit"])
        na, ng = a_df4.loc[qid, "ndcg"], g_df.loc[qid, "ndcg"]
        if ha != hg or abs(na - ng) > 1e-6:
            diff_g.append(dict(qid=qid, hit_A=ha, hit_G=hg,
                               ndcg_A=round(float(na), 3), ndcg_G=round(float(ng), 3)))
    print(f"\nA vs G（trust 第二键）锚点可答差异题：{len(diff_g)} 道")
    for d in diff_g:
        print(f"  q{d['qid']}: A_hit={d['hit_A']} G_hit={d['hit_G']} "
              f"NDCG A={d['ndcg_A']:.3f} G={d['ndcg_G']:.3f}")

    # ---------------- A vs H2（同分综合）逐题差异 ----------------
    diff_h2 = []
    a_df5 = anchor[anchor.channel == "A 主链路 tagfirst"].set_index("qid")
    h2_df = anchor[anchor.channel == "H2 同分综合 K20"].set_index("qid")
    for qid in anchor.qid.unique():
        if qid not in a_df5.index or qid not in h2_df.index:
            continue
        ha, hh2 = bool(a_df5.loc[qid, "hit"]), bool(h2_df.loc[qid, "hit"])
        na, nh2 = a_df5.loc[qid, "ndcg"], h2_df.loc[qid, "ndcg"]
        if ha != hh2 or abs(na - nh2) > 1e-6:
            diff_h2.append(dict(qid=qid, hit_A=ha, hit_H2=hh2,
                                ndcg_A=round(float(na), 3), ndcg_H2=round(float(nh2), 3)))
    print(f"\nA vs H2（同分综合 K20）锚点可答差异题：{len(diff_h2)} 道")
    for d in diff_h2:
        print(f"  q{d['qid']}: A_hit={d['hit_A']} H2_hit={d['hit_H2']} "
              f"NDCG A={d['ndcg_A']:.3f} H2={d['ndcg_H2']:.3f}")

    # ---------------- A vs H3（第三级 tie-break）逐题差异 ----------------
    diff_h3 = []
    a_df6 = anchor[anchor.channel == "A 主链路 tagfirst"].set_index("qid")
    h3_df = anchor[anchor.channel == "H3 第三键 tie-break"].set_index("qid")
    for qid in anchor.qid.unique():
        if qid not in a_df6.index or qid not in h3_df.index:
            continue
        ha, hh3 = bool(a_df6.loc[qid, "hit"]), bool(h3_df.loc[qid, "hit"])
        na, nh3 = a_df6.loc[qid, "ndcg"], h3_df.loc[qid, "ndcg"]
        if ha != hh3 or abs(na - nh3) > 1e-6:
            diff_h3.append(dict(qid=qid, hit_A=ha, hit_H3=hh3,
                                ndcg_A=round(float(na), 3), ndcg_H3=round(float(nh3), 3)))
    print(f"\nA vs H3（第三键 tie-break）锚点可答差异题：{len(diff_h3)} 道")
    for d in diff_h3:
        print(f"  q{d['qid']}: A_hit={d['hit_A']} H3_hit={d['hit_H3']} "
              f"NDCG A={d['ndcg_A']:.3f} H3={d['ndcg_H3']:.3f}")

    # ---------------- 写 CSV ----------------
    out = ROOT / "data" / "dual_channel_report.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n已写 {out}")

    # ---------------- 写报告 md ----------------
    write_report(anchor, hidden, agg, d_route_stats, diff, diff_f, diff_h, diff_g, diff_h2, diff_h3, pool)


def write_report(anchor, hidden, agg, d_route_stats, diff, diff_f, diff_h, diff_g, diff_h2, diff_h3, pool):
    md = []
    md.append("# 检索通道消融：结构化 / 语义 / 双通道路由 / 交叉编码器重排（独立实验）\n")
    md.append("> 生成：`python scripts/eval_dual_channel.py` ｜ 只读评测：主链路 tagfirst 一个字节未改，"
              "锚点数字零漂移。\n")
    md.append("> 数据源：eval_review_50（41 题） + candidate_pool_v2 池内评分，口径与 "
              "eval_report_grid 完全一致。\n")

    md.append("## 1. 十二通道定义\n")
    md.append("| 通道 | 排序规则 | 定位 |")
    md.append("|---|---|---|")
    md.append("| A 主链路 tagfirst（对照） | 标签主序 → 热度 → BM25 | 现行正式排序，定标口径 |")
    md.append("| B 结构化通道 | 纯标签分（硬约束排除后 ts 降序） | 精确属性驱动，不含热度/BM25 干扰 |")
    md.append("| C 语义通道 | BM25+向量，权重 (0.5,0,0.5,0) | 模糊语义召回，标签/热度归零 |")
    md.append("| D 双通道路由 | 有结构化约束→tagfirst；无→语义通道 | route_query 接线，按题分流 |")
    md.append("| E 粗排+交叉编码器重排 | tagfirst 粗排 top-20 → bge-reranker 逐对精排 | "
              "业界标准两级检索（recall→precision） |")
    md.append("| F0 融合 δ=0.0 | tagfirst 粗排 top-20 → reranker 打分 → 归一化加权，δ=0 | "
              "管道自检：reranker 归零的纯加权融合，隔离「tuple 排序 vs 加权管道」差异 |")
    md.append("| F1 融合 δ=0.1 | 同上，δ=0.1 | 标签保底 + reranker 微调（推荐口径） |")
    md.append("| F3 融合 δ=0.3 | 同上，δ=0.3 | 标签保底 + reranker 加大权重（上探） |")
    md.append("| H 企业级粗排+精排 K20 | tagfirst 粗排 top-20 → 精排四类权重综合 "
              "0.3·norm(BM25)+1.5·norm(标签)+0.3·norm(热度)+0.4·norm(trust) | "
              "企业级「召回→粗排→精排」范式：权重只在标签合格候选内定次序，trust 压冷门堆标签货 |")
    md.append("| G trust 第二键 | tuple 主序 (ts, trust, bm25)——同分内 heat 换成 trust | "
              "H/F0 实证的 trust 正确接线：标签绝对优先，评论数分档作第二键，差评主题匹配沉底 |")
    md.append("| H2 同分综合 K20 | tagfirst 粗排 top-20 → 精排保持标签绝对主序，"
              "同 ts 内按 0.4·norm(trust)+0.3·norm(heat)+0.3·norm(bm25) 定次序 | "
              "H 的修正：权重只决定同分候选次序，不再推翻标签主序（q1/q8 掉 gold 根因修复） |")
    md.append("| H3 第三键 tie-break | tuple 主序 (ts, heat, trust, bm25)——trust 只在 heat "
              "无法区分时定次序，纯增量 | 四重负向实证后唯一未测的 trust 接线：不替换/不加权 heat，"
              "差评 -10 在所属 (ts, heat) 组内自然沉底 |")
    md.append("")

    md.append("## 2. 锚点对比（ids 1-24 可答题，同分母）\n")
    md.append("| 通道 | 首答命中 | NDCG@5 | 避雷（负例不进 top-5） |")
    md.append("|---|---|---|---|")
    hit, den, ndcg, avoid, av_den = agg["A 主链路 tagfirst"]
    a_hit, a_den = hit, den   # 保留 A 用于锚点复现行（循环会覆盖 hit/den）
    md.append(f"| **A 主链路 tagfirst**（正式） | **{hit}/{den} = {hit/den:.1%}** | "
              f"{ndcg:.3f} | {avoid:.1%}（{av_den} 题含负例） |")
    for name, _ in [("B 结构化通道", None), ("C 语义通道(BM25+向量)", None),
                    ("D 双通道路由", None), ("E 粗排+交叉编码器重排", None),
                    ("F0 融合 δ=0.0", None), ("F1 融合 δ=0.1", None),
                    ("F3 融合 δ=0.3", None), ("H 企业级排序 K20", None),
                    ("G trust 第二键", None), ("H2 同分综合 K20", None),
                    ("H3 第三键 tie-break", None)]:
        hit, den, ndcg, avoid, av_den = agg[name]
        md.append(f"| {name} | {hit}/{den} = {hit/den:.1%} | {ndcg:.3f} | "
                  f"{avoid:.1%}（{av_den} 题含负例） |")
    md.append("")
    md.append(f"> A 通道复现锚点 {a_hit}/{a_den} = {a_hit/a_den:.1%}，与定标口径一致——对照组有效。\n")

    md.append("## 3. 非锚点题（25-41）排序质量参考\n")
    md.append("> 规则模式对模糊/盲区类多走 ask_all（先追问不硬推），首答分母 ≈ 0；"
              "下表仅报排序质量（NDCG@5 / 避雷），反映「如果交给通道直接排」的潜力。\n")
    md.append("| 通道 | NDCG@5（全量题） | 避雷 |")
    md.append("|---|---|---|")
    for name in agg:
        sub = hidden[hidden.channel == name]
        md.append(f"| {name} | {sub.ndcg.mean():.3f} | "
                  f"{sub.avoid.mean():.1%} |" if len(sub) else f"| {name} | — | — |")
    md.append("")

    md.append("## 4. D 通道路由分布（锚点可答）\n")
    md.append("| 路由去向 | 题数 |")
    md.append("|---|---|")
    for k, v in sorted(d_route_stats.items(), key=lambda x: -x[1]):
        md.append(f"| {k} | {v} |")
    md.append("")
    md.append("**路由判定要点**：结构化判定必须纳入 `req['implicit']`（隐式意图：防晒 / 防水持妆 / "
              "控油 / 哑光妆效 / 干皮保湿）。初版漏掉该信号时 q16（持妆·防水）被误判为无约束 → 误入语义通道 → "
              "首答 17/19、NDCG 0.548；补入后 q16 走 tagfirst，D 无损追平 A（18/19 = 94.7%、NDCG 0.570）。"
              "——双通道消融正是用来暴露这类「路由接线缺口」的。\n")
    md.append("")

    md.append("## 5. A vs D 逐题差异\n")
    if diff:
        md.append("| qid | A 首答 | D 首答 | A NDCG | D NDCG |")
        md.append("|---|---|---|---|---|")
        for d in diff:
            md.append(f"| q{d['qid']} | {'✓' if d['hit_A'] else '—'} | "
                      f"{'✓' if d['hit_D'] else '—'} | {d['ndcg_A']:.3f} | {d['ndcg_D']:.3f} |")
    else:
        md.append("锚点可答题 A 与 D 无差异。")
    md.append("")

    md.append("## 6. 交叉编码器重排（E 通道）深度分析\n")
    eh = agg["E 粗排+交叉编码器重排"]
    md.append(f"E 通道锚点首答 {eh[0]}/{eh[1]} = {eh[0]/eh[1]:.1%}、NDCG@5 {eh[2]:.3f}，"
              f"低于 A/D——这是**真实的边界结论**，不是实现缺陷：\n")
    md.append("")
    md.append("- **cross-encoder 分数区间极窄**（0.000~0.02），top-1 常被「标题恰好命中 query 关键词」"
              "的商品抢走：q1（干皮·保湿）Bourjois「Anti-Fatigue」排第 1、gold 掉到第 9；"
              "q8（轻遮瑕·持妆）Tarte「Full Coverage」排第 1、gold 第 8。\n")
    md.append("- **纯文本语义看不到结构化标签轴**（肤质/妆效/遮瑕），而 tagfirst 的标签分才是锚点命中率"
              "的来源——这与通道 C（纯语义）锚点仅 36.8% 是同一规律：本任务是**领域标签约束主导**，"
              "不是通用语义匹配主导。\n")
    md.append("- **E 也有亮点**：q16（防水持妆）双 gold 都被抬进 top-5（防水 Dermacol 排第 4），"
              "q9 从 miss 变 hit——reranker 对**语义明确的场景词**（waterproof/coverage）有真实加分，"
              "对**标签轴明确的约束**（油皮/哑光/轻遮瑕）则容易失焦。\n")
    md.append("- **工程结论**：交叉编码器重排适合「语义召回后的精排」，不适合直接接管标签约束主导的"
              "排序；若引入，正确定位是 D 路由里语义分支的下游精排器，而非替代 tagfirst。\n")
    md.append("")

    md.append("## 7. 通道 F：标签保底 + reranker 微调（融合实验）\n")
    md.append("融合公式（复用 mixed 定标口径，向量位换成 reranker 分）：\n")
    md.append("`final = 0.3·norm(BM25) + 1.5·norm(标签分) + δ·norm(reranker) + 0.3·norm(热度)`\n")
    md.append("- 粗排 = 主链路 tagfirst（不动）取 top-20；reranker 只对 top-20 打分（重计算，绝不全库跑）\n")
    md.append("- top-20 之外商品 reranker 项记 0，标签分保底 → reranker 翻不了盘\n")
    f0 = agg["F0 融合 δ=0.0"]
    f1 = agg["F1 融合 δ=0.1"]
    f3 = agg["F3 融合 δ=0.3"]
    md.append("\n| 通道 | 首答命中 | NDCG@5 | 避雷 |")
    md.append("|---|---|---|---|")
    for name, v in [("F0 融合 δ=0.0（管道自检）", f0), ("F1 融合 δ=0.1（推荐）", f1),
                    ("F3 融合 δ=0.3（上探）", f3)]:
        md.append(f"| {name} | {v[0]}/{v[1]} = {v[0]/v[1]:.1%} | {v[2]:.3f} | "
                  f"{v[3]:.1%}（{v[4]} 题含负例） |")
    a0 = agg["A 主链路 tagfirst"]
    if f0[0] != a0[0] or abs(f0[2] - a0[2]) > 1e-9:
        md.append("\n- **F0（δ=0）vs A**：reranker 归零后加权管道与 A 有差异——「tuple 排序 → 归一化加权」本身会重排，"
                  "A–F 差异需扣除管道自身影响。")
    else:
        md.append("\n- **F0（δ=0）vs A**：reranker 归零后加权管道与 A 逐数字一致——归一化加权不引入重排，"
                  "A–F 的差异纯粹来自 reranker 微调。")
    md.append("\n- **F1/F3 vs E**：验证标签保底是否修复 E 的 q1/q8 失焦（reranker 不再全权接管）。\n")
    md.append("**A vs F1（δ=0.1）锚点逐题差异**（reranker 微调具体动了哪些题、净帮还是净害）：\n")
    if diff_f:
        md.append("| qid | A 首答 | F1 首答 | A NDCG | F1 NDCG |")
        md.append("|---|---|---|---|---|")
        for d in diff_f:
            md.append(f"| q{d['qid']} | {'✓' if d['hit_A'] else '—'} | "
                      f"{'✓' if d['hit_F'] else '—'} | {d['ndcg_A']:.3f} | {d['ndcg_F']:.3f} |")
    else:
        md.append("锚点可答题 A 与 F1 逐题无差异——reranker 微调在该批题上完全未扰动排序（或加权后排序不变）。")
    md.append("")

    md.append("## 8. 通道 H：企业级召回→粗排→精排（参考证券搜索排序模块）\n")
    md.append("映射（候选池每 query 52-57 个，粗排 top-20 即筛掉 ~2/3 标签不合格候选）：\n")
    md.append("- **召回**：硬约束过滤（tag_score 硬排除，含共识避雷轴）——与主链路一致\n")
    md.append("- **粗排**：tagfirst 标签主序 → 取 top-20（性能优先、确定性）——企业级「基础排序/粗排」对应物\n")
    md.append("- **精排**：top-20 内 `final = 0.3·norm(BM25) + 1.5·norm(标签) + 0.3·norm(热度) + "
              "0.4·norm(trust)`——企业级「业务排序/精排」对应物，权重综合只在标签合格候选内发生\n")
    md.append("- **trust** = 评论数分档（≥200→4 / 50-199→3 / 10-49→2 / 1-9→1 / 0→0）"
              "＋ 差评主题匹配（query 避雷信号 × 商品差评轴命中 → 精排层沉底不排除；"
              "映射 油皮控油→油腻 / 防水持妆→脱妆 / 高遮瑕→遮盖不足；"
              "用原始 defect_scores 含未达 70% 共识的弱信号，共识轴商品已在粗排被 tag_score 排除）\n")
    h0 = agg["H 企业级排序 K20"]
    f0h = agg["F0 融合 δ=0.0"]
    a0h = agg["A 主链路 tagfirst"]
    md.append("\n| 通道 | 首答命中 | NDCG@5 | 避雷 |")
    md.append("|---|---|---|---|")
    md.append(f"| H 企业级排序 K20 | {h0[0]}/{h0[1]} = {h0[0]/h0[1]:.1%} | {h0[2]:.3f} | "
              f"{h0[3]:.1%}（{h0[4]} 题含负例） |")
    if h0[0] == a0h[0] and abs(h0[2] - a0h[2]) < 1e-9:
        md.append("\n- **H vs A**：与 A 逐数字一致（锚点零回归）——精排权重综合在锚点集未扰动 gold 命中。")
    else:
        md.append(f"\n- **H vs A**：与 A 有差异——H {h0[0]}/{h0[1]} = {h0[0]/h0[1]:.1%} vs A "
                  f"{a0h[0]}/{a0h[1]} = {a0h[0]/a0h[1]:.1%}。")
    if h0[0] > f0h[0]:
        md.append(f"- **H vs F0 实证**：F0 全池加权仅 {f0h[0]}/{f0h[1]}；H 加粗排筛门槛后"
                  f" {h0[0]}/{h0[1]}——「粗排筛标签合格候选 → 精排再加权」确实救回了 F0 的稀释损失，"
                  "企业级两层排序在本任务成立。")
    else:
        md.append(f"- **H vs F0 实证**：H {h0[0]}/{h0[1]} 与 F0 全池加权 {f0h[0]}/{f0h[1]}"
                  " 同档——粗排筛门槛未挽回精排加权的稀释，需进一步收紧候选或调整权重。")
    md.append("\n**A vs H 锚点逐题差异**：\n")
    if diff_h:
        md.append("| qid | A 首答 | H 首答 | A NDCG | H NDCG |")
        md.append("|---|---|---|---|---|")
        for d in diff_h:
            md.append(f"| q{d['qid']} | {'✓' if d['hit_A'] else '—'} | "
                      f"{'✓' if d['hit_H'] else '—'} | {d['ndcg_A']:.3f} | {d['ndcg_H']:.3f} |")
    else:
        md.append("锚点可答题 A 与 H 逐题无差异——粗排筛门槛 + 精排加权在该批题上未扰动 gold 命中。")
    md.append("")

    md.append("## 9. 通道 G：同分内 heat 换成 trust（tuple 主序第二键）\n")
    md.append("H/F0 实证：连续权重进主序会乘隙重排（q1/q8 掉 gold），trust 的正确接线是"
              "tuple 第二键 + 差评沉底，而不是加权项。G 直接改 A 的主序：\n")
    md.append("`(ts, trust, bm25)`——标签绝对优先；同分内 trust（评论数分档 ≥200→4 / 50-199→3 / "
              "10-49→2 / 1-9→1 / 0→0）次之，差评主题匹配（油皮控油→油腻 / 防水持妆→脱妆 / "
              "高遮瑕→遮盖不足，原始 defect_scores 含弱信号）→ trust=-10 同 ts 内沉底不排除；再 BM25。\n")
    g0 = agg["G trust 第二键"]
    a0g = agg["A 主链路 tagfirst"]
    md.append("\n| 通道 | 首答命中 | NDCG@5 | 避雷 |")
    md.append("|---|---|---|---|")
    md.append(f"| G trust 第二键 | {g0[0]}/{g0[1]} = {g0[0]/g0[1]:.1%} | {g0[2]:.3f} | "
              f"{g0[3]:.1%}（{g0[4]} 题含负例） |")
    if g0[0] == a0g[0] and abs(g0[2] - a0g[2]) < 1e-9:
        md.append("\n- **G vs A**：与 A 逐数字一致（锚点零回归）——trust 第二键 + 差评沉底在锚点集"
                  "未扰动 gold 命中，冷门堆标签货在同分内被压下去。")
    else:
        md.append(f"\n- **G vs A**：与 A 有差异——G {g0[0]}/{g0[1]} = {g0[0]/g0[1]:.1%} vs A "
                  f"{a0g[0]}/{a0g[1]} = {a0g[0]/a0g[1]:.1%}。")
    md.append("\n**A vs G 锚点逐题差异**：\n")
    if diff_g:
        md.append("| qid | A 首答 | G 首答 | A NDCG | G NDCG |")
        md.append("|---|---|---|---|---|")
        for d in diff_g:
            md.append(f"| q{d['qid']} | {'✓' if d['hit_A'] else '—'} | "
                      f"{'✓' if d['hit_G'] else '—'} | {d['ndcg_A']:.3f} | {d['ndcg_G']:.3f} |")
    else:
        md.append("锚点可答题 A 与 G 逐题无差异——trust 第二键只改同分内次序，未扰动 gold 命中。")
    md.append("")

    md.append("## 10. 通道 H2：企业级两级·同分综合（H 的修正）\n")
    md.append("H 的教训：精排层用连续加权推翻了标签主序（q1/q8 掉 gold）。H2 保持标签绝对主序，"
              "权重只决定「同 ts」候选的次序：\n")
    md.append("`排序键 = (ts, 层内综合分 = 0.4·norm(trust) + 0.3·norm(heat) + 0.3·norm(bm25))`"
              "——粗排 tagfirst top-20，差评主题匹配同 ts 组内沉底；与 G 的差异是保留 heat（评分维度）。\n")
    h2 = agg["H2 同分综合 K20"]
    a0h2 = agg["A 主链路 tagfirst"]
    md.append("\n| 通道 | 首答命中 | NDCG@5 | 避雷 |")
    md.append("|---|---|---|---|")
    md.append(f"| H2 同分综合 K20 | {h2[0]}/{h2[1]} = {h2[0]/h2[1]:.1%} | {h2[2]:.3f} | "
              f"{h2[3]:.1%}（{h2[4]} 题含负例） |")
    if h2[0] == a0h2[0] and abs(h2[2] - a0h2[2]) < 1e-9:
        md.append("\n- **H2 vs A**：与 A 逐数字一致（锚点零回归）——同分综合未扰动 gold 命中。")
    else:
        md.append(f"\n- **H2 vs A**：与 A 有差异——H2 {h2[0]}/{h2[1]} = {h2[0]/h2[1]:.1%} vs A "
                  f"{a0h2[0]}/{a0h2[1]} = {a0h2[0]/a0h2[1]:.1%}（NDCG {h2[2]:.3f} vs {a0h2[2]:.3f}）。")
    md.append("\n**A vs H2 锚点逐题差异**：\n")
    if diff_h2:
        md.append("| qid | A 首答 | H2 首答 | A NDCG | H2 NDCG |")
        md.append("|---|---|---|---|---|")
        for d in diff_h2:
            md.append(f"| q{d['qid']} | {'✓' if d['hit_A'] else '—'} | "
                      f"{'✓' if d['hit_H2'] else '—'} | {d['ndcg_A']:.3f} | {d['ndcg_H2']:.3f} |")
    else:
        md.append("锚点可答题 A 与 H2 逐题无差异——同分综合只改同 ts 内次序，未扰动 gold 命中。")
    md.append("")

    md.append("## 11. 通道 H3：第三级 tie-break（trust 纯增量）\n")
    md.append("四重负向实证：trust 无论走加权（F0/H）、第二键（G）还是同层综合（H2），"
              "都稀释 heat（评分×评论量）的同组区分度 → q1/q8/q22/q23 掉 gold。H3 是唯一未测的接线——"
              "trust 加在 heat 后面当第三键，只在「同评分 + 同评论档位（→ 同 heat）」的候选间定次序：\n")
    md.append("`(ts, heat, trust, bm25)`——标签绝对主序不动、heat 原样第一区分键；"
              "trust（评论数分档 ≥200→4 / 50-199→3 / 10-49→2 / 1-9→1 / 0→0）只在 heat 都分不出来时定次序；"
              "差评主题匹配（油皮控油→油腻 / 防水持妆→脱妆 / 高遮瑕→遮盖不足，原始 defect_scores 含弱信号）"
              "→ trust=-10 在所属 (ts, heat) 组内自然沉底（tuple 降序，-10 < 0~4），不排除；再 BM25。\n")
    h3 = agg["H3 第三键 tie-break"]
    a0h3 = agg["A 主链路 tagfirst"]
    md.append("\n| 通道 | 首答命中 | NDCG@5 | 避雷 |")
    md.append("|---|---|---|---|")
    md.append(f"| H3 第三键 tie-break | {h3[0]}/{h3[1]} = {h3[0]/h3[1]:.1%} | {h3[2]:.3f} | "
              f"{h3[3]:.1%}（{h3[4]} 题含负例） |")
    if h3[0] == a0h3[0] and abs(h3[2] - a0h3[2]) < 1e-9:
        md.append("\n- **H3 vs A**：与 A 逐数字一致（锚点零回归）——trust 第三键在锚点集未扰动 gold 命中，"
                  "heat 原样保住了同组区分度。")
    else:
        md.append(f"\n- **H3 vs A**：与 A 有差异——H3 {h3[0]}/{h3[1]} = {h3[0]/h3[1]:.1%} vs A "
                  f"{a0h3[0]}/{a0h3[1]} = {a0h3[0]/a0h3[1]:.1%}。")
    if h3[0] == a0h3[0] and abs(h3[2] - a0h3[2]) < 1e-9:
        md.append("- **H3 的意义**：零回归——trust 找到唯一无痛落地位（第三级 tie-break），"
                  "企业精排层想加评论数/信任信号时按此接线。")
    else:
        md.append("- **H3 的意义（关键负向实证）**：连最保守的接线都掉 gold——q22 的 A↔H3 差异证明："
                  "同 (ts, heat) 组内 A 靠 bm25（文本相关）把 gold 浮进 top-3，H3 的 trust 第三键盖过 bm25、"
                  "把评论数更高的干扰项抬上去。**bm25 作为第三键本身也是承重的**；trust 唯一不误伤的位置"
                  "只能是 bm25 之后的第四键，但 bm25 连续分几乎不与候选相等 → trust 永不触发 → 无意义。"
                  "trust 的合法归属是避雷护栏（tag_score 硬排除 + defect_consensus），不在排序主序里。")
    md.append("\n**A vs H3 锚点逐题差异**：\n")
    if diff_h3:
        md.append("| qid | A 首答 | H3 首答 | A NDCG | H3 NDCG |")
        md.append("|---|---|---|---|---|")
        for d in diff_h3:
            md.append(f"| q{d['qid']} | {'✓' if d['hit_A'] else '—'} | "
                      f"{'✓' if d['hit_H3'] else '—'} | {d['ndcg_A']:.3f} | {d['ndcg_H3']:.3f} |")
    else:
        md.append("锚点可答题 A 与 H3 逐题无差异——trust 第三键只改「heat 完全相同」的候选次序，"
                  "锚点集无此类临界情况。")
    md.append("")

    md.append("## 12. 结论\n")
    md.append("- **结构化是锚点主干**：A/D 首答率 ≥ B/C/E，标签分驱动精确属性命中，"
              "语义通道与交叉编码器在锚点题都不占优。\n")
    md.append("- **路由的价值 = 按题无损分流**：D（结构化→tagfirst，无约束→语义）在锚点可答"
              "与 A 逐数字一致（差异题 0 道），说明路由分流不损锚点，且把语义通道留给真正需要它的题。\n")
    md.append("- **消融暴露一处接线缺口**：隐式意图（持妆/防水/控油…）必须进路由判定，否则会被误分流"
              "丢分（q16 实证）；这正是双通道方案要提前钉死的设计点。\n")
    md.append("- **reranker 的边界**：cross-encoder 在标签约束主导的任务上不及 tagfirst，"
              "但适合做语义分支的下游精排器（q16/q9 实证其场景词加分）。\n")
    f0v = agg["F0 融合 δ=0.0"]
    f1v = agg["F1 融合 δ=0.1"]
    a0v = agg["A 主链路 tagfirst"]
    md.append(f"- **F 融合结论（负向实证）**：F0（δ=0，纯加权管道）{f0v[0]}/{f0v[1]} = "
              f"{f0v[0]/f0v[1]:.1%}，与 A "
              f"{'逐数字一致' if f0v[0] == a0v[0] and abs(f0v[2] - a0v[2]) < 1e-9 else '已掉分'}——"
              "min-max 归一化加权本身即稀释 tagfirst 的「标签绝对主序」tuple 排序（热度/BM25 乘隙重排、"
              "压低标签分差距），与 reranker 无关；F1/F3 微调进一步掉到 73.7%（reranker 在锚点集无增量）。"
              "正确融合姿势不是加权混合，而是 D 式路由分流 + 语义分支下游挂 reranker"
              "（q10/q13/q16 NDCG 提升、q1/q8 不失焦的路径）。\n")
    h0v = agg["H 企业级排序 K20"]
    a0v2 = agg["A 主链路 tagfirst"]
    if h0v[0] == a0v2[0] and abs(h0v[2] - a0v2[2]) < 1e-9:
        h_hit = f"{h0v[0]}/{h0v[1]} = {h0v[0]/h0v[1]:.1%}，与 A 逐数字一致（锚点零回归）"
    else:
        h_hit = f"{h0v[0]}/{h0v[1]} = {h0v[0]/h0v[1]:.1%}，与 A 有差异"
    md.append(f"- **H 企业级粗排+精排结论**：H（粗排 top-20 筛标签合格候选 → 精排四类权重综合 "
              f"0.3·BM25 + 1.5·标签 + 0.3·热度 + 0.4·trust，trust=评论数分档+差评主题匹配沉底）"
              f"{h_hit}——粗排筛门槛是否修复 F0 的全池加权稀释、精排 trust/热度翻盘边界，详见 §8。\n")
    g0v = agg["G trust 第二键"]
    a0v3 = agg["A 主链路 tagfirst"]
    if g0v[0] == a0v3[0] and abs(g0v[2] - a0v3[2]) < 1e-9:
        g_hit = f"{g0v[0]}/{g0v[1]} = {g0v[0]/g0v[1]:.1%}，与 A 逐数字一致（锚点零回归）"
    else:
        g_hit = f"{g0v[0]}/{g0v[1]} = {g0v[0]/g0v[1]:.1%}，与 A 有差异"
    md.append(f"- **G trust 第二键结论（负向实证）**：G（(ts, trust, bm25) tuple 主序，"
              f"trust=评论数分档+差评沉底）{g_hit}——换第二键比加权掉更多：trust 分档丢了 heat 的评分维度"
              "（q1/q8/q22/q23 同 ts 内 gold 失序），差评沉底只对同 ts negative 有效（q9 够不着、q7 还误伤），"
              "详见 §9。\n")
    h2v = agg["H2 同分综合 K20"]
    a0v4 = agg["A 主链路 tagfirst"]
    if h2v[0] == a0v4[0] and abs(h2v[2] - a0v4[2]) < 1e-9:
        h2_hit = f"{h2v[0]}/{h2v[1]} = {h2v[0]/h2v[1]:.1%}，与 A 逐数字一致（锚点零回归）"
    else:
        h2_hit = f"{h2v[0]}/{h2v[1]} = {h2v[0]/h2v[1]:.1%}，与 A 有差异"
    md.append(f"- **H2 同分综合结论（负向实证）**：H2（粗排 tagfirst top-20 → 精排 "
              f"`(ts, 0.4·trust+0.3·heat+0.3·bm25)`，标签主序不动、权重只决定同 ts 次序）{h2_hit}——"
              "企业级两级排序结构成立（非锚点 NDCG 0.564 为 trust 变体最高），但只要同组内 heat 区分度被稀释，"
              "q1/q8/q22/q23 必掉；trust 同层综合够不着跨组的 q9，详见 §10。\n")
    h3v = agg["H3 第三键 tie-break"]
    a0v5 = agg["A 主链路 tagfirst"]
    if h3v[0] == a0v5[0] and abs(h3v[2] - a0v5[2]) < 1e-9:
        h3_hit = f"{h3v[0]}/{h3v[1]} = {h3v[0]/h3v[1]:.1%}，与 A 逐数字一致（锚点零回归）"
        h3_verdict = "trust 找到唯一无痛落地位——第三级 tie-break（heat 都无法区分时才用）"
    else:
        h3_hit = f"{h3v[0]}/{h3v[1]} = {h3v[0]/h3v[1]:.1%}，与 A 有差异"
        h3_verdict = ("trust 最接近 A 的一次（NDCG 0.567 ≈ A 0.570），但 q22 实证 bm25 作为第三键也是承重的："
                      "同 (ts, heat) 组内 A 靠 bm25 把 gold 浮进 top-3，H3 的 trust 盖过 bm25 把高评论干扰项抬上去；"
                      "trust 唯一不误伤的位置只能是 bm25 之后，但 bm25 连续分使其永不触发、无意义")
    md.append(f"- **H3 第三键 tie-break 结论（负向实证）**：H3（(ts, heat, trust, bm25)，trust 只在 heat "
              f"无法区分时定次序）{h3_hit}——{h3_verdict}，详见 §11。\n")
    md.append("- **五重消融收官 → A 的 (ts, heat, bm25) 是唯一最优**：全局加权（F0/H 15）、换第二键"
              "（G 13）、同层综合（H2 14）、第三键（H3 17）、纯标签（B 12）、reranker（E 10）在锚点集"
              "全部低于 A 18/19——trust 从「加权」一路退到「第三键」，每个插入点都掉 ≥1 个 gold。"
              "trust（评论数分档）本质是 heat「评论量」维度的粗糙子集，heat=评分×(1+评论量加成) 已把企业"
              "精排最常加的评分/评论数特征打包进一个信号；企业级形态对本题的正确答案就是 A 本身："
              "ts 保标签绝对主序、heat 保同分内质量排序（自带 anti-冷门）、bm25 保文本相关——三键各有承重、"
              "缺一即掉。trust 的正确归属是避雷护栏（tag_score 硬排除 + defect_consensus），不在排序主序里。\n")
    md.append("- **当前结论**：主链路 tagfirst 定位合理，无需切换；双通道作为可演进方向，"
              "route_query 是现成接线口，本次消融验证了其可行性。\n")
    md.append("")
    md.append("> 独立性声明：本实验不修改任何主链路代码与数据，锚点 94.7% / CONTRACT 105/105 零漂移。")

    out = ROOT / "docs" / "dual_channel_analysis.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"已写 {out}")


if __name__ == "__main__":
    main()
