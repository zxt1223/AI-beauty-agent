# beauty_agent — RAG 智能导购

beauty_agent 是一个基于 **RAG（检索增强生成）** 的美妆粉底导购 Agent：以 Amazon Beauty 真实商品与真实评论区构建检索底座，用「**确定性规则为主 + LLM 兜底**」的方式提供**可解释、诚实、可审计**的推荐。项目包含一个本地可运行的 Web 演示，以及一套完整的离线评测闭环（锚点回归、决策断言、资损陷阱、消融对照），可作为 RAG + Agent 护栏工程实践的参考样例。

设计原则一句话：**能答的绝不上模型，答不了的诚实说** —— 检索与决策在规则侧完成，LLM 只在规则盲区兜底、在跨语言场景充当翻译桥，每一条推荐都带可追溯的库内证据。

> 说明：「Harness」在本文指 Agent 之上的一个**轻量管控层**（会话预算 / 医疗护栏 / 全链路埋点，见下），完整的企业级管控（多实例、在线 A/B、审核 SOP）是演进目标而非现状，演进路径见 [docs/enterprise_evolution.md](docs/enterprise_evolution.md)。

## 功能特性

- **显式决策节点**：信息不足先追问 → 检索不到改写重试 → 无解时诚实兜底。三个节点显式建模、可独立度量和审计，而非写死的线性脚本。
- **证据回填（grounding）**：推荐卡片附命中原因（肤质 / 妆效 / 遮瑕等）+ 真实评论区差评证据；回复正文由库内标签确定性生成，结构上避免编造。
- **硬避雷**：对差评扎堆的商品做「负面共识」硬过滤（某缺陷负面评论 ≥5 条且占该商品负面评论 ≥70% → 不进推荐），以评论共性缺陷优先于好评堆量。
- **跨会话用户记忆（匿名）**：记住语言偏好与肤质，隔久重开先确认「还是以混油肤质为您推荐吗」；用户可随时推翻旧记忆。
- **色号诊断**：色号是强个人化约束 —— 信息不足不猜色号，先给区间级结论再软问常用色号，会话内记忆。
- **医疗越界护栏**：命中治疗 / 用药语义（如「祛痘治好我」）附就医免责声明，且不误伤「痘痘肌选粉底」这类正常选品表达。
- **多语种路由**：中文走「规则 + LLM 混合」、英文走纯规则（零 LLM、低延迟）、法 / 阿 / 俄 / 西等经翻译桥进入英文规则。
- **资损防护**：假价回显、假优惠券、超预算等陷阱由回归门统一拦截，不依赖提示词自觉。
- **可观测与反馈闭环**：全链路请求埋点（输入 → 路由 → 约束 → 决策 → 推荐 → 护栏）落盘为一条流水账，每条推荐都能回查原因；前端 👍/👎 反馈经待审确认后回灌评测金标准。

## 架构

```
 web/index.html  (双语对话页 · 商品卡片 · 避雷块 · 兜底块 · 决策透明面板)
        │  POST /api/chat {query, user_id}
        ▼
 scripts/web_server.py
   ├─ 语言路由 lang_router   : 中文→hybrid / 英文→rule / 其他→翻译成英文→rule
   ├─ 管控层 harness         : 会话预算 → 医疗护栏 → 全链路埋点 harness_trace.jsonl
   └─ 用户画像(匿名)         : lang + 肤质记忆 → 注入 agent.run(profile=…)
        │
        ▼
 scripts/agent.py  GuideAgent.run(query, profile)
   ① extract_constraints   英文规则 + 中文显式规则层 → 约束/妆效/预算/隐式意图
   ② 记忆肤质注入           本轮未明说肤质 → 采用记忆画像
   ③ decide_ask            追问决策节点  ask_all / ask_first / ask_shade_soft / no_ask
   ④ _retrieve             意图分流 → 硬过滤(预算/避雷/死链/妆效) → tagfirst
                           (标签分→热度→BM25) → 候选不足 fill-in 补款
   ⑤ decide_fallback       诚实兜底决策节点（无解/矛盾 → 直说 + 替代方向）
   ⑥ _build_reply/evidence  证据回填 + 诚实话术 + 卡片结构化数据
        │
        ▼
 数据层
   data/products_clean.csv         商品库（Amazon Beauty 真实商品，标签枚举）
   data/product_defect_evidence.csv 评论区差评 → 缺陷证据轴（避雷依据）
   MySQL beauty_agent              候选池 / 评测集 / 评测结果（见 docs/database_schema.md）
   data/llm_cache.json             LLM 结果缓存（key 不落盘）
        │
  LLM 兜底（规则盲区才上）
   DeepSeek deepseek-v4-flash —— 意图必须通过检索兑现率门验证，否则降级回规则
```

**排序与模型分工**：主链路排序用 `tagfirst`（标签分绝对主序 → 热度 → BM25 文本兜底），其中热度 = 评分 × 评论量加成，作为无行为数据阶段的内容代理。核心决策全部在规则侧完成——12 通道消融实验显示，深度交叉编码器重排在标签约束主导的该类任务上并不优于规则排序（详见 [docs/dual_channel_analysis.md](docs/dual_channel_analysis.md)）；bge 向量 / 重排仅用于「规则够不着的语义 query」的兜底分支，且经置信度闸门二次判断后才放行，不凌驾标签硬约束。

## 评测指标

数据源：MySQL `eval_review_50` 评测集 + 候选池（每 query 的 gold 与负候选，分层采样），入口 `python scripts/eval_runner.py`。

> 评测分两套口径，引用数字请带评测集：**锚点域** = 24 道人工金标题（下表）；**v3 域** = 159 道真实评论区压力测试题（首答 26.7%，作为诚实基线）。两套分母与难度不同，不可直接比较。数据文件角色与行数见 [data/README.md](data/README.md)。

| 指标（锚点域，24 题人工金标） | 值 | 说明 |
|---|---|---|
| 首答命中率 | **18/19 = 94.7%** | 干净命中 top-3；分母 = 19 条可推荐题 |
| NDCG@5 | 0.553 | 排序质量（池内口径） |
| 避雷率 | 0.889 | 池内负例不进 top-5；全库避雷 24/24 |
| CONTRACT 硬断言 | **105/105** | 决策正确性（追问 / 兜底 / 硬过滤 / 证据齐全） |
| 追问率 / 降级率 / 兜底率 | 29.2% / 0% / 8.3% | 三个决策节点的分布 |
| 资损陷阱拒绝率 | **8/8 = 100%** | 假价回显 / 假优惠 / 预算硬约束 |
| 二期 A/B（hidden 9 题） | 规则 0/9 → 规则+LLM 兜底 7/9 | LLM 兜底在规则盲区有真实增量，锚点零漂移 |

坏例统一登记进 `data/badcase_report.csv`（query → 期望 → 实际 → 失败层 → 修复动作 → 回归结果），形成「评测 → 归因 → 定向修 → 回归」的闭环。

## 快速开始

### 环境要求
- Python 3.9+（演示只需 pandas / numpy / requests）
- 完整评测链路另需 MySQL + `PyMySQL` + `SQLAlchemy`

```bash
pip install pandas numpy requests PyMySQL SQLAlchemy
# 可选（仅语义兜底分支需要，演示与锚点评测不需要）：
pip install sentence-transformers
```

### 1. 网页演示
```bash
python scripts/web_server.py          # 默认端口 7860，自动打开浏览器
```
Windows 下也可直接双击 `启动AI导购.bat`。

- 英文输入 → 纯规则（确定性、零网络、零 key）；中文输入 → 自动走 LLM 兜底（可选配置 key，见下）；其他语种 → 自动翻译成英文。
- 跨会话记忆、决策透明面板、中英示例题都可在页面里体验。

### 2. 配置 LLM 兜底（可选）
复制 `scripts/.env.example` 为 `scripts/.env`，填入 `DEEPSEEK_API_KEY=sk-xxx`。
不配置也能跑——LLM 超时 / 断连 / 无 key 一律自动降级回规则，整轮不崩。

### 3. 完整评测
完整评测需要本地 MySQL（`beauty_agent` 库，表结构见 [docs/database_schema.md](docs/database_schema.md)）。数据装载脚本为环境相关的一次性脚本，不随仓库分发；仓库内附候选池 CSV 快照（`data/candidate_pool_v2.csv`，10702 行 / 200 题）与 v3 评测源（`data/_v3_eval_full.csv`，159 行）。评测真值以 MySQL `eval_review_50`（200 行）为准；`data/eval_review_50.csv` 是 v1 遗留的 11 行样例，勿当快照。运行：

```bash
python scripts/eval_runner.py         # 锚点指标 + 坏例登记，输出 data/eval_report.csv
python scripts/eval_loss_risk.py      # 资损陷阱回归（零 LLM 零 key）
python scripts/eval_compare.py        # A/B 对照（纯规则 vs 规则+LLM 兜底）
```

## 目录结构

```
beauty-agent/
├─ web/index.html                双语对话前端（零依赖单页）
├─ scripts/
│  ├─ web_server.py              演示入口（stdlib 零依赖后端）
│  ├─ agent.py                   GuideAgent（3 决策节点 + 证据回填）
│  ├─ retrieval_engine.py        检索引擎（BM25 / 标签分 / 向量三模式）
│  ├─ intent_reasoning.py        隐式意图推理 + query 改写
│  ├─ harness.py                 管控层（会话预算 / 医疗护栏 / 埋点）
│  ├─ lang_router.py             多语种路由
│  ├─ llm_intent.py / llm_gate.py  LLM 兜底（规则盲区 + 检索兑现率门）
│  ├─ contract_cases.py          确定性 CONTRACT 用例
│  ├─ eval_runner.py / eval_compare.py / eval_pool_v2.py / eval_report_grid.py
│  ├─ clean_products.py / defect_consensus.py / build_avoid_set.py / build_candidate_pool_v2.py
│  └─ db_config.py               数据库配置（从 .env 读取，仓库内无明文凭据）
├─ data/                         商品库 / 评测集 / 评测结果 CSV（角色与行数见 data/README.md）
├─ docs/                         设计文档（schema / 优化计划 / 评测报告 / 业界对标分析）
└─ 启动AI导购.bat                 Windows 双击启动
```

## 文档

- [data/README.md](data/README.md) — 数据文件资产清单与行数真相源
- [docs/database_schema.md](docs/database_schema.md) — 数据模型、评测口径、意图双层架构、A/B 实验结论
- [docs/agent_design.md](docs/agent_design.md) — Agent 决策节点设计、坏例闭环全过程
- [docs/OPTIMIZATION_PLAN.md](docs/OPTIMIZATION_PLAN.md) — 分阶段优化计划与验收记录
- [docs/metrics_glossary.md](docs/metrics_glossary.md) — 指标口径字典（各数字的分母与坑）
- [docs/enterprise_evolution.md](docs/enterprise_evolution.md) — 企业级演进路径（行为精排 / 多实例 / A/B）
- [docs/benchmarks/](docs/benchmarks/) — 业界 RAG / 评测方法论对标（Amazon-C4、ESCI、steerable 检索等）
- [docs/eval_report_analysis.md](docs/eval_report_analysis.md) — 评测报告（复杂度 × 类型网格、模式消融、坏例归因）
