# -*- coding: utf-8 -*-
"""config.py — 全局配置单一真相源（对齐 pangu 方案配置 / 运营调优模块）
===============================================================================
企业级口径：可调参数不散落在各脚本里，收拢到一处，运营/实验可改、可审计、可回滚。
各模块 `from config import X` 引用（值保持不变 → 锚点零漂移）。

  HEAT_HI / HEAT_MID       热销分档（agent.py 热销加分 + 中文口碑护栏）
  DEFECT_CONSENSUS         避雷共识阈值（defect_consensus.py，用户定标 70%）
  SESSION_MAX_QUERIES /    会话行为预算（harness.py）
  SESSION_MAX_LLM /
  SESSION_WINDOW_SEC
  MAX_PROFILES             用户画像上限（web_server.py，按 last_visit 淘汰）
  SEM_WEIGHTS              语义通道权重（recall_router.py / eval_dual_channel.py）
  RECALL_TEXT_K /          各召回路 top-K（recall_router.py 多路召回候选池）
  RECALL_HOT_K /
  RECALL_VEC_K

本文件不 import 任何业务模块（纯常量），避免循环依赖。
"""
import sys
import io
from pathlib import Path

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

ROOT = Path(r"C:\Users\Lenovo\Desktop\beauty-agent")

# ---- 热销分档（2026-08-27 用户定）：高 ≥200 / 中 50-199 / 低 <50 ----
HEAT_HI, HEAT_MID = 200, 50

# ---- 避雷共识（2026-08-27 用户定标 70%；2026-09-02 补最小样本下限）----
# 口径（单一真相源，defect_consensus.py）：该商品负面评论总数 ≥ DEFECT_MIN_NEG 才参与比例判定，
#   且缺陷轴提及数 ÷ 该商品负面评论总数 ≥ DEFECT_CONSENSUS → 硬规则（命中即避雷）。
#   分母 = 该商品的负面评论总数（product_defect_evidence.csv.n_neg_reviews），
#   不是含好评的全部评论，也不是单缺陷主题的评论数——注释写死，防误用。
#   DEFECT_MIN_NEG = 5 防「1~4 条偶发差评就下避雷结论」（原无下限时 195 商品 163 进避雷、
#   命中项负面中位仅 1——单条差评 1/1=100% 即永久拉黑，2026-09-02 审计实证）。
DEFECT_CONSENSUS = 0.7
DEFECT_MIN_NEG = 5                 # 最小负面评论样本下限（<5 条不配下「避雷」结论）

# ---- 会话行为预算（harness.py）----
SESSION_MAX_QUERIES = 100      # 单会话最多查询次数（防死循环/刷接口）
SESSION_MAX_LLM = 20           # 单会话最多 LLM 触发次数（成本控制；中文每条 LLM ~2-12s）
SESSION_WINDOW_SEC = 3600      # 预算窗口：1 小时滚动

# ---- 用户画像上限（web_server.py）----
MAX_PROFILES = 100             # 画像上限，超出按 last_visit 淘汰最久

# ---- 语义通道权重（对齐 eval_dual_channel.SEM_WEIGHTS）：BM25 0.5 / 标签 0 / 向量 0.5 / 热度 0 ----
SEM_WEIGHTS = (0.5, 0.0, 0.5, 0.0)

# ---- 语义试探分支门槛（2026-09-02 用户拍板 θ=0.05）：reranker top-1 置信度 ≥ θ → 有明确语义
#      指向 → 走语义推荐；< θ → 真模糊，保持 ask_all 追问。词表外语义意图题 conf 0.070-0.686 /
#      真模糊 q4/q5/q6 conf 0.001-0.008 天然拉开间隙。可配置，后续 θ∈[0.03,0.05,0.07] 参数扫描 ----
SEM_PROBE_THRESHOLD = 0.05
SEM_PROBE_COARSE_K = 20     # 试探粗排候选数：mixed 全库 top-K → reranker 精排

# ---- 多路召回 top-K（recall_router.py；字段路小库全收，不设 K——等价全库 tagfirst 无损）----
RECALL_TEXT_K = 40
RECALL_HOT_K = 40
RECALL_VEC_K = 40

# ---- 精排器选择（ranker.py）：coldstart = 标签主序（现唯一可用，锚点口径）；
#      behavior = 行为模型双塔（预留位，无 CTR/GMV 数据未训练，选中时安全降级冷启动）----
RANKER = "coldstart"
