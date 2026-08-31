# -*- coding: utf-8 -*-
"""defect_consensus.py — 评论负面反馈 → 硬规则（避雷）共识口径（用户定标 70%）
========================================================================================
口径：某缺陷轴提及次数 ÷ 该商品负面评论数 ≥ 70% → 该缺陷轴算「硬规则」（命中即避雷）。
  轴 ∈ {卡粉, 脱妆, 闷痘, 刺激, 油腻}（避雷轴）；色号偏深黄/色号偏浅灰 = 色号适配，
  不算质量问题，永不进避雷。负面评论数 0 → 无法形成共识 → 不标（宁缺毋滥）。

被 agent.py（运行时硬过滤）与 build_avoid_set.py（负候选打分）共用，保证口径唯一。

输入：product_defect_evidence.csv 一行（parent_asin / defect_scores / n_neg_reviews）
用法：
  from defect_consensus import consensus_axes, parse_scores, DEFECT_CONSENSUS
"""
from __future__ import annotations

# 70% 负面共识阈值（用户定标 2026-08-28：提及数/负面评论数 ≥70% → 标硬规则）
DEFECT_CONSENSUS = 0.7
# 可进硬规则的缺陷轴（与 agent.py DEFECT_LABEL 的键一致）
AVOID_AXES = {"卡粉", "脱妆", "闷痘", "刺激", "油腻"}


def parse_scores(scores_str) -> dict:
    """'卡粉:3;脱妆:1;色号偏深黄:2' → {'卡粉':3, '脱妆':1, '色号偏深黄':2}。"""
    out = {}
    for part in str(scores_str or "").split(";"):
        if ":" in part:
            k, _, v = part.partition(":")
            try:
                out[k] = int(float(v))
            except (TypeError, ValueError):
                continue
    return out


def consensus_axes(defect_scores, n_neg_reviews) -> set:
    """返回达标硬规则的缺陷轴集合（≤ 1 个也可，通常 0-2）。"""
    scores = parse_scores(defect_scores)
    try:
        n_neg = int(n_neg_reviews)
    except (TypeError, ValueError):
        n_neg = 0
    if n_neg <= 0:
        return set()
    return {ax for ax, cnt in scores.items()
            if ax in AVOID_AXES and cnt / n_neg >= DEFECT_CONSENSUS}
