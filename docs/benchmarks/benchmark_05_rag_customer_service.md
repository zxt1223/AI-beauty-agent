# Benchmark 05 — Agentic RAG 电商智能客服（LangGraph + Hybrid Search + 幻觉自纠正）

> **对标定位**：客服 Agent 工程架构（编排/检索降级/幻觉检测/badcase 闭环）
> **项目来源**：[github.com/auron-lmh/rag-ecommerce-customer-service](https://github.com/auron-lmh/rag-ecommerce-customer-service)
> **技术栈**：LangGraph、Milvus、Qwen3-vl-rerank、智谱 GLM-4-Flash、MySQL（30万订单）、Redis、Gradio

---

## 1. 项目是什么

一套**生产级 Agentic RAG 电商客服系统**：用 LangGraph 编排 13 节点工作流，检索用 Hybrid Search，生成后做**幻觉检测 + 自纠正闭环**，高敏承诺（价格/退款）有护栏，转人工有交接包。**这是「客服 Agent」里工程化最完整的开源参考**，badcase 优化机制尤其值得抄。

## 2. 完整架构

### 2.1 编排（LangGraph 13 节点 StateGraph）
主链路：`classify → check_human → retrieve → generate → evaluate → human_approval → rewrite + policy`
- **Agentic 回路**：`evaluate → human_approval → rewrite → retrieve → generate` 最多循环 3 轮
- **Human-in-the-Loop**：`interrupt_before` 真正中断图执行，外部注入审批结果
- **可观测性**：retrieve/web_search 是独立图节点，级联检索流程可观测

### 2.2 检索（多级降级是亮点）
- **L1 Hybrid Search**：BM25 稀疏(0.3) + Dense 稠密(0.7) + WeightedRanker 融合（Milvus + jieba 中文分词）
- **双路召回**：原始问题 + 改写问题并行检索 → 合并去重 → 精排（宣称召回 +30%）
- **复杂查询分解**：检测「和/对比/区别」类查询 → 拆子问题并行检索后汇总
- **Reranker 精排**：qwen3-vl-rerank，20 候选 → Top-5
- **5 级级联降级**：
  - L1 Hybrid → L2 LLM 查询改写（最多2次）→ L3 Multi-Query + HyDE 并行检索 → L4 联网搜索（GLM-4-Flash 优先）→ L5 **诚实兜底**：「无法确认，建议咨询人工客服」

### 2.3 生成 + 幻觉检测（badcase 核心机制）
- **幻觉检测**：G-Eval 风格但 **claims 级聚合**（明确「不信 LLM 自报标量」，逐条断言检查）
- **自纠正闭环**：检测 → 提取缺失信息 → 改写重搜 → 重新生成（最多 2 轮）
- **高敏承诺护栏**：价格/退款/政策承诺类回答，**忠实度需 ≥0.85**，否则转人工核验
- 单次请求 LLM 调用预算最多 8 次（防延迟爆炸）

### 2.4 评测集与指标
- **评测集构建**：49 条客服问答，**6 意图 × 3 难度**（每个意图覆盖 3 档难度）
- 金标准测试在 `src/evaluation/`，覆盖 9 项指标，一键复现 `python scripts/run_benchmark.py --with-generation`

| 指标 | 结果 | 算法 |
|---|---|---|
| Recall@5 | 0.755 | Embedding 余弦相似度（阈值0.7） |
| MRR | 0.725 | Embedding 相似度排名 |
| NDCG@5 | 0.728 | 同上 |
| Faithfulness | 0.72 | LLM G-Eval 精确 / Embedding 快速双模式 |
| 关键词覆盖率 | 0.976 | 字符串匹配 |

另有：幻觉率、纠正轮数（生成侧信号）、Latency Score（P50/P95/P99）。

### 2.5 badcase 优化机制（全：这是它最值钱的部分）
| 失败模式 | 优化手段 |
|---|---|
| 答错/漏答 | 幻觉自纠正（evaluate 不达标 → 改写重搜 → 重生成，≤2 轮） |
| 检索召回失败 | 5 级级联降级（改写 → Multi-Query/HyDE → 联网） |
| 高敏错误承诺 | 忠实度 <0.85 强制转人工 |
| 指代丢失（"上次那个券"） | 指代消解护栏：含指代词强制走 RAG |
| 多轮答非所问 | 智能重检索：判断追问/切换/澄清 |
| 愤怒/极端情绪 | 情绪识别四分级 → 直接转人工 + 交接包 |
| 幻觉注入 | 4 层防御（输入清洗/PII 脱敏/角色锚定/文档过滤）+ JWT 权限隔离 |

## 3. 最值得借鉴的地方（对 beauty_agent）

1. **评测集构建网格**：`6 意图 × 3 难度 = 49 条` —— 我们 908 条 Query 也应该按「意图 × 难度」分层标注，评测报告按格出数，能看出 Agent 在哪种难度翻车
2. **badcase 闭环流程**：把「评测 → 发现失败 → 归因（检索漏了/生成幻觉/指代丢）→ 定向修」做成标准流程，每个 badcase 归属到检索层/生成层/编排层——badcase 优化即讲这套闭环
3. **诚实兜底**：L5「无法确认就建议人工」——我们的避雷准确率要的就是这个行为；没有匹配商品时宁可不推荐也不瞎编
4. **检索降级链路**：我们模块② 可以从 BM25 + 向量混合起步，再逐级加「查询改写」等，形成可讲的多级降级
5. **评测工具化**：`run_benchmark.py` 一键跑评测出报告 —— 我们也要做一键评测脚本（eval_runner）

## 4. 可落地到 beauty_agent 的三件事

- [ ] 给 eval_queries 加 `intent`（肤质咨询/妆效咨询/遮瑕咨询/预算/避雷…）+ `difficulty` 两列，评测按网格出报告
- [ ] 建立 badcase 登记表：字段 = query / 期望 gold / 实际输出 / 失败层（检索/生成/编排）/ 修复动作
- [ ] RAG 生成 prompt 内置诚实兜底：「若检索结果无匹配商品，直接说明未找到并建议其他查询」，做进避雷准确率评测
