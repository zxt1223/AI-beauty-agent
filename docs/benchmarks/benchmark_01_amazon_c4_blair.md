# Benchmark 01 — BLaIR / Amazon-C4（复杂查询商品搜索）

> **对标定位**：评测集设计对标（复杂查询）
> **项目来源**：UCSD McAuley Lab（Amazon Reviews 2023 官方团队）
> - 仓库：[github.com/kongrui/hyp1231-AmazonReviews2023](https://github.com/kongrui/hyp1231-AmazonReviews2023)
> - 评测工具包：[github.com/hyp1231/BLaIR-Bench](https://github.com/hyp1231/BLaIR-Bench)
> - 数据集：`McAuley-Lab/Amazon-Reviews-2023`（HuggingFace）
> - 论文：arXiv:2403.03952《Bridging Language and Items for Retrieval and Recommendation》

---

## 1. 项目是什么

Amazon Reviews 2023 的官方配套仓库，做三件事：

1. **数据**：把 Amazon Reviews 2023 原始数据预处理成标准 train/val/test 划分（`benchmark_scripts/`）
2. **模型**：BLaIR——在 Amazon Reviews 2023 上预训练的语言模型，学会「商品元数据 ↔ 语言上下文」的语义对齐（`blair/`）
3. **新评测集**：**Amazon-C4**（Complex Contexts Created by ChatGPT）——复杂商品搜索基准（`amazon-c4/`）

> 我们的项目是「Amazon Beauty + RAG 导购」，这个仓库是**同数据源（同父数据）**里跟「评测集设计」最接近的参考。

## 2. 完整架构

### 2.1 数据获取与预处理
- 数据源：Amazon Reviews 2023（33 个类目，570M 评论 / 48M 商品）
- `benchmark_scripts/` 提供原始数据 → train/val/test 的切分脚本
- BLaIR 预训练用「商品元数据 + 评论」成对数据

### 2.2 核心 AI 技术：BLaIR 预训练
- 在 Amazon Reviews 2023 上做**句子级对比学习预训练**（参考 SimCSE），目标：同一商品被不同语言上下文表达时嵌入相近，不同商品相距远
- 预训练后冻结或微调，用作商品/查询的**语义编码器**
- 关键结论：**仅用 MLM 训练的模型检索效果极差**（RoBERTa 0.25 vs BLaIR 14.46），因为 `[CLS]` 句向量没经过句子级对比目标训练——对比学习目标对零样本检索至关重要

### 2.3 评测集 Amazon-C4 如何构建（核心借鉴点）

| 步骤 | 做法 | 我们的对应 |
|---|---|---|
| 素材 | 从 Amazon Reviews 2023 **测试集**均匀采样 ~22,000 条评论，条件：**5 星 + 评论 ≥100 字符** | 我们从全量评论抽真实需求，未限定星级/长度 |
| 改写 | **ChatGPT 把真实评论改写为第一人称自然段落**作为查询 | ⚠️ 我们是**原始评论抽取**，不改写——更真实 |
| 配对 | 复杂查询改写自某条评论 → ground-truth = 该评论对应商品 | 同构：我们的 primary = 来源评论商品 |
| 候选池 | ground-truth 所在域内随机采样 **50 个同类目商品** + 跨域大候选池（1,058,417 商品） | 我们只有商品知识库 1,117 个，无采样负候选 |
| 规模 | 21,223 条查询，平均查询长度 **229.89 字符** | 我们 908 条 |
| 对比 | ESCI 短查询平均仅 **22.46 字符**——量级差 10 倍 | — |

**复杂查询示例**（对比同商品）：
- ESCI 短查询：`salt gun`
- Amazon-C4 复杂查询：`I want a gun that I can use while gardening to get rid of stink bugs, ants, flies, and spiders in my house. It needs to be amazing and help me feel less scared.`

### 2.4 评测指标与方法
- 任务：**complex product search** —— 给定长自然语言上下文，从候选池召回相关商品
- 指标：**NDCG@100**（ground-truth 商品在 Top100 排序中的位置质量）
- 方法：零样本稠密检索（查询嵌入 vs 商品元数据嵌入，余弦相似度排序）
- 关键发现：**BM25 在长复杂查询下完全失效（NDCG 全为 0.00）**，证明词法匹配无法处理复杂语义查询，必须用语义检索

### 2.5 badcase 优化
- 论文未单独讲 badcase；但指出「BM25 失效」这个失败模式 → 引导模型需要更强的语言理解与语言-商品语义对齐

## 3. 最值得借鉴的地方（对 beauty_agent）

1. **「复杂 vs 短查询」难度分层**：C4 证明长自然语言查询是评测盲区。我们的 query 平均长度偏短，评测会偏简单 → 可以按查询长度/信息密度给评测集分层（short / medium / complex），对标 C4 的 22 vs 230 字符两级
2. **候选池构建法**：C4 用「同类目采样 50 负候选 + 跨域候选」来测 NDCG，比我们「全库排序」更接近真实电商漏斗（用户不会翻完 1000 个商品）。我们的 NDCG@5 也应限定候选池
3. **我们的独特点（C4 没有的）**：C4 是 **LLM 改写**的查询（半合成），我们是**真实评论原句**——「我们的 Query 不是 LLM 生成的，是真实用户在真实场景下写的，生态效度更高」；且 C4 只给了 5 星好评配对（全正例），我们还有**避雷负例**维度，更完整

## 4. 可落地到 beauty_agent 的三件事

- [ ] 给 908 条 query 加一列 `complexity`（按长度 + 信息熵分 short/medium/complex），评测按难度分层出报告
- [ ] NDCG@5 评测改为「限定候选池」：每个 query 从知识库采样同属性负候选（对标 C4 的 50 同类目），避免全库排序虚高
- [ ] 将「评测集对标 Amazon-C4 复杂查询 + 真实评论抽取」写入项目简介
