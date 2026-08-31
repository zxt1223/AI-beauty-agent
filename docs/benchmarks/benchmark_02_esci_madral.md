# Benchmark 02 — Amazon ESCI + J-MADRAL/P-MADRAL（相关度金标准 + 商品检索）

> **对标定位**：金标准相关度分级设计
> **项目来源**：MADRAL 团队（HuggingFace），数据来自 Amazon
> - 模型：[J-MADRAL](https://huggingface.co/J-MADRAL/J-MADRAL) / [P-MADRAL](https://huggingface.co/J-MADRAL/P-MADRAL)
> - 相关数据集：AmazonESCI（~97.3k）、SearchESCI（~100k）
> - 评测基准：Amazon ESCI / TREC Product Search 2023 / Search ESCI
> - 论文：《Multi-Aspect Joint Retrieval for E-Commerce: Bridging Product Catalogs and Customer Reviews》

---

## 1. 项目是什么

工业界 + 学术界的「电商商品检索」标杆：用 Amazon 官方日志的**真实搜索查询 + 人工标注相关度**（ESCI），训练和评测商品检索模型。**我们的金标准相关度分级（primary/extra/negative）跟 ESCI 的四档结构几乎同构**——这是本项目对我们最重要的价值。

## 2. 完整架构

### 2.1 ESCI 数据集（金标准真相源）
- ESCI = **E**xact / **S**ubstitute / **C**omplement / **I**rrelevant，Amazon 真实用户搜索日志，**人工标注**的四档相关度：

| 档位 | 含义 | 例子（query="轻薄粉底液"） | 对应我们的 |
|---|---|---|---|
| Exact | 精确匹配：同一款商品，就是用户要找的 | 该粉底液本尊 | `primary`（相关 1.0） |
| Substitute | 替代品：功能相同、不同款 | 另一款轻薄粉底液 | `extra`（相关 0.8） |
| Complement | 互补品：搭配使用 | 定妆喷雾、粉扑 | 我们的 scope 内无（同品类搜索） |
| Irrelevant | 不相关 | 粉底刷 | `negative`（相关 -1） |

- 意义：**Amazon 官方用人工标注确立「相关度不是二元的，是分档的」**——这就是我们 primary/extra/negative 三档的工业界先例。

### 2.2 模型：MADRAL（多角度稠密检索）
- **双编码器稠密检索**（BiEncoder）：查询和商品共用同一个 BERT 编码器，相似度 = 点积
- 三个变体（角度不同）：
  - `P-MADRAL`：只用**商品**（Product）角度建模
  - `R-MADRAL`：只用**评论**（Review）角度建模
  - `J-MADRAL`：**商品 + 评论联合建模**（Joint）——最关键的设计
- 训练两阶段：
  1. 预训练 20 epoch（lr 1e-4）在电商商品+评论数据上
  2. 微调 20 epoch（lr 5e-6），**每样本 7 个负样本**做对比学习
- 核心思想：同一编码器同时服务「商品检索」和「评论检索」两种查询——因为评论里蕴含真实用户的语言（买家会怎么描述这个商品），联合建模提升召回

### 2.3 评测
- 三个评测集：Amazon ESCI（商品检索）、Search ESCI（评论检索）、TREC Product Search 2023（竞赛数据）
- 指标：**R@100 / R@500 / MRR / nDCG@10 / nDCG@50**
- 基线：BM25、DRAGON、P-BiBERT、J-BiBERT 等
- 关键结论：
  - P-MADRAL 在商品检索（Amazon ESCI）夺冠（R@100=0.6235）
  - **J-MADRAL 在评论检索（Search ESCI）全面第一**（R@100=0.6488）——说明**加入评论信息显著提升「用户语言 → 商品」的检索**

## 3. 最值得借鉴的地方（对 beauty_agent）

1. **ESCI 分档 = 我们金标准分档的官方背书**（核心论据）：「我们的 relevance 三档（1.0/0.8/-1.0）与 Amazon 官方 ESCI 的 Exact/Substitute/Complement/Irrelevant 四档同构，工业界先例」
2. **商品 + 评论联合建模**：我们目前只用商品元数据（title/brand/skin_tags）建检索，**没有用评论里的真实语言**。J-MADRAL 证明加评论能显著提升「用户口语 → 商品」匹配——而我们恰好有全部评论数据，且 query 就是从评论抽的，可以：把每商品的代表性好评文本也嵌入，建「商品 + 评论双视图」检索
3. **负采样训练**：J-MADRAL 用 7 负样本对比学习。我们如果做精排/微调，可以采用同样策略
4. **我们的差异点**：ESCI 是**官方人工标注**（贵、规模受限），我们是**自动生成 + 可解释规则**（便宜、可扩展、可追溯）——「我们解决了人工标注成本问题，用规则自动生成金标准，抽样人工复核兜底，实现 908 条 Query 的规模化」

## 4. 可落地到 beauty_agent 的三件事

- [ ] 在 eval_gold 里把 relevance 语义与 ESCI 四档对齐说明（primary=Exact、extra=Substitute、negative=Irrelevant），写进 schema 文档
- [ ] RAG 检索阶段增加「评论视图」：每商品关联 Top 好评摘要文本，检索时商品元数据 + 评论双路召回
- [ ] 将「相关度分档对标 Amazon ESCI」写入项目简介
