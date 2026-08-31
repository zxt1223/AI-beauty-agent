# Benchmark 04 — RAG LLM-based Recommender（RAG 电商推荐全栈，GCP）

> **对标定位**：RAG 导购 Agent 检索架构
> **项目来源**：[github.com/polarbear333/rag-llm-based-recommender](https://github.com/polarbear333/rag-llm-based-recommender)（MIT）
> **数据源**：Amazon Reviews 2023（PySpark ETL）
> **技术栈**：GCP（BigQuery 向量 + ScaNN + Vertex AI + LangChain + Gemini-2.5-pro + FastAPI + React）

---

## 1. 项目是什么

一个完整的 **RAG 电商语义推荐系统**：用户用自然语言描述需求 → 系统从评论语料里召回相关商品片段 → LLM 生成带溯源的推荐解释。**这就是我们模块② RAG 导购 Agent 的目标架构**。

## 2. 完整架构

### 2.1 五阶段流水线

```
① 数据接入 & ETL（PySpark）
   Amazon Reviews 2023 → 清洗/标准化/去重 → 长评论切 passage（stride overlap 滑窗）
② Embedding 生成（Vertex AI 文本嵌入，维度 1024）
   批量生成 + retry/backoff + 幂等写 BigQuery
③ 向量索引（BigQuery ARRAY<FLOAT64> + ScaNN ANN）
④ 检索流水线：候选召回 k=50 → 重排 → 上下文组装 → LLM 生成
⑤ 服务（FastAPI）+ 前端（React/Next.js 聊天式界面）
```

### 2.2 AI 技术细节

- **数据预处理**：评论切 passage 用**带步长重叠的滑窗**（stride overlap），兼顾上下文连续性（避免切面切断语义）与控制 token 长度；按 product_id + 高相似度阈值双重去重
- **混合打分公式**：`final_score = α·vector_score + β·metadata_boost + γ·recency_boost`（α/β/γ 可调）
  - metadata_boost：类别/品牌/价格区间的元数据加分
  - recency_boost：新品/近期数据加权
- **检索**：ANN k=50 召回 → 轻量 BM25 式或 cross-encoder 重排 top-N（N≪k）→ 组装带溯源（source id + snippet）的上下文
- **Grounding（防幻觉核心）**：prompt 程序化组装（指令头 + 查询 + 有序上下文 + 生成约束），**LLM 只允许用给定 snippet 做事实性陈述**，回复附 provenance 链接；**截断/省略低置信度来源**
- **LLM**：Gemini-2.5-pro（LangChain 编排）

### 2.3 评测
- **离线**：留出查询集 → **MRR**（首个相关结果位置）+ **nDCG@k**（分级相关排序质量）+ Precision/Recall（特征/情感抽取）
- **在线**：逐请求延迟剖析（embedding/检索/重排/生成各阶段）、每 1000 查询成本估算
- **调参**：k（召回深度，越大召回越高但延迟成本↑）、N（重排数，越大精度越高但 prompt token ↑）、α/β/γ（混合权重）

### 2.4 badcase 与局限
- **幻觉/事实性风险**：检索结果质量差 → 生成不可靠 → 靠 truncate low-confidence + fallback 启发式兜底
- **passage 割裂**：切块可能切断「虽然……但是……」类转折语义
- **去重阈值敏感**：相似度阈值不当 → 漏相关变体或留冗余
- **评测范围有限**：只测检索相关性，没测推荐商业效果（转化率）
- **强依赖 GCP**：迁移成本高

## 3. 最值得借鉴的地方（对 beauty_agent）

1. **检索流水线结构**：召回(k)→重排(N)→上下文组装→生成 的标准四段式，我们直接抄这个骨架；k/N 是我们评测时能写进报告的两个超参
2. **Grounding 约束**：LLM 只用检索片段作答 + 溯源引用 + 截断低置信来源 —— 这就是我们「避雷准确率」的工程实现基础（不该推荐的不进上下文，进了也不准引用）
3. **混合打分 + 元数据 boost**：我们用 skin_tags/finish_tag 当 metadata boost，正好把我们「硬约束/软偏好」的匹配分数嵌进检索打分
4. **passage 策略**：我们若把好评文本也纳入检索（对标 benchmark 02），用 stride overlap 滑窗切，不硬切
5. **我们的差异点**：它用 BigQuery/ScaNN/Gemini 的 GCP 全家桶（重、贵、迁移难）；我们用轻量本地栈（MySQL + 本地 embedding + 开源 LLM）——「同样架构，成本低一个量级」是关键差异

## 4. 可落地到 beauty_agent 的三件事

- [ ] 模块② 检索层按「召回→重排→组装→生成」四段式搭建，k=20、N=5 起步
- [ ] 生成层强制 grounding：上下文只含匹配商品（含 skin_tags 命中原因），prompt 注明「只能基于给定商品属性作答，不知道就直说」
- [ ] 评测报告加「延迟 + 检索深度 k 的权衡」一节（对标在线评测）
