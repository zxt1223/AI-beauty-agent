# -*- coding: utf-8 -*-
"""defect_consensus.py — 评论负面反馈 → 硬规则（避雷）共识口径
========================================================================================
当前口径（用户定标 2026-08-28 的 70% 比例 + 2026-09-02 补最小样本下限）：
  ① 该商品负面评论总数（n_neg_reviews）≥ DEFECT_MIN_NEG（5）——负面样本太少不下「避雷」结论，
     防止 1~4 条偶发差评就把商品永久拉黑（审计实证：原无下限时 195 商品 163 进避雷、
     命中项负面评论中位仅 1，单条差评 1/1 = 100% 即命中）。
  ② 某缺陷轴提及次数 ÷ 该商品负面评论总数 ≥ DEFECT_CONSENSUS（70%）→ 该轴算「硬规则」。
  轴 ∈ {卡粉, 脱妆, 闷痘, 刺激, 油腻}（避雷轴）；色号偏深黄/色号偏浅灰 = 色号适配，
  不算质量问题，永不进避雷。负面评论数 0 → 无法形成共识 → 不标（宁缺毋滥）。

  ⚠️ 分母口径注释写死（2026-09-02）：分母 = 该商品「负面」评论总数（n_neg_reviews），
     不是含好评的全部评论，也不是单缺陷主题的评论数——本表每商品一行，缺陷词提及数与
     负面评论数来自同一个负评池，不存在「单主题独立评论数」字段。

被 agent.py（运行时硬过滤）与 build_avoid_set.py（负候选打分）共用，保证口径唯一。

历史基线（v2026-08-28 旧口径，已归档仅消融用）：
  无最小样本下限，仅缺陷轴提及数 ÷ 负面评论数 ≥ 70% → 硬规则。
  保留为 legacy_consensus_axes()，跑改动消融/回归时可对比新旧避雷集差异。

输入：product_defect_evidence.csv 一行（parent_asin / defect_scores / n_neg_reviews）
用法：
  from defect_consensus import consensus_axes, legacy_consensus_axes,
                                 parse_scores, DEFECT_CONSENSUS, DEFECT_MIN_NEG
"""
from __future__ import annotations

from config import DEFECT_CONSENSUS, DEFECT_MIN_NEG  # 70% + 最小样本 5（单一真相源）
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
    """返回达标硬规则的缺陷轴集合（≤ 1 个也可，通常 0-2）。

    2026-09-02 门槛升级：负面评论总数 < DEFECT_MIN_NEG → 样本不足，不参与判定（返回空集）。
    即：偶发差评（1~4 条）无法再靠 100% 占比把商品打入避雷表。
    """
    scores = parse_scores(defect_scores)
    try:
        n_neg = int(n_neg_reviews)
    except (TypeError, ValueError):
        n_neg = 0
    if n_neg < DEFECT_MIN_NEG:            # ① 最小样本下限（旧口径无此行）
        return set()
    return {ax for ax, cnt in scores.items()
            if ax in AVOID_AXES and cnt / n_neg >= DEFECT_CONSENSUS}


def legacy_consensus_axes(defect_scores, n_neg_reviews) -> set:
    """【已归档基线，消融对比用】v2026-08-28 旧口径：无最小样本下限，
    缺陷轴提及数 ÷ 负面评论数 ≥ 70% 即硬规则。不用于生产判定。"""
    scores = parse_scores(defect_scores)
    try:
        n_neg = int(n_neg_reviews)
    except (TypeError, ValueError):
        n_neg = 0
    if n_neg <= 0:
        return set()
    return {ax for ax, cnt in scores.items()
            if ax in AVOID_AXES and cnt / n_neg >= DEFECT_CONSENSUS}
