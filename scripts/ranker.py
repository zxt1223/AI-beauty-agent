# -*- coding: utf-8 -*-
"""ranker.py — 精排器接口 + 行为模型预留位（对齐 pangu 排序模块「粗排 → 精排」）
===============================================================================
pangu 排序链：召回 → 粗排（模型/双塔）→ 精排（行为特征模型 CTR/GMV/LambdaRank）
                                                        → 业务重排（避雷/防资损/多样性）

本模块落地「精排」这一层的**接口位**（seam）：
  - ColdStartRanker  当前唯一可用精排 = (标签分, 热度, BM25) tagfirst（锚点口径）。
                     本库数据是 Amazon 静态快照，**无 CTR/GMV/转化埋点** → 冷启动。
  - BehaviorRanker   企业级演进预留位：行为模型双塔打分接口已定、特征清单已列，
                     **未训练**（无行为数据，局限1）。接入行为模型只改 config.RANKER 一行，
                     调用点与接口不变——这就是「可演进架构」的落点。

调用点：agent._retrieve 主链路 `self.ranker.rank(candidates, req, meta)`。
换精排：config.RANKER = "coldstart" | "behavior"（behavior 无模型时安全降级冷启动，不崩）。

诚实声明：MVP 不实现行为模型训练管线——把「哪里接、特征是什么、怎么换」钉死在代码
和文档里，讲「接口就位、演进路径清晰、现因数据局限走冷启动」。
"""
import io
import sys
from abc import ABC, abstractmethod
from pathlib import Path

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")

from config import RANKER


class Ranker(ABC):
    """精排器接口：输入候选（召回并集）+ 请求特征，输出降序 [(asin, score, reasons)]。"""

    @abstractmethod
    def rank(self, candidates, req, meta):
        """candidates: asin 列表；req: 结构化约束；meta: 上下文（隐式/肤质/路由…）。
        返回 [(asin, score, reasons)]，score 降序（业务重排/后置过滤在 agent 侧继续做）。"""


class ColdStartRanker(Ranker):
    """冷启动精排 = tagfirst (标签分, 热度, BM25)（12 通道消融唯一最优，锚点 94.7%）。
    无行为信号下用「内容代理」——热度=评分×评论量打包质量+口碑规模，自带反冷门。"""

    def __init__(self, idx):
        self.idx = idx

    def rank(self, candidates, req, meta):
        return self.idx.score_candidates("tagfirst", req, candidates)


class BehaviorRanker(Ranker):
    """行为模型精排（**预留位，未启用**）：有 CTR/GMV/转化埋点后训练 LambdaRank 双塔，
    精排特征（行为侧）：
      - 曝光/点击/转化 / CTR、加购率、成交（GMV）
      - 价格带拟合度、近 30 天复购/热度、差评规避后转化
    接入后取代 ColdStartRanker，调用点与接口不变（config.RANKER = "behavior"）。
    无模型被选中 → 安全降级冷启动 + 显式警告（绝不崩 Demo，诚实标注未启用）。"""

    def __init__(self, idx):
        self.idx = idx
        self._fallback = ColdStartRanker(idx)

    def rank(self, candidates, req, meta):
        print("[ranker] BehaviorRanker 未训练（无 CTR/GMV 行为数据），降级 ColdStartRanker。", file=sys.stderr)
        return self._fallback.rank(candidates, req, meta)


def get_ranker(idx):
    """精排器工厂：config.RANKER 选择，换精排只改配置一行。"""
    if RANKER == "behavior":
        return BehaviorRanker(idx)
    return ColdStartRanker(idx)
