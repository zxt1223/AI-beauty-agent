# Benchmark 06 — Spring AI 跨境多语言客服 Agent（Evidence-Grade RAG + 确定性评测）

> **对标定位**：确定性评测体系（Agent CONTRACT Eval / RAG Eval）+ 证据级 RAG
> **项目来源**：[github.com/RyanCoreAI/spring-ai-crossborder-customer-service](https://github.com/RyanCoreAI/spring-ai-crossborder-customer-service)
> **技术栈**：Java 21 / Spring Boot 4.1 / Spring AI 2.0、PostgreSQL 16 + pgvector、Redis 7、RocketMQ、Vue3

---

## 1. 项目是什么

一套**多租户跨境电商客服 Agent 平台**，最值钱的是它的**评测体系**：`Agent CONTRACT Eval`（200 条确定性用例）+ `RAG Eval`（每租户 51 条 × 4 种检索模式），全部**无模型 key 可复现**。它对「评测怎么组织、证据链怎么展示、边界怎么诚实标注」给出了范本。

## 2. 完整架构

### 2.1 Agent 编排（受控流水线）
`Triage Router → Specialists（Order/Return/Product/Policy/Handoff）→ Safety Gate + Tool Allowlist → Tenant-scoped Commerce Tools → Response Composer + QA Snapshot`
- 核心原则：**「模型只提出工具调用请求，应用负责执行」**——工具在暴露给模型前和执行前都过 tenant/role/identity/risk/approval 五重校验
- 高风险动作（退款/取消/改地址/补发）只创建**内部审批请求**，LLM 不直接改外部平台
- Redis 锁 + 幂等键 + 超时/重试/熔断控制重复副作用

### 2.2 Evidence-Grade RAG（证据级检索）
`BM25 + Vector + RRF + 邻居窗口 + rerank fallback + context pack + evidence level + citation`
- **BM25 关键词 + Vector（pgvector，租户级 filter）双路召回 → RRF 融合**
- 邻居窗口扩展上下文片段
- rerank 失败有 fallback 降级路径
- 输出带 **evidence level（证据等级）+ citation（引用标注）**
- RAG Workbench 后台可视化证据链

### 2.3 评测体系（核心借鉴点）

**Agent CONTRACT Eval**（验证 Agent 行为契约）
- 构建：**200 条 deterministic CONTRACT cases**——确定性用例，**无模型 key 也可复现**（明确「不冒充人工 GOLD 数据集」）
- 输出：Markdown/JSON/JUnit 三格式，支持**工具选择/参数/引用/失败类别可回放**，记录 P95 latency
- 指标：通过率 / **Tool Precision**（正确工具调用/全部调用）/ Tool Recall / **Citation Coverage** / Poisoning Block（注入拦截率）/ No-answer（正确拒答率）

| 租户 | 用例 | 通过率 | Tool Precision | Citation Coverage | Poisoning Block |
|---|---|---|---|---|---|
| 1001 | 102 | 100% | 99.02% | 100% | 100% |
| 1002 | 98 | 100% | 100% | 100% | 100% |

**RAG Eval**（验证检索质量）
- 构建：每个检索模式 × 每租户 51 条用例
- 四种模式对比：BM25-only / Hybrid / Hybrid+Rerank / Vector-only
- 指标：MRR 0.90-0.91、nDCG@K 0.90-0.91、No-answer 100%、Poisoning Block 100%
- Vector-only 因默认无 embedding 模型 → **保留为诊断结果，不计入门禁**（诚实标注）

**诚实边界**：README 明确「人工 GOLD 数据集门禁未完成」；「简历不得写已达到 Gorgias/Zendesk 商业成熟度」——**不把 Fixture 冒充生产接入**。

### 2.4 数据与种子
- Flyway 自动迁移 MySQL + PostgreSQL/pgvector，加载 2 租户/10 客户/20 商品/30 订单/政策知识/评测数据
- 商品 → commerce cache/订单工具上下文；政策 → RAG 知识源

## 3. 最值得借鉴的地方（对 beauty_agent）

1. **确定性 CONTRACT Eval**：先做一批**输入→期望输出的确定性用例**（不需要调模型），验证「工具选择对不对、引用全不全、该拒答的拒不拒」——这是无模型 key 也能跑、能当场演示的评测。我们给 RAG 导购做 50-100 条确定性用例（如「敏感肌 + 痘痘肌 query 必须排除含酒精商品」）
2. **Tool Precision / Citation Coverage**：我们导购 Agent 的「工具」= 检索/排序/查评论，可以测「Agent 该调用检索时调用了吗」「推荐理由是否都来自商品属性（citation）」
3. **Evidence Level + Citation**：推荐回复带「证据等级 + 引用来源」——我们 prompt 让 Agent 标注「依据：XX商品的肤质标签」，评测 NDCG 时顺便测 citation 覆盖率
4. **多模式对比评测**：BM25-only vs Hybrid vs Hybrid+Rerank 四模式对比表 —— 我们模块② 应该输出一张**检索模式消融对比表**（数据驱动的技术选型亮点）
5. **诚实标注边界**：我们做评测也保持「哪些是自动生成、哪些未人工复核」的诚实标注——这本身就是可信度
6. **我们的差异点**：它 200 条 CONTRACT 是人工手写的，我们 908 条 Query 是真实评论自动抽取的——**我们更接近真实分布，且自动化可扩展**

## 4. 可落地到 beauty_agent 的三件事

- [ ] 建 `eval_guide.md` 时纳入确定性用例层：50-100 条 CONTRACT cases（输入 query → 期望行为断言），跑通后再上 908 条真实评测
- [ ] 模块② 输出检索模式消融对比表（BM25-only / 向量-only / 混合 / 混合+重排），每模式跑同一评测集
- [ ] 推荐回复要求带「依据」引用（商品属性来源），评测指标加 Citation Coverage
