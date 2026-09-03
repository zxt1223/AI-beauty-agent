# -*- coding: utf-8 -*-
"""recall_router.py — 企业级检索链路：多路召回 → 路由 → 排序通道（Phase-MVP）
===============================================================================
对齐 pangu_search_product 召回模块（字段/向量/热度/API 召回）+ 动态路由：

  多路召回  recall_field 字段路  硬约束（敏感/痘痘）通过即全收，等价 tag_score 硬排除
            recall_text  文本路  BM25 top-K（语义相似候选）
            recall_hot   热销路  heat top-K（口碑规模候选，自带反冷门）
            recall_vector 语义路  向量 top-K（bge 惰性加载，缺模型自动跳过 → 调用方降级）
            recall_all   并集    四路去重 → 候选池（对齐「召回 → 粗排 → 精排」企业级范式）

  路由决策  has_struct(req)：有结构化约束（含隐式意图）→ 主链路 tagfirst；
            无约束 → 语义通道（BM25+向量），避免 tagfirst 退化为热度榜。

锚点铁律（12 通道消融结论，2026-09-01）：
  - 锚点题全部含结构化约束 → 路由到 tagfirst；字段路=硬约束通过全集 → 并集排序
    与全库 tagfirst **字节级一致**（硬排除项本就 -inf 跳过，幸存项顺序不变，tuple 稳定排序）
  - 绝不走加权融合（F0/H 掉 5 点实证）；语义通道只服务无约束 query（D 通道已证无损）
  - route_trace 随 record 落 harness_trace.jsonl（可观测：各路由了多少、走哪个通道）

设计说明（诚实口径）：1090 款小库上字段路≈全库，多路召回是骨架验证；海量数据时
字段路先粗筛、各路并集才是候选池——召回层在海量场景承重。MVP 验证骨架 + 无损性。
"""
import io
import sys
from pathlib import Path

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")

from config import SEM_WEIGHTS, RECALL_TEXT_K, RECALL_HOT_K, RECALL_VEC_K


class RecallRouter:
    """多路召回 + 路由决策（D 通道无损路由的企业级落点）。"""

    def __init__(self, idx):
        self.idx = idx

    # ------------------------------------------------------------------ 路由 ----
    def has_struct(self, req):
        """有结构化约束（含隐式意图）→ True（走 tagfirst）。
        对齐 eval_dual_channel.RoutedDualChannel.has_struct（D 通道锚点无损口径）。"""
        route = self.idx.route_query(str(req.get("qtext") or ""))
        return (route in ("budget", "hard", "form", "avoid", "shade")
                or bool(req.get("hard") or req.get("soft") or req.get("finish")
                        or req.get("coverage") or req.get("form")
                        or (req.get("budget") is not None))
                or bool(req.get("implicit")))

    def channel(self, req):
        return "tagfirst" if self.has_struct(req) else "semantic"

    # ------------------------------------------------------------------ 召回 ----
    def recall_field(self, req):
        """字段路：hard 轴三段判定（A 真雷踢 / B 无信息沉底）下通过即全收。
        与 ProductIndex.hard_verdict 共用 → 召回层与打分层不漂移：
          - ok / sink（无该轴信息且无缺陷证据）→ 放行进候选池（sink 由 tag_score 沉底）
          - exclude（真雷：缺标签且有该轴缺陷 consensus）→ 仍拦在召回层（与 -inf 一致）
        小库≈全库，排序结果与全库 tagfirst 字节级一致；海量数据时此路做属性粗筛。"""
        hard = set(req.get("hard") or ())
        if not hard:
            return list(self.idx.by_asin)
        out = []
        for a, p in self.idx.by_asin.items():
            verdict, _missing = self.idx.hard_verdict(req, p)
            if verdict != "exclude":
                out.append(a)
        return out

    def recall_text(self, req, K=None):
        """文本路：BM25 top-K（title+brand 英文文档）。"""
        K = K or RECALL_TEXT_K
        q = str(req.get("qtext") or "")
        scored = sorted(self.idx.by_asin,
                        key=lambda a: self.idx.bm25.score(q, self.idx.index_of[a]),
                        reverse=True)
        return scored[:K]

    def recall_hot(self, req, K=None):
        """热销路：heat（评分 × 评论量）top-K，自带反冷门。"""
        K = K or RECALL_HOT_K
        scored = sorted(self.idx.by_asin,
                        key=lambda a: self.idx.heat_score(self.idx.by_asin[a]),
                        reverse=True)
        return scored[:K]

    def recall_vector(self, req, K=None):
        """语义路：向量 top-K。bge 未加载 → 返回 []（调用方自然降级为文本+热销）。
        build_vec_query 需 req['vec_text']（eval 口径才有），运行时缺失则用原 query。"""
        K = K or RECALL_VEC_K
        if self.idx._encoder is None or self.idx._doc_vecs is None:
            return []
        try:
            qv = self.idx._encoder.encode(
                [self.idx.build_vec_query(req)], normalize_embeddings=True)[0]
        except Exception:
            return []
        scored = sorted(self.idx.by_asin,
                        key=lambda a: float(qv @ self.idx._doc_vecs[self.idx.index_of[a]]),
                        reverse=True)
        return scored[:K]

    def recall_all(self, req):
        """多路召回 → 并集去重（稳定顺序：字段 → 文本 → 热销 → 语义）。
        返回 (候选列表, route_trace)。"""
        parts = {
            "field": self.recall_field(req),
            "text": self.recall_text(req),
            "hot": self.recall_hot(req),
            "vector": self.recall_vector(req),
        }
        seen, union = set(), []
        for name, cands in parts.items():
            for a in cands:
                if a not in seen:
                    seen.add(a)
                    union.append(a)
        trace = {name: len(c) for name, c in parts.items()}
        trace["union"] = len(union)
        return union, trace

    def route_and_recall(self, req):
        """顶层：路由决策 + 多路召回，返回 (channel, candidates, route_trace)。"""
        ch = self.channel(req)
        cands, trace = self.recall_all(req)
        trace["channel"] = ch
        return ch, cands, trace


if __name__ == "__main__":
    # 自测：跑通 路由 + 多路召回 + 并集，输出 route_trace（不加载向量）
    from retrieval_engine import ProductIndex
    idx = ProductIndex()
    router = RecallRouter(idx)
    # 结构化 query（走 tagfirst）
    r1 = {"qtext": "foundation matte for oily skin", "hard": set(), "soft": {"油皮"},
          "finish": "哑光", "coverage": None, "form": None, "budget": None, "implicit": []}
    ch, cands, tr = router.route_and_recall(r1)
    print(f"[结构化] channel={ch} recalls={tr}")
    # 无约束 query（走语义，向量未加载 → 降级文本+热销）
    r2 = {"qtext": "I need a foundation for my wedding", "hard": set(), "soft": set(),
          "finish": None, "coverage": None, "form": None, "budget": None, "implicit": []}
    ch, cands, tr = router.route_and_recall(r2)
    print(f"[无约束] channel={ch} recalls={tr}")
