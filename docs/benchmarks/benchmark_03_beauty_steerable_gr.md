# Benchmark 03 — Amazon Beauty Steerable Generative Retrieval（可操控检索基准，含避雷维度）

> **对标定位**：避雷维度（avoid_target）+ 多维度评测设计
> **项目来源**：[huggingface.co/datasets/xiaoleichu/amazon-beauty-steerable-gr](https://huggingface.co/datasets/xiaoleichu/amazon-beauty-steerable-gr)
> **上游数据**：`paischer101/preference_discerning`（MIT）；相关论文 arXiv:2412.08604
> **规模**：429,902 条示例 · 12,101 商品 · 22,363 用户（train 241k / val 94.7k / test 93.7k）

---

## 1. 项目是什么

**同数据源（Amazon Beauty）+ 评测维度最接近我们意图**的一个基准。它把「生成式检索」评测拆成 **6 个维度**，其中 **`avoid_target`（避雷）** 维度和我们的 `negative` 金标准完全同构。我们用「找商品」，它还考「不找商品」——这正是我们避雷准确率想测的事。

## 2. 完整架构

### 2.1 数据构造流程（Amazon Beauty → benchmark）
1. **数据源**：Amazon Beauty 5-core 评论 + 商品元数据（McAuley Lab）
2. **用户偏好生成**：用 **Llama-3-70B-Instruct** 根据用户评论历史生成自然语言偏好指令（`prompt_text`）
3. **情感标注**：用 `siebert/sentiment-roberta-large-english` 给评论打情感标签
4. **Semantic ID**：用 `sentence-t5-xxl` 编码商品标题 → **RQ-VAE 残差量化**成 3 个 code（各 ∈[0,255]）+ 第 4 个**碰撞 token**（区分共享同前缀的商品）
5. **增量会话构造**：同一用户的相邻样本，历史逐步追加「上一步目标」，目标顺延为下一步商品

### 2.2 六维评测设计

| 维度 | 定义 | 有 history | 对应我们的 |
|---|---|---|---|
| `preference_rec` | 基于偏好的推荐 | 是 | 我们主评测（首答准确率） |
| `history_consolidation` | 历史整合（"大海捞针"，长历史里找目标） | 是 | 可借鉴：多轮记忆 |
| `sentiment` | 情感跟随（旧版，无用户上下文） | 否 | 可借鉴 |
| `sentiment_with_history` | 情感跟随 + 保留历史（本仓库新增） | 是 | — |
| `fine_steering` | 偏好引导（细粒度，单条指令） | 是 | 我们 Query 粒度 |
| `coarse_steering` | 偏好整合（粗粒度） | 是 | — |

### 2.3 避雷维度（核心借鉴点）
- `expected_behavior` 两档：
  - **`retrieve_target`**：模型应生成/检索出目标商品（正常推荐）
  - **`avoid_target`**：**模型不应推荐该目标商品**（情感轴的负样本一半）——负面偏好的反向指令
- 例：用户吐槽某商品不好 → 指令里带负面偏好 → 模型若还推它 = 失败
- **这就是我们 eval_gold 里 `negative`（相关 -1，避雷）的行业标准形态**——我们把它落到「避雷准确率」指标

### 2.4 评测指标
- README 未给出公式；从任务形态（生成目标 SID / 全目录排序）推断为 Recall@K / NDCG@K 类检索指标（官方说明以论文为准）
- 评测有两种形态：**约束解码生成**（让模型生成 target SID）或 **全目录排序**（对 catalog 12,101 商品排序）

## 3. 最值得借鉴的地方（对 beauty_agent）

1. **「避雷」从我们的隐性负例 → 明确评测维度**：我们把 negative 做成显式 `avoid` 指令样本，评测时专门测「不该推的推了吗」，这就是对齐 steerable 的 `avoid_target` 维度。「避雷维度对齐学界 steerable 检索基准」
2. **多维度评测设计**：我们目前只有「单轮首答」一个场景。可以借鉴拆维度：主推荐（preference_rec）+ 避雷（avoid）+ 多轮/带历史（history consolidation），让评测报告更立体
3. **偏好指令的逐步演化**：`prompt_text` 随会话演化（"高遮瑕" → "自然裸妆" → "高色素浓度"），说明**同一用户偏好会逐步收敛/细化**——我们做多轮 Agent 时可用这个思路构造多轮评测
4. **我们的差异点**：steerable 的 prompt 是 **Llama-3-70B 生成的**（合成偏好），我们是**真实评论抽取**（真实偏好）——真实生态效度更高

## 4. 可落地到 beauty_agent 的三件事

- [ ] 评测指标表加一行：「避雷准确率」定义为 **avoid_query 场景下 Agent 未推荐 negative 商品的比例**（对标 avoid_target）
- [ ] 把 eval_gold 的 `negative` 样本从「来源差评商品」扩展到「评论中明确吐槽的属性」（如「会卡粉」「闷痘」→ 有这些属性标签的商品也归 avoid），对标情感负样本
- [ ] 六维评测设计可借鉴，本项目落地三指标（首答/NDCG@5/避雷）
