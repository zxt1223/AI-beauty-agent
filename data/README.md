# data/ · 资产真相源清单（评测文件治理 2026-09-02）

> 一句话：**MySQL 是评测真相源，CSV 分「输入源文件 / 快照 / 报告产物」三类；行数以本表为准，别信散落在旧文档里的历史数字。**

## 权威核对（2026-09-02 实测 MySQL）

| 表 / 文件 | 行数 | 角色 |
|---|---|---|
| MySQL `products` | 1090 | 商品主库真相源 |
| MySQL `eval_review_50` | **200**（id 1-200，41 锚点 + 159 v3） | **评测集唯一真相源** |
| MySQL `对比表1` | 41（仅 v2 复核副本，v3 **未**进） | v2 人工复核对照 |
| MySQL `candidate_pool_v2` | **10702 / 200 题** | 池内首答/NDCG 口径（2026-09-02 v3 扩池重建） |
| MySQL `candidate_pool` | 617 / 11 题 | Phase-1 池（旧） |
| `products_clean.csv` | 1090 | 商品库 CSV 输入源（与 MySQL products 同源） |
| `product_defect_evidence.csv` | 按商品一行 | 差评轴证据（避雷共识分母 `n_neg_reviews` 真相源） |
| `data/_v3_eval_full.csv` | 159（88 好评 + 32 模拟 + 39 差评） | **v3 评测源文件** → 已装载 eval_review_50 id 42-200 |
| `data/candidate_pool_v2.csv` | 10702 | 池 CSV 快照（与 MySQL 一致） |

## ① 商品与证据（输入源，运行时只读）

- `products_clean.csv` —— 1090 款主商品库（五维标签见 docs/label_pipeline.md）
- `product_defect_evidence.csv` —— 缺陷证据轴 `defect_scores` + `n_neg_reviews`（避雷共识分母）
- `review_scores.csv` / `products_clean_sim.csv` / `products_clean.xlsx` / `quality_report.md` —— 辅助数据与清洗质量报告

## ② 评测集（真相源 = MySQL，CSV 只是快照/源文件）

- **`eval_review_50.csv`（11 行）＝ 遗留 v1 抽取样例，不是 MySQL 200 行快照** —— README/ARCHITECTURE 历史引用曾误当「可装载快照」，勿当真值，仅存档
- `_v3_eval_full.csv`（159 行）—— v3 三轨源文件（好评/模拟/差评），装载进 eval_review_50 的输入
- `evaluation_set.csv` —— Phase-1 50 题集（含 intent + explicit/implicit/query_rewrite）
- `eval_queries.csv` / `eval_queries_all.csv` —— eval_queries（11 行）表的 CSV 源

## ③ 候选池（排序评测口径）

- `candidate_pool_v2.csv` —— **10702 行 / 200 题**（首答/NDCG 池内口径；2026-09-02 扩池，q7 重标后口径）
- `candidate_pool.csv` —— 617 行 / 11 题 Phase-1 池（NDCG@5 0.553 的旧口径池，Phase-1 消融用）

## ④ 报告产物（可随时重跑，非真相，改代码后直接重生成）

| 文件 | 生成脚本 | 内容 |
|---|---|---|
| `eval_report.csv` | eval_runner.py | **锚点回归**（94.7%/105/105…，每次改动必跑） |
| `eval_v3_report.csv` | _eval_v3.py | v3 四维（26.7%/0.268/16-16/资损 38/39） |
| `ablate_v3_report.csv` | _ablate_v3.py | 新增集 12 通道消融 |
| `dual_channel_report.csv` | eval_dual_channel.py | 12 通道消融（旧 75 题口径） |
| `sem_probe_report.csv` | _sem_probe_report.py | 语义试探 200 条（进入 66/通过 14/误放 0） |
| `loss_risk_report.csv` | eval_loss_risk.py | 资损陷阱 8 题 |
| `badcase_report.csv` | 三通道自动登记 | 坏例登记（q9 + Phase-1 池泄漏 3 跳过） |
| `human_gold_check.csv` | prep_human_gold_check.py | 人工 gold 复核记录 |
| `dashboard.html` | dashboard.py | 埋点看板（双击浏览器打开） |

## ⑤ 运行时状态（不入库；可重建/含运行时数据）

- `user_profiles.json` / `harness_trace.jsonl` / `ui_feedback.jsonl` —— 画像/埋点/反馈
- `llm_cache.json` / `title_translation_cache.json` / `translate_cache.json` —— 缓存（省调用）

## ⑥ 过程文件（`_` 前缀，临时中间产物，可删）

`_v3_*.csv|json`、`_tag_fill.json` —— v3 构建中间产物（`_eval_v3.py` / 池构建暂存），不入正式真相。

## 铁律

1. **改表/改指标/改行数口径 → 强制同步 `docs/database_schema.md`**（该文档是本仓库 schema 单一真相源）。
2. 新增评测文件 → 在 ②③④ 找到归属并更新本表行数；旧文件淘汰 → 移入 `data/` 下或标注「遗留」，别留无主 CSV。
3. 对外引用数字 → 以 MySQL 实测 + `eval_runner.py` 输出为准，不用本表之外的历史行数。
