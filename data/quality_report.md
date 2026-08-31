# 数据质量报告 —— 商品知识库·模块①

- 数据源: Amazon All Beauty 商品元数据 (meta_All_Beauty.jsonl.gz)，全库 —
- 范围: 面部粉底液（foundation / bb cream / cushion），排除化妆工具/散粉/妆前等

## 清洗漏斗

| 阶段 | 商品数 |
|---|---|
| 全库 | 112,590 |
| 标题含 foundation/bb cream/cushion | 2301 |
| 排除工具/散粉/妆前等非粉底液后 | 1090 |
| 按 parent_asin 去重后 | 1090 |

## 数据质量问题

| 检查项 | 结果 |
|---|---|
| 标题命中粉底液关键词 | 2301 |
| 排除工具/非粉底液后 | 1090 |
| 按 parent_asin 去重后 | 1090 |
| 价格缺失 | 838 |
| 价格缺失率 | 76% |
| 价格异常(≤0或>150$) | 3 |
| 评分异常(超出1-5) | 0 |
| 重复标题 | 30 |
| 已停产商品 | 2 |
| 属性冲突-肤质(title vs details) | 1 |
| 属性冲突-妆效(title vs details) | 4 |

## 属性完整度

| 属性 | details 原始完整度 | 合并 title 兜底后 |
|---|---|---|
| brand | 66% | 89% |
| price | 23% | 23% |
| item_form | 73% | 87% |
| skin_type | 37% | 37% |
| finish_type | 45% | 55% |
| coverage | 28% | 28% |
| skin_tone | 7% | 7% |
| scent | 0% | 0% |

## 字段来源分布（置信度）

| 字段 | 来自 details | 来自 title 推断 | 缺失 |
|---|---|---|---|
| item_form | 799 | 155 | 136 |
| skin_type | 404 | 7 | 679 |
| finish_type | 492 | 109 | 489 |

## 属性冲突样例

| 标题 | 冲突 |
|---|---|
| SACE LADY Matte Liquid Foundation Makeup, Longwear Foundatio | 肤质冲突=True 妆效冲突=False |
| TROIAREUKE H+ Cushion Foundation (Shade 21), Natural Coverag | 肤质冲突=False 妆效冲突=True |
| Too Faced Dew You Glow Full Coverage Foundation - Sand | 肤质冲突=False 妆效冲突=True |
| TOO FACED Dew You Glow Full Coverage Foundation - Natural Be | 肤质冲突=False 妆效冲突=True |
| TOO FACED VANILLA TUTTI FRUTTI DEW YOU FULL-COVERAGE FRESH G | 肤质冲突=False 妆效冲突=True |