# 意图识别 · 隐藏意图推理规则表（v13）

> **定位**：这是 beauty-agent「意图识别升级 v13」的领域知识真相源——显式表达 → 隐藏意图的推理依据。
> **「用户表达是症状，要反推需求。」** 用户说「消除油光」不一定会说自己是油皮，说「防水」不一定说要去海边——query 短、上下文不足，必须用**领域世界知识**补全（对标 SSUF 的知识增强模块、DeepInterestGR 的深层兴趣挖掘）。
>
> 最近更新：2026-08-26

## 1. 为什么需要隐藏意图推理（问题定义）

现有 `intent` 是**规则层的显式意图**（10 轴多标签）：query 里出现什么词，标什么轴。局限：

| 显式表达 | 用户真正需要的（隐藏意图） | 只识别显式会漏掉 |
|---|---|---|
| "I need a **waterproof** foundation" | 防晒/SPF（防水底妆常配防晒；且说防水多是户外/水上场景） | 防晒属性匹配、防晒商品推荐 |
| "**eliminates shine**"（消除油光） | 油皮/混油肤质 + 哑光妆效（控油需求的属性多面性） | 肤质匹配（哑光商品、油皮适用） |
| "dry, dehydrated skin… moisture" | 干皮/混干肤质 + 保湿功效 | 肤质标签（已部分覆盖） |
| "over 60, blotchy, full coverage" | 熟龄肌（熟龄对遮瑕/保湿的复合需求） | 熟龄肌标签（无此轴） |

**核心原则**：隐藏意图 = 显式表达背后的**场景归因 + 属性多面性 + 功效关联**。

## 2. 推理规则表（显式信号 → 隐式意图）

每条规则 = 触发信号 → 隐藏意图标签 + query 改写注入词。触发分两种强度：

- **强信号（单条件）**：关键词本身即明确意图（`waterproof`、`over 60`、`dehydrated`、`lightweight`），无需 intent 轴配合——**修复显式识别漏报**（如 `extract_queries` 保湿轴正则漏 `dehydrated`/`moisture`，id=4「looking for moisture」靠隐式规则补偿）。
- **弱信号（双条件）**：关键词有歧义（`matte`/`shine` 可能是妆效描述而非控油诉求），需 intent 轴确认。

| # | 规则名 | 触发 | 隐藏意图 `implicit_intent` | 改写注入 `query_rewrite` |
|---|---|---|---|---|
| 1 | 防水→防晒 | 强信号：`waterproof / water resist / water resistant` | `防晒/SPF` | `SPF sun protection` |
| 2 | 户外/水上场景→防晒+防水 | 强信号：`vacation / beach / pool / swim / cruise / camping / outdoor / island / tropical` **或**「出行动词 `to` + 地点名词」（`going to Cancun`） | `防晒/SPF;防水持妆` | `waterproof SPF` |
| 3 | 控油→油皮/混油+哑光 | **弱信号**：`控油` 轴 ∩ `shine / oil control / blot / mattif / grease / matte` | `油皮/混油肤质;哑光妆效` | `matte oil control` |
| 4 | 保湿→干皮/混干 | 强信号：`hydrat / moisturiz / dehydrated / moisture / dry / flaky` | `干皮/混干肤质` | `hydrating dry skin` |
| 5 | 高遮瑕+熟龄→熟龄肌 | 强信号：`over 6X+ / aging / mature / senior` | `熟龄肌` | `mature skin` |
| 6 | 轻薄→轻薄质地 | 强信号：`lightweight / light feel / feels light / thin / not heavy` | `轻薄质地` | `lightweight` |

### 规则工程经验

1. **语法歧义**：场景规则初版用 `going to` 匹配出行意图，误伤 `"...isn't going to draw attention to my blemishes"`（`going to` + 动词 = "将要"，不是出行）。修复为「出行动词 `to` **必须跟地点/场景名词**」：`going to Cancun` ✓ / `going to draw` ✗。规则 = 语义匹配，不是表面词匹配。
2. **显式识别漏报的补偿**：`保湿` 轴正则漏 `dehydrated`/`moisture`，id=4 显式只标了肤质——隐式规则的强信号关键词把「干皮/混干」补了回来。意图识别要**显式 + 隐式双层**才完整。
3. **防误报优先级**：宁可漏报（不推防晒）不要误报（推无依据防晒）——所以弱信号规则保留双条件。
4. **语序鲁棒性**：轻薄规则初版只匹配 `light feel`，漏掉 `feels light`（id=11「feels light and lasts all day」）。修复为双向语序 `(light feel|feels? light)`——规则要覆盖自然语言变形，不是死正则。

## 3. 落库字段（eval_queries 新增）

| 字段 | 类型 | 含义 |
|---|---|---|
| `explicit_intent` | TEXT | 显式意图（规则层 10 轴，即原 `intent` 列的语义，兼容保留） |
| `implicit_intent` | TEXT | **隐藏意图（分号分隔）**：场景归因/属性多面性/功效关联 |
| `intent_source` | TEXT | 意图来源：`rule`（规则推理）/ `llm`（LLM 推理）/ `rule;llm`（双路一致）—— v13 只落 `rule`，`llm` 为预留策略位 |
| `query_rewrite` | TEXT | **改写后 query**（对齐 pangu_search_qp `revise` 模块）：原句归一化 + 隐式关键词注入，供检索/匹配用 |

## 4. 架构：规则为主 + LLM 预留（对齐 pangu_search_qp 双策略）

```
                       ┌─ 规则层（rule_strategy，现有）──→ explicit_intent
用户 query ──→ 意图识别 ─┼───────────────────────────────────┼─→ intent 融合
                       └─ 推理层（intent_reasoning）────────┘
                             ├─ 领域规则表（本文件，确定性、可审计）
                             └─ LLM 策略（预留：CoT 零样本推理，规则未覆盖时补盲；
                                            对齐 DeepInterestGR 多 LLM 挖掘 / FABRIC LLM 判断）
```

- **确定性**：规则表 100% 可复现，离线可跑，坏 case 可逐条回溯到规则。
- **可扩展**：`intent_source` 字段已预留 `llm`；接 LLM 后规则未覆盖的 query 走零样本推理，双路结果可对照。

## 5. 评测应用

1. **金标准驱动**：`implicit_intent` 参与 extras 属性匹配（见 database_schema.md §7）
   - `防晒/SPF` → 匹配 title 含 SPF 的商品（223 个候选，20.5%）
   - `哑光妆效` → 匹配 `finish_tag=哑光`（241 个候选，22.1%）
   - `油皮/混油肤质` → 匹配 `skin_tags` 含油皮/混油
2. **避雷增强**：隐藏意图也可进避雷（要防晒 → 避无 SPF 且暴露场景？—— 严格不适用，防晒是硬需求非可避项）
3. **query 改写**：`query_rewrite` 供 Agent 检索时扩展召回（Multi-Query 思路，对齐 benchmark05 L2 查询改写）
