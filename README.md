# 基于Harness架构的beauty_agent — RAG 智能导购

一个**可真实使用的 AI 粉底导购**：基于 Amazon Beauty 真实商品与真实评论区构建检索底座，用「规则为主 + LLM 兜底」的方式做**确定性、可解释、诚实**的推荐。既是一个能跑的 Web 演示，也是一套带完整评测闭环的 RAG 工程样例。

一句话：**能答的绝不上模型，答不了的诚实说** —— 规则负责确定性与成本，LLM 只补规则的漏，且每一句推荐都有可追溯的库内证据。

## 核心设计

- **3 个显式决策节点**：要不要追问（信息不足先问）／检索不到要不要改写重试／无解要不要诚实兜底 —— 不是写死的线性脚本，Agent 自判断、自执行、自修复。
- **3 个决策指标**：追问率 / 降级率 / 兜底率，让「Agent 会不会乱来」变成可量化的数字。
- **证据回填（grounding）**：推荐卡片附命中原因（肤质/妆效/遮瑕…）+ 真实评论区差评证据；回复正文从库内标签确定性生成，**结构上不可能编造**。
- **诚实边界**：无解时直说 + 给替代方向，绝不硬推「看着沾边」的商品；资损防护（假价/假优惠/预算超限）靠回归门保证，不靠提示词自觉。
- **多语种 + 跨会话记忆**：中文/英文/其他语种自适应；匿名用户画像记住语言偏好与肤质，隔久重开先确认「还是以混油肤质为您推荐吗」。
- **驾驭层（Harness）**：会话预算（LLM 超限降级规则）、权限门、医疗越界护栏、全链路埋点回溯。

## 架构

```
 web/index.html  (双语对话页 · 商品卡片 · 避雷块 · 兜底块 · 决策透明面板)
        │  POST /api/chat {query, user_id}
        ▼
 scripts/web_server.py
   ├─ 语言路由 lang_router   : 中文→hybrid / 英文→rule / 其他→翻译成英文→rule
   ├─ 驾驭层 harness         : 会话预算 → 医疗护栏 → 全链路埋点 harness_trace.jsonl
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
   DeepSeek deepseek-v4-flash —— 意图必须能被检索兑现率门验证，否则降级回规则
```

## 评测指标

数据源：`eval_review_50` 评测集 + 候选池（每 query gold + 50 个负候选，分层采样）。入口 `python scripts/eval_runner.py`。

| 指标 | 值 | 说明 |
|---|---|---|
| 首答命中率（锚点） | **18/19 = 94.7%** | 干净命中 top-3（定标口径） |
| NDCG@5 | 0.553 | Phase-1 候选池 tagfirst |
| 避雷率 | 0.889 | 负例不进 top-5 |
| CONTRACT 硬断言 | **105/105** | 24 题决策正确性 |
| 追问率 / 降级率 / 兜底率 | 29.2% / 0% / 8.3% | 3 决策指标 |
| 资损陷阱拒绝率 | **8/8 = 100%** | 假价回显 / 假优惠 / 预算硬约束 |
| 二期 A/B（hidden 9 题） | A 0/9 → B 7/9 | 纯规则盲区 → 规则+LLM 兜底救回 7 题，锚点零漂移 |

坏例全部登记进 `data/badcase_report.csv`（query → 期望 → 实际 → 失败层 → 修复动作 → 回归结果），形成「评测 → 归因 → 定向修 → 回归」闭环。

## 快速启动

### 环境要求
- Python 3.9+（演示入口只需 pandas / numpy / requests）
- 完整评测链路另需 MySQL + `PyMySQL` + `SQLAlchemy`

```bash
pip install pandas numpy requests PyMySQL SQLAlchemy
# 可选（仅向量检索段 B，演示与锚点评测不需要）：
pip install sentence-transformers
```

### 1. 网页演示（推荐先跑这个）
```bash
python scripts/web_server.py          # 默认端口 7860，自动打开浏览器
```
- 英文输入 → 纯规则（确定性、零网络、零 key）；中文输入 → 自动走 LLM 兜底（需配置 key，见下）；其他语种 → 自动翻译成英文。
- 跨会话记忆、决策透明面板、中英示例题都在页面里可体验。

### 2. 配置 LLM 兜底（可选）
复制 `scripts/.env.example` 为 `scripts/.env`，填入：
```
DEEPSEEK_API_KEY=sk-xxx
```
不配置也能跑——LLM 超时/断连/无 key 一律自动降级回规则，整轮不崩。

### 3. 完整评测
完整评测需要本地 MySQL（`beauty_agent` 库，表结构见 [docs/database_schema.md](docs/database_schema.md)）。数据装载脚本为环境相关的一次性脚本，不随仓库分发；仓库内附评测数据 CSV 快照（`data/eval_review_50.csv`、`data/candidate_pool_v2.csv` 等），可自行装载后运行：

```bash
python scripts/eval_runner.py         # 锚点指标 + 坏例登记，输出 data/eval_report.csv
python scripts/eval_loss_risk.py      # 资损陷阱回归（零 LLM 零 key，8/8 全过）
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
│  ├─ harness.py                 驾驭层（会话预算 / 护栏 / 埋点）
│  ├─ lang_router.py             多语种路由
│  ├─ llm_intent.py / llm_gate.py  LLM 兜底（规则盲区 + 检索兑现率门）
│  ├─ contract_cases.py          确定性 CONTRACT 用例
│  ├─ eval_runner.py / eval_compare.py / eval_pool_v2.py / eval_report_grid.py
│  ├─ clean_products.py / defect_consensus.py / build_avoid_set.py / build_candidate_pool_v2.py
│  └─ db_config.py               数据库配置（从 .env 读取，仓库内无明文凭据）
├─ data/                         商品库 / 评测集 / 评测结果 CSV
├─ docs/                         设计文档（schema / 优化计划 / 评测报告 / 业界对标分析）
└─ 启动AI导购.bat                 Windows 双击启动
```

## 文档导航

- [docs/database_schema.md](docs/database_schema.md) — 数据模型、评测口径、意图双层架构、A/B 实验结论
- [docs/agent_design.md](docs/agent_design.md) — Agent 决策节点设计、坏例闭环全过程
- [docs/OPTIMIZATION_PLAN.md](docs/OPTIMIZATION_PLAN.md) — 分阶段优化计划与验收记录
- [docs/benchmarks/](docs/benchmarks/) — 业界 RAG/评测方法论对标（Amazon-C4、ESCI、steerable 检索等）
- [docs/eval_report_analysis.md](docs/eval_report_analysis.md) — 评测报告（复杂度×类型网格、模式消融、坏例归因）
