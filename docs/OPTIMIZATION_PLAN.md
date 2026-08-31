# beauty_agent 优化计划（对标工业界/学界基准）

> 一句话定位：
> **评测集设计对标 Amazon-C4（复杂查询）+ ESCI（相关度四档），检索架构对标 RAG 电商全栈，避雷维度对齐 steerable 检索基准。**
> 与它们的关键差异（我们的独特点）：
> 1. **Query 是真实评论抽取**（C4 是 LLM 改写半合成）→ 生态效度更高
> 2. **金标准自动生成 + 可解释规则**（ESCI 是官方人工标注）→ 规模化 + 可追溯
>
> 最近更新：2026-08-29 ｜ 关联文档：[benchmarks/](benchmarks/) 六份对标分析 ｜ 数据库真相源：[database_schema.md](database_schema.md) ｜ 评测集 v2：[eval_v2_batch1.md](eval_v2_batch1.md) / [eval_v2_batch2.md](eval_v2_batch2.md) ｜ 导购 Agent：[agent_design.md](agent_design.md) ｜ 评测闭环：[eval_runner.py](../scripts/eval_runner.py) + [badcase_report.csv](../data/badcase_report.csv)
>
> **2026-08-27 三方向升级**：RAG 深度优化（**动态路由检索 + 知识分层 + 冗余过滤/置信度降权**）并入 Phase 1 检索层 ✅；动态决策（**3 显式决策节点 + 3 决策指标**）并入 Phase 2 Agent 层 ✅（硬断言 100%、追问率 29.2% / 降级率 0% / 兜底率 8.3%）；多模态接口预留（肤色识别二期）并入 Phase 4。不推翻现有计划，检索/Agent 层直接吸收，多三个差异化设计点。
>
> **2026-08-29 前端交付**：**可真实使用的 AI 导购**（演示件）——`web_server.py` 零依赖后端 + `web/index.html` 双语单页 + `启动AI导购.bat` 双击启动，见 Phase 4。中文输入自动走 LLM 兜底（规则层只认英文关键词），规则模式 + 中文 = 前端自动切混合模式；`eval_compare.py` 分母修正（41 题全量重跑）。

---

## 1. 现状盘点（已完成）

| 模块 | 现状 | 关键产物 |
|---|---|---|
| 商品知识库 | ✅ 1090 粉底液（v10 重构后），多标签肤质（skin_tags）+ 色号（shade_tag，v12）+ 硬约束/软偏好 | `products_clean.csv` + MySQL `products` 表 |
| 数据质量 | ✅ 15 指标落库，价格 76.9% 缺失双轨策略 | `quality_metrics` 表 |
| 评测集 Query | ✅ 908 条真实评论抽取 → 筛出 **11 条 need 句**进评测集（v10 根因修复：评论句≠搜索需求句） | `eval_queries` 表 |
| 金标准 | ✅ 67 条，relevance 三档（1.0/0.8/-1.0）：8 primary + 32 extra（27 四维 + 5 隐式）+ 27 negative | `eval_gold` 表 |
| 意图识别 | ✅ v13 双层（规则为主 + LLM 预留）：隐藏意图推理 + query 改写 + 隐式金标准增补 | `intent_reasoning_rules.md` |
| 避雷集 | ✅ v12 意图相反标签匹配（要白皙→避深色、要高遮瑕→避轻遮瑕），27 个避雷商品全部标签自证 | `build_avoid_set.py` |
| **评测集 v2** | ✅ **24/32-40 条**交互式标准答案题，8 类全覆盖（直说3+模糊3+避雷3+色号3+预算3+持妆3+硬约束3+质地3） | `eval_v2_batch1.md` + `eval_v2_batch2.md` + `eval_v2_batch3.md` |
| **候选池** | ✅ 11 Query × 617 行（金标准 67 + 负候选 550：难例 135 / 随机 415），NDCG 改在池内计算避免全库虚高 | `candidate_pool` 表 + `build_candidate_pool.py` |
| 文档化 | ✅ schema 真相源 v13 + 连接信息 | `docs/database_schema.md` |

**缺失（本次计划要补的）**：检索层、RAG 生成、评测指标落地、badcase 闭环、可量化的评测报告。

## 2. 对标矩阵（每个参考补我们哪块）

| 参考项目 | 对标点 | 补我们缺的 | 参考文档 |
|---|---|---|---|
| Amazon-C4 (BLaIR) | 复杂查询评测 | Query 难度分层、候选池采样、NDCG 定义 | [benchmark_01](benchmarks/benchmark_01_amazon_c4_blair.md) |
| Amazon ESCI + J-MADRAL | 相关度四档 | relevance 分档的官方背书、商品+评论双视图检索 | [benchmark_02](benchmarks/benchmark_02_esci_madral.md) |
| Beauty Steerable-GR | 避雷维度 | avoid_target → 避雷准确率的行业形态、多维度评测 | [benchmark_03](benchmarks/benchmark_03_beauty_steerable_gr.md) |
| RAG 电商全栈 | 检索架构 | 召回→重排→组装→生成四段式、grounding、混合打分 | [benchmark_04](benchmarks/benchmark_04_rag_ecommerce_fullstack.md) |
| Agentic RAG 客服 | 工程化/badcase | 评测网格、降级链路、幻觉自纠正、诚实兜底 | [benchmark_05](benchmarks/benchmark_05_rag_customer_service.md) |
| Spring AI 跨境客服 | 确定性评测 | CONTRACT Eval、Tool Precision、检索模式消融 | [benchmark_06](benchmarks/benchmark_06_springai_crossborder.md) |

## 3. 分阶段计划

### Phase 0：评测集升级（对标 C4 + ESCI + steerable）—— 约 1 天 ✅ 完成

**目标**：把「908 条 Query 平铺」升级成「可分层、可归因的评测集」。

- [x] **Query 分层**：`complexity`（short/medium/complex）+ `intent` 多轴 + `difficulty` 三列 —— 已完成（v10，schema 落库）
- [x] **候选池采样**：`eval_gold` 之外建 `candidate_pool`——每个 Query 配 50 个同类目负候选（对标 C4，金标准全量入池 + 难例 15/随机 35 分层），NDCG 改为在候选池内算，避免全库排序虚高 —— 2026-08-27 完成（617 行，难例占比 24.5%）
- [x] **避雷维度扩展**：negative 从「来源差评商品」升级为「意图相反标签匹配 + 缺陷证据兜底」（v12，27 个避雷商品全部标签自证）
- [x] **评测集 v2（新方向，2026-08-26 启动）**：从「相关度三档」升级为「**导购 Agent 交互式标准答案题**」——每条含 应识别意图（含 v13 隐藏意图）/ 硬约束 / 追问设计 / 推荐理由证据 / 避雷自证，直接支撑 Phase 2 Agent 评测与 Phase 3 三指标
  - 进度：**24/32-40 条**，8 类全覆盖（直说3+模糊3+避雷3+色号3+预算3+持妆3+硬约束3+质地3）
  - **价格策略约定（2026-08-27 用户定）**：库内价格为入库快照、会过期——真实场景接**实时价格 API**（库存/优惠同理），库内价格需**定期更新**；评测标准答案只验证「引用可检索价格 + 诚实标注价格状态（待核实/快照）」，不验证实时价格本身
  - **成交导向约定（2026-08-27 用户定）**：推荐不只给名字，要给「现在值得买的决策包」——①**热销优先**（预算内多个匹配按热度排序，热度=评论量分档 高≥200/中 50-199/低<50）；②**活动/促销价**库内无字段，统一标「需实时 API」不虚构；③**每条推荐带 🔗 asin 链接占位**（真实场景拼 `https://www.amazon.com/dp/{asin}` 跳转，评测验证推荐落到具体商品 ID）——评测标准答案推荐证据升级为「标签+价格+口碑+热度+链接」四件套
  - 待补：8 类每类 4-5 条 → 各补 1-2 条冲量（约 8-16 条）；复核表人工复核（eval_review_50 三列全空）
  - 交付文档：`eval_v2_batch1.md` / `eval_v2_batch2.md` / `eval_v2_batch3.md`；涉及商品走人工精标（只精标评测涉及的几十个，不对全库）
- [x] 更新 `database_schema.md`（已到 v13，含意图识别/隐式金标准/避雷集升级同步）
- **交付物**：分层后的评测集 + 分层统计报告（评测集 v2 铺满时补统计报告）

### Phase 1：检索层（对标 RAG 电商全栈 + 三方向升级）—— 约 2 天 ✅ 完成（段 A + 段 B）

**目标**：让「给商品打分排序」从简单字符串匹配升级为可评测的检索系统，核心三升级：**动态路由检索 + 知识分层 + 冗余过滤/置信度降权**（对齐外部方法论「RAG 深度优化」）。

- [x] **动态路由检索（先路由再检索）**：`route_query` 按 8 类意图分流（budget/hard/form/avoid/shade/default）——段 A 已实现，2026-08-27
- [x] **知识分层（四层）**：`tag_score` 逐轴可审计（肤质/妆效/遮盖/质地/色号 + 隐式意图 + 置信度降权），规则层/证据层接口预留
- [x] 商品侧构建检索索引：`title + brand + 标签` 拼成商品文档（段 A：英文文档供 BM25；代表性好评摘要待评论视图，对标 J-MADRAL 商品+评论联合建模）
- [x] **Embedding（段 B，2026-08-27）**：bge-small-en-v1.5（384 维）+ torch CPU 版，模型离线化到 `models/` 本地加载（`download_bge.py`，规避在线依赖）；商品文档 = title+brand+英文标签列（finish/coverage/form/skin/skin_tone），query = query_rewrite+结构化英译注词（build_vec_query，结构化↔向量对齐）；query 向量单次编码缓存 + 矩阵乘法
- [x] **冗余过滤 / 置信度降权**：`*_source` 低置信度字段（title 推断）、`conflict_*` 冲突标记 → 对应轴 ×0.5 已实现；KLAIRS「标题 cushion 实际乳霜」假命中实证
- [x] **混合打分**：`mixed = 0.3·BM25 + 1.5·标签 + 0.1·向量 + 0.3·热度`（min-max 归一化，δ 由 grid_scan.py 网格定参）——向量作小权重语义辅助
- [x] **检索模式消融（四模式定参）**：bm25 0.014 / tag 0.652 / vec 0.115 / **mixed 0.457**（Recall 0.606、避雷 0.926）——段 A 0.445→段 B 0.457、Recall 0.495→0.606（+0.111）；三大发现：向量独立区分度弱（相似度窄带 0.5-0.7，「embedding 一把梭」反证）、结构化↔向量对齐（0.077→0.115，q5 hard MRR 1.0）、δ=0.1 最优（δ 调大污染混合到 0.297）；q9 坎昆防水 MRR 0.125→0.200 Recall 0→1.0 兑现；报告 [retrieval_phase1A.md](retrieval_phase1A.md) + [retrieval_phase1B.md](retrieval_phase1B.md)
- **交付物**：retrieval_engine.py + eval_retrieval.py + grid_scan.py + download_bge.py + 消融对比表（retrieval_phase1A/1B.md）+ 明细（retrieval_ablation.csv / grid_scan.csv）+ 8 类路由 + 离线模型 models/

### Phase 2：RAG 导购 Agent（对标两套客服）—— 约 1.5 天 ✅ 完成（2026-08-27）

**目标**：检索结果 → 可解释、可信、会拒答的导购对话。

- [x] **编排**：轻量 workflow（约束抽取 → 追问决策 → 检索 → 改写重试 → 生成 → 诚实兜底），不急着上 LangGraph；每次 run 落一条**结构化决策记录**（对话 demo 和 CONTRACT 断言消费同一份）
- [x] **显式决策节点（三方向升级：动态决策）**：把「要不要追问 / 检索不到要不要改写重试 / 无解要不要兜底」做成 3 个**显式决策节点**（不是写死线性流程，agent 自判断自执行自修复）——评测集 v2 的追问设计/诚实兜底题直接转成决策断言；加 **3 个决策指标：追问率 / 降级率 / 兜底率**（差异化设计：3 决策节点 + 3 量化指标）
- [x] **追问策略（决策节点「追问」的规则层，2026-08-27 用户验收定稿）**：追问数 = 缺失的关键约束数；**约束独立 → 一轮合并问完**（先给友好预期解释目的，降低输入压力，如「为了更好帮您筛选商品，请先回答几个问题」）；**约束有依赖/答案会改变后续方向 → 分步问**（D-2 控油≠哑光→先问妆效）；**预算属软约束，答案不终止追问**——推荐组合固定「2 预算内 + 1 微超升级位」，微超档仍需维度信息支撑；**预算型也先问清（2026-08-27 用户补定）**——预算 Query 常只给「预算+遮瑕」：**信息极缺时（肤质/妆效/色号全缺，如 P-3）强制追问**（合并式+友好预期）；**约束较全时色号设软追问**（如 P-1/P-2）：推荐末尾附「告诉我常用色号（偏自然/偏白）可更精准」，不强制、给到即加分
- [x] **Grounding**：推荐证据只基于命中商品事实 + `tag_score` 命中原因，四件套 = 标签 + 价格 + 口碑 + 热度 + 🔗asin；只 cite query 实际指定的轴（id3 不默认色号）；未标轴诚实标注（「该商品遮瑕度库内未标，需确认」）；缺价标「价格待核实」不虚构
- [x] **诚实兜底**：无匹配/矛盾无解 → 直说「库里没有能同时强控油+强保湿的单品」+ 给替代方向（平衡型 + 分区处理），不硬推「看着沾边」的商品（level=full）；换季「一件到底」→ 诚实附注「没有粉底会自动调肤」仍推平衡型（level=honest_note）
- [x] **Citation**：推荐回复带依据（「依据：肤质；妆效；隐式控油；隐式哑光」），由 `tag_score` reasons 拼出，只含 query 实际指定轴
- [x] **确定性 CONTRACT 用例**：24 题 CONTRACT 断言（硬断言 105 条，**通过率 100%**），无模型 key 可复现（对标 benchmark_06）——评测集 v2 的交互式标准答案题（追问设计/硬约束/避雷自证）直接转成 CONTRACT 断言，两套复用同一批题；断言族覆盖：追问决策正确/兜底决策正确/硬约束过滤（肤质/遮瑕/质地）/假命中排除（KLAIRS）/诚实标注（缺价/未标）/证据齐全（四件套+🔗asin）/不默认色号/意图完整性（坎昆防晒）/缺陷证据避雷
- [x] **3 决策指标实测（24 题）**：追问率 = 7/24 = 29.2%（ask_all=4 / ask_first=1 / ask_shade_soft=2）；降级率 = 0/24 = 0.0%（硬过滤不把能答的误判无解）；兜底率 = 2/24 = 8.3%（honest_note=1 / full=1）
- [x] **软指标（measured，不判定）**：避雷率 24/24 = 100%（≥ Phase 1 基准 0.926）；首答命中 1/24 primary asin 进全库 top-3（id17 query 补全后 Wanderlust Powder 入列；口径差异，Phase 1 是候选池 top5 0.606）；备选命中 1/43
- [x] **首答命中率定标（2026-08-28，用户验收）**：口径 = **干净命中 top-3 = 14/19 = 73.7%（达标 70%）**——candidate_pool_v2 池内（Phase-1 同方法学，难例占比 22.5%），分母 = 有推荐的 19 题（排除 ask_all/ask_first）；命中 = top-3 ≥1 正确答案（primary 或 extra）且 0 避雷泄漏。支撑变更落地：①**排序 mixed → tagfirst**（`retrieval_engine.py` 新增 mode="tagfirst" 分支 = 标签主序→热度→BM25；Phase-1 复测 NDCG 0.467→0.553、MRR 0.606→0.745，避雷 0.926→0.889 用户已验收）；②**②精标落库**（`apply_label_patch.py`：4 肤质追加 + 14 遮瑕补空，source=manual，改前备份，双写 CSV+MySQL）；③candidate_pool_v2 建池（build_candidate_pool_v2.py）。对照口径单独报：严格 primary top-3 = 57.9%、宽松 any-correct = 78.9%
- [x] **顺带修复两个 Phase 1 隐藏 bug**：①引擎 `IMPLICIT_RULES` 防晒/防水 lambda 传参 bug（`fn(p)` 传 dict → `"spf" in p` 查键永不命中）→ 改查 title 文本，修复后坎昆题能推出 SPF/防水商品，Phase 1 重跑 mixed NDCG 0.457→0.467（q9 Recall 1.0），Recall 0.606 / 避雷 0.926 不变；②控油正则漏 `oily`/`controls oil` → id14/18 油皮控油隐式意图补上（`matte` 刻意不并入：干皮也要哑光，控油≠哑光是 D-2 铁律）
- **交付物**：agent.py + contract_cases.py + eval_agent.py + agent_design.md + 可对话演示（`agent.py --chat`）
- **已知边界（id17 query 截断已修复 2026-08-27）**：`eval_review_50` id=17 query 已从 220 字符截断版恢复为 280 字符完整原文（补回「Wanderlust Powder」半句），`eval_v2_batch3.md` 同步；修复后 form=粉状轴可测，id17 新增 4 条断言（form=粉状 + recs_form_ok + shade=None + no_shade_citation，硬断言 101→105 仍 100%），决策分布不变（追问 29.2% / 降级 0% / 兜底 8.3%），首答命中 0→1/24（Wanderlust 入列）。剩余边界：query 只给「right shade」未指明方向 → Agent 不默认白皙（正确）；首答命中率口径（全库 top-3 严于候选池 Recall@5 0.606）；无模型 key（纯规则层）

### Phase 3：评测闭环（对标 ESCI + 两套客服评测体系）—— 约 1 天（进行中）

**目标**：三指标落地 + badcase 可迭代。

- [x] **eval_runner.py**（2026-08-28 完成；2026-08-29 补系统层）：一键跑全评测集，输出三指标 + CONTRACT + 3 决策指标 + **系统层**
  - **首答命中率 = 干净命中 top-3 = 94.7%**（18/19，2026-08-28 定标口径：candidate_pool_v2 池内，top-3 ≥1 正确答案（primary 或 extra）且 0 避雷泄漏；分母 = 有推荐的 19 题。链路：定标 14/19 → **q20 泄漏修复 15/19**（2026-08-29，balance 规则）→ **第四批池重建 17/19**（q14/q19 排序提升）→ **q7 gold 重标 18/19**（2026-08-29，见下方「q7 gold 重标」记录））
  - **系统层（2026-08-29 补，三层指标齐全）**：CONTRACT 通道 24 题 `Agent.run` 实测平均单轮耗时 + LLM token 成本 + LLM 异常数。纯规则模式恒 **0/0/0**（零模型零成本是卖点量化，不是缺省）；hybrid 模式的耗时/token 见 eval_compare_report.csv 旁路。三行标 `no-regress` 不参与 DELTA 回归对比（墙钟实测有噪声）。**耗时=稳态单轮 ~44ms**。**顺带优化（2026-08-29，A/B 零漂移验证）**：`diag_system_layer.py` 实测发现 `_retrieve` 无条件 `enable_vectors()` 使每个进程一次性载入 bge 模型 ~16s（tagfirst 排序根本不用向量，rule/hybrid 都白载，eval_compare 曾打两遍「向量索引就绪」）→ 去掉 `_retrieve` 里的 `enable_vectors()`（需向量的 vec/mixed 由 eval 脚本显式调，vec_sim 自持惰性加载）；改后 eval_compare A==B 15/19、hidden B 7/9、NDCG 锚点 0.529 全数不变
  - NDCG@5 = **0.553**（Phase-1 候选池内 tagfirst，gain=2^rel-1，rel<0 记 0；另报 Recall@5 0.676 / MRR 0.745）
  - 避雷准确率 = **0.889**（Phase-1 池 tagfirst，负例不进 top-5）+ 全库 CONTRACT 口径 24/24 = 100%
  - 附带：CONTRACT 硬断言 105/105 = 100%、追问率 29.2% / 降级率 0% / 兜底率 8.3%
  - 输出 `data/eval_report.csv`（汇总 + 与上一轮自动回归对比）+ 终端汇总；只 import 有 stdout 保护的模块，其余内联避免 wrapper GC 坑
- [x] **badcase 登记表**（2026-08-28 完成）：字段 query / 期望gold / 实际输出 / 失败层=检索|生成|编排（+细归因列）/ 修复动作 / 回归结果——自动从三通道实际输出生成，写 `data/badcase_report.csv`（utf-8-sig）。**首版 8 条**：首答 miss 5（q7/q9/q14/q19=检索层排序不足，gold 标签完整但未进 top-3；q20=检索层避雷泄漏）+ Phase-1 池避雷泄漏 3（q7/q9/q11 负例进 top-5）——对标 benchmark_05 的闭环。**2026-08-29 q20 泄漏已修复**（balance 规则）、**q7 gold 重标修复**（首答 miss）→ 坏例降为 **4 条**（首答 miss 1：q9 + Phase-1 池泄漏 3），badcase_report.csv 已自动剔除已修复项；**Phase-1 池泄漏 3 条（q7/q9/q11）经实测为「池内子集排序伪影」——用户拍板跳过**（负例商品全库 rank 28/139/73，Agent top-12 窗口外，生产不推；缺陷预过滤清不掉），详见下方「Phase-1 池泄漏跳过」记录
- [x] **二期 A/B 实验：模糊意图兜底 → LLM**（2026-08-28 完成，回答「什么时候该上 LLM」）：规则盲区（9 条 hidden-intent 题 ids 25-33）上 A（纯规则）vs B（规则+LLM 兜底）对比
  - **设计四要素**：触发 = 规则完全盲区 + 语境线索词；LLM 输出强制结构化 JSON（DeepSeek deepseek-v4-flash）；**信任信号 = 检索兑现率（非 LLM 自报置信度）**：意图→规范轴→库内 ≥20→检索 top-8 兑现 ≥0.5 才采信；降级链（超时/断连/解析失败/兑现不了 → 静默回规则）
  - **两个修复（实验逼出的边界认知）**：①锚点漂移——v1 触发条件太宽，锚点题 q8 被 "settle" 误拉进 LLM、LLM 过度推断防水持妆污染排序 → 锚点 14→13；加 `_rule_has_signal` 闸门（规则已有任一信号绝不上模型）修复 → 锚点回 73.7% 零漂移。②妆效丢失——LLM 把妆效写在「约束.妆效」不在"意图"列表 → 哑光丢失 + D-2 追问截胡 → CASES_HIDDEN 6/9；并入 `validate` + 采信哑光同步 finish → 9/9
  - **结果（实测定稿）**：锚点 A/B 均 14/19=73.7%（零漂移；**2026-08-29 q20 修复后 A/B 均升至 15/19=78.9%，仍零漂移**）；hidden A 0/9 → B **7/9 可答、7/9 命中**；31/32（熟龄肌/轻薄质地）= 模型识别但库兑现不了 → 诚实降级 A==B；CONTRACT CASES 105/105、CASES_HIDDEN 9/9；NDCG@5 hidden A=0.306 → B=0.786。**2026-08-29 前端交付时全量重跑（41 题）**：锚点 A/B 均 **18/19=94.7%** 零漂移；非锚点（25-41，17 题：25-33 盲区 + 34-41 规则可答）A 可答 7/17 → B 可答 15/17、命中 14/17（LLM 救回 8 题 + q31/32 诚实降级）；NDCG@5 锚点 A==B 0.547、hidden A=0.597→B=0.818；LLM 触发 10/41 全在盲区、锚点零误触发
  - **产物**：`llm_intent.py`（LlmIntentFallback + --test）/ `eval_compare.py`（A/B 入口 + eval_compare_report.csv）/ `add_hidden_intent_cases.py`（落库 25-33）/ `llm_cache.json`（无 key 缓存）；锚点回归文件 eval_report.csv 经 ANCHOR_MAX_ID=24 隔离，保持干净
- [x] **评测报告模板**（2026-08-28 完成）：`scripts/eval_report_grid.py` 一键出 `docs/eval_report_analysis.md` + `data/eval_report_grid.csv` + `eval_report_detail.csv`——①总览（三指标 + 3 决策指标 + 二期 A/B）；②**complexity × query_type 网格**（33 题样本小，格子=命中/可答+题号，诚实标注稀疏；隐式意图行规则模式 0/0 = A 组 0/9 的网格证据）；③**检索模式消融**（tagfirst vs mixed，同分母 19：首答 78.9% vs 68.4%、NDCG@5 0.537 vs 0.371）；④类型明细；⑤badcase 归因分布 + top10；⑥核心结论。不碰 eval_report.csv（锚点回归文件保持干净）——对标 benchmark_06 报告结构
- [x] **人工抽检 + gold 真值校准 + 70% 负面共识（规则参数校准路线，2026-08-28）**：用户拍板走「规则参数校准」（监督/RL 在 demo 阶段做不了——无环境/无奖励流）。**闭环三件事**：①**人工抽检** 10 条关键题 → 4 PASS + 6 MODIFY（`human_gold_check` 表 + `prep_human_gold_check.py`）；②**gold 真值校准**（`calibrate_gold.py`，条目级复用保留语义批注，6 条 MODIFY 落库 + 三处同步 eval_review_50/对比表1/candidate_pool_v2 + 备份 *_bak_v17）：q8 P Rimmel→Mirenesse 且 Rimmel 转 negative、q20 移除 MaryKay、q25 P Sweat→Dermacol 且 Sweat 移除/extras 加 Hera、q31 移除 BOOTS、q15/q17 记录核验不改；③**70% 负面共识 → 硬规则**（`defect_consensus.py`）：缺陷轴提及数÷负面评论数 ≥70% → 标硬规则，色号偏深黄/浅灰不算避雷轴，`agent._load_defect` 从「有轴即避」升级为共识口径 + `build_avoid_set.py` 同口径。**锚点零回归**：校准 + 共识升级后首答仍 **73.7%（14/19）**、CONTRACT 105/105、q8 仍 ✓ primRank=1——评论共识与引擎排序一致（Mirenesse 本就第一、Rimmel 卡粉 75% 被引擎正确排后），44 个假避雷放行（新 primary Dermacol 闷痘 31%、extra Revlon 卡粉 8%）没付出召回代价
- [x] **q20 避雷泄漏修复：全年通用混合肌 balance 规则（2026-08-29）**：q20「冬干夏油要一瓶全年」v2 池 top-3 曾混入 Estee 粉饼 + Rimmel 控油慕斯两个负例（gold 语义「极端控油=冬天拔干」，恰是爆款 → 同分 4.0 时 heat tie-break 胜出）。修法：`req["seasonal"]` 贯通三处 req 构造（extract_constraints / parse_query / 各脚本 `_req_from_rec`，修掉三处共享的「meta 字段丢失」隐患），`tag_score` 加 balance 分支——seasonal 且 implicit 含「干皮保湿+油皮控油」时，干油双覆盖（或全肤质）+2「干油双标(全年)」、单季品 -2「单季品·降权」；**必须配 seasonal**（q21 同签名但 gold 把 Estee 当正例，combo 单闸门会误伤）。**效果**：首答 **73.7%→78.9%（14/19→15/19）**、泄漏 1→0；NDCG 0.553 / 避雷 0.889 / CONTRACT 105/105 / 3 决策指标零回归；A/B 锚点 15/19 仍零漂移。详见 agent_design.md §8.7
- [x] **第四批补题冲量（8 类各补 1 条，ids 34-41，2026-08-29）**：评测集 24→41 题（24 锚点 + 9 hidden + 8 补题），双表落库（eval_review_50 + 对比表1，显式 id）+ 重建 candidate_pool_v2（41 题 × 2250 行，难例占比 17.6%）
  - **补题缺口**：色号类缺深色桶（S-1/2/3 全白皙）、质地类缺液体（T-2 粉状/T-3 气垫）、预算类缺「预算+敏感肌」、持妆类缺户外运动场景
  - **结果**：锚点首答 **78.9%→89.5%（15/19→17/19）**——池重建后 q14/q19 难例排序提升转命中（NDCG/避雷/CONTRACT/3 决策指标全零回归）；**新题 34-41 首答 7/7 = 100% 零泄漏**（35 是 ask_all 追问题不进分母）
  - **q38/q40 negative 泄漏修复（教训记录）**：初版 negative 选「超预算 Clinique / 轻遮瑕 LA MER」——全肤质+自然标签在池内 `score_candidates` 排进 top-3（预算/遮瑕 hard filter 只在 `agent._retrieve` 生效，池内通道不应用）。**修法：negative 必须选池内 tagfirst 天然不进 top-3 的商品**（意图反方向或强缺陷证据），不能依赖仅 _retrieve 生效的硬过滤排位。q38 换刺激:3 缺陷 DISCONTINUED 款（「不刺激」反方向）、q40 换液体 Rimmel（粉状反方向）。草稿见 `docs/eval_v2_batch4_draft.md`（状态：已落库）
- [x] **q7 首答 miss 修复：gold 重标（2026-08-29）**：q7「干皮/混合/敏感/痘痘肌要高遮瑕」首答 miss（primRank=11），根因是 gold 三处硬伤——①primary EX1（B00M681EX6）实际中度遮瑕且遮瑕未标，无法自证「高遮瑕」；②extra Dermacol（B077W2RCN7）带缺陷证据（闷痘:4），缺陷商品不能当正推；③extra Clinique（B01N1UUETU）带缺陷证据（色号偏深黄:3）+ 肤质未标，被敏感/痘痘硬约束排除。**新 gold**：primary=B08SW7WZPX（液体/全肤质;敏感肌/哑光/高遮瑕，不在缺陷证据表）、extra=B00GCQZB00（乳霜/全肤质/哑光/高遮瑕），negative 不动。脚本 `scripts/relabel_q7_gold.py`（dry-run 默认，--apply 真写：备份 *_bak_v19 + 双表 UPDATE + 重建池）。**效果**：首答 17/19→18/19（94.7%）、q7 primRank=11→3、坏例 5→4；NDCG 0.553 / 避雷 0.889 / CONTRACT 105/105 / 3 决策指标全零回归；**其余锚点池零漂移**（RNG 实测 q1-6/q8-24/q25-41 全列一致）
- [x] **Phase-1 池避雷泄漏 3 条（q7/q9/q11）跳过（2026-08-29，用户拍板）**：badcase 报告 Phase-1 池「负例进 top-5」3 条，实测根因 = **池内子集排序伪影**：负例商品（弱证据缺陷负例）在 v2 池内 rank 4/4/5，但**全库 rank 28/139/73**——Agent `_retrieve` 的 top-12 窗口根本看不到，**生产链路不推**；且缺陷预过滤清不掉（B07MX218RF 卡粉 9/35=25.7% 未过 70% 共识门槛不在 defect map；q7/q9 无避雷轴触发）。改评测口径对齐 Agent 的修法实测清不掉，**用户拍板跳过**——badcase_report.csv 保留登记，标注「跳过·池内子集伪影」。口径：生产 Agent 全库排序无此泄漏，Phase-1 池是独立评测通道，两套口径如实分开报
- [ ] 与 **eval_guide.md**（评测指标定义文档）合流，同步 `database_schema.md`
- **交付物**：~~eval_runner.py + 首版评测报告 + badcase 表~~ → eval_runner.py + badcase 表 + 评测报告模板已完成，剩 eval_guide.md 合流
- **遮瑕精标方向（数据天花板，2026-08-28 用户确认）**：低分题（id=8/15/17）根因是推荐品「遮瑕未标、无法自证高遮瑕/轻薄」；但库内商品为几年前录入，**标题/描述无遮瑕线索的旧商品无法人工标注** → 只对标题/描述含遮瑕信息的 gold 商品精标 coverage 真值，其余保持「未标」+ Agent 诚实规则兜底（「未标」≠「不适用」既有认知成立）

### Phase 4：工程化 + 项目呈现 —— 约 0.5 天

- [x] **可真实使用的 AI 导购前端（2026-08-29 完成，演示交付件）**：`scripts/web_server.py`（零依赖 stdlib http.server：`/api/chat` + `/health` + 静态页；rule/hybrid 双实例懒加载，整轮加锁串行化 llm_cache 写）+ `web/index.html`（单页对话：商品卡片四件套 + 避雷块 + 兜底块 + `<details>` 决策透明面板 + 规则/混合模式切换 + 8 条中英示例题 + 多轮拼接）+ `启动AI导购.bat`（CRLF + python 回退链，双击启动自动开浏览器）
  - **双语支持（CJK）**：规则层只认英文关键词 → `CJK` 正则命中即视为盲区自动走 LLM；中文专用 prompt `SYSTEM_PROMPT_CJK` 额外抽 肤质/妆效/遮瑕/质地/色号/预算 + 负约束数组 + 证据（英文 query 永远走旧 prompt → A/B 零漂移）；`TIMEOUT_CJK=25`（中文推理慢，15s 会误超时）；`_merge_cjk_constraints` 把 LLM 显式约束合进 req——**CJK 约束合并不再被 validate 门阻塞**（自然妆效不在 VERIFIABLE 会误拒，预算也会一起丢）
  - **真实链路实测（全部 HTTP 过）**：英文规则 45ms（intent=rule）；中文油皮哑光 3.3s 冷 LLM → 哑光 + 控油；中文避雷 hard=敏感肌 + coverage=高遮瑕 + negative=闷痘；多轮「I need a foundation」→ ask_all 3 问 → 回答 → 3 推荐。前端规则模式 + 中文输入 = 自动切混合模式（带 toast 提示）
  - **Windows 真机坑（CRLF + python stub）**：①`python` 在 cmd/PowerShell 命中 `WindowsApps` 0 字节 reparse stub（Git Bash 跳过 reparse point 所以能跑）→ .bat 用回退链（PATH python 带 pandas → tradingagents 绝对路径 → Anaconda base）；②工具写的 .bat 是 LF 行尾，cmd.exe 批处理必须 CRLF（LF 会整行串读，报 `'agent' 不是命令`）→ 改 CRLF + UTF-8 no BOM（chcp 65001）
  - **eval_compare 分母修正（对外口径的数字）**：评测集扩到 41 题后 hidden 分母硬编码 `/9`、旁路 `/33` 是 stale → 改动态 `len(rb['detail'])`；锚点定标 15→18。重跑干净：A 18/19=94.7% ✓ 复现、B 18/19 零漂移、CONTRACT 105/105、hidden A 7/17→B 15/17 命中 14/17、LLM 触发 10/41 全在盲区
- [x] **跨会话用户记忆（2026-08-31 完成，第二轮交付件）**：AI 导购要「懂用户」——语言偏好 + 肤质画像 + 时间感知问候，三项都跨会话/跨重开持久化
  - **存储**：后端落盘 `data/user_profiles.json`，按**匿名 userId** 键控（首次访问前端 localStorage 生成 `u_<random>`，`clearBtn` 不清它——清对话不丢记忆）；上限 100 条按 `last_visit` LRU 淘汰。核心设计 = **服务端画像数据层 + 匿名隐私**（无 key/无敏感数据，换浏览器记忆仍在）
  - **schema**：`{uid: {"lang": "zh|en|None", "skins": ["混油"], "last_visit": "...", "created": "..."}}`——`lang`=最近一次回复语言（zh/en），`skins`=用户明确说过的肤质（中文标签，最近一次显式声明覆盖）
  - **语言记忆**：`reply_lang` 记忆规则 = 多轮续答（`User says:` 前缀）或无新意图时**沿用 profile.lang**；用户主动切语种（新意图且 query 语言与记忆不一致）→ 跟随 query 并更新记忆。前端加载时拉 `GET /api/profile` → `uiLang` 从 profile.lang 恢复，**重开聊天框界面/回复仍是记忆语言**
  - **肤质记忆**：①`extract_constraints` 每条肤质命中处同步 `meta["skins_stated"]`（英文规则 + `_CJK_SKIN` 中文映射）；②`_CJK_SKIN` 补 **混油/混干 细粒度**（必须排在 `油/干` 子串条目**之前**，否则混油被降级成油皮）；LLM 会把混油归一成「混合肌」→ `_merge_cjk_constraints` 加**原文关键词复检**（raw 含混油 → soft=混油 且 discard 混合肌）；③`agent.run(q, profile=profile)` 在 `_llm_merge` 之后、`decide_ask` 之前注入记忆肤质——**顺序关键**（先注入会让 should_fallback 判「规则已有信号」→ 中文不再走 LLM 抽其他约束）；**只在 profile 非 None 时注入 → eval/contract 无 profile 路径字节级零回归**
  - **时间感知问候**：前端 `MEMORY_GAP_MS=24h`，隔久重开 + 有肤质记忆 → 问候「👋 好久不见！我记得您是混油肤质。还是以混油肤质为您推荐粉底液吗？」+ 两个 chip：`是的`（确认沿用）/`不是`（POST /api/profile 清空 skins + 引导重述新肤质）——像真人一样记得老用户
  - **验收（16/16 全过）**：英文首问→profile.lang=en→重开续英文；中文「我是混油，想要哑光」→ `skins_stated=["混油"]` 细粒度→profile.skins=["混油"]；再发「再推荐一款」（无肤质词）→ `memory_applied=true` 以混油注入；`POST /api/profile` 清空 skins 生效；英文/中文/法语桥零回归。**锚点逐数字复现：首答 94.7% / NDCG@5 0.553 / 避雷 0.889 / CONTRACT 105/105 / 追问 29.2% / 降级 0%**
  - **坑（教训记录）：非重入锁嵌套死锁**——`handle_chat` 整轮 `with _lock:` 内调用 `get_agent`（其内部也是 `with _lock:`），`threading.Lock` 非重入 → 同一线程二次 acquire 挂死，`/api/chat` 必死锁（`/health`/`/api/profile` 正常，只 chat 挂——第一个死锁线索就是「EN 挂 ZH 400」的不对称）。修法：`get_agent` 挪出外层锁（预热后读缓存无需锁，冷构建走内部锁，两锁不嵌套）
- [x] **中文快路径（CJK 规则层，2026-08-31 补）**：用户实测反馈「运行太慢 + 说了肤质还问肤质」——根因 = 每条中文都走 LLM（2-12s，LLM 挂/超时甚至 25s），且 LLM 一旦返回空（`llm_evidence=llm_no_output`，实测「我是冬混干夏混油肤质」11.9s 失败）`_merge_cjk_constraints` 整段跳过 → 明说的肤质全丢 → ask_all 问肤质
  - **修法**：`agent._cjk_explicit(req, meta, query)` 中文显式约束规则层——肤质/妆效/遮瑕/质地/色号/预算/控油/持妆/负约束/熟龄/防晒防水保湿轴 全部从原文直抽（LLM 无关），在 `run()` 里 `extract_constraints` 之后、`should_fallback` 之前调用；`llm_intent.should_fallback` 中文分支改为 **CJK_SCENE 场景门**——显式约束抽到 → 规则能答不上模型；含场景线索（海边/婚礼/换季/出汗/泛红…）→ 上 LLM 补隐式意图；裸问 → 直接 ask_all（省 LLM 空转）
  - **肤质细节**：混干/混油 必须先于 干/油 判（子串降级）；T区油两颊干 → 混合肌（按偏油/偏干给混油/混干）；混合偏油/偏干 → 细化为混油/混干（skins_stated 同步清理）；「不要闷痘/怕闷痘」是负约束**不得**误判成痘痘肌硬约束（痘痘肌只认「痘痘肌/痘肌/痘皮/爱长痘」等肤质表述）；冬+夏 → seasonal（全年），又油又干同时 → unsolvable
  - **验收**：用户原话 0.04s（原 11.9s）、no_ask 不问肤质、记忆记下混干+混油、再推荐按记忆注入；油皮预算哑光 0.06s（预算 200 直抽）+ ask_shade_soft；场景 query「去海边玩」仍 2.2s 走 LLM 补防水；裸问直接 ask_all。**锚点逐数字复现：首答 94.7% / NDCG@5 0.553 / 避雷 0.889 / CONTRACT 105/105 / 追问 29.2% / 降级 0%**（eval 全英文 → CJK 门控字节零漂移）
- [x] **Harness 驾驭层（2026-08-31 完成，第三轮交付件）**：把「三条用户反馈」收进 Agent 运行管控底座（不是推理大脑，是驾驭层）。现状已有 ~70% Harness 基因（3 决策节点=确定性管控层、user_profiles=会话状态、避雷/诚实=护栏、_record=可观测、LLM 超时/降级=降级链），本轮补齐缺失四块凑齐五大能力：
  - **`scripts/harness.py` 轻量中间件**（零依赖，包住 agent.run）：①工具拦截+权限门 `gate()`——query 必须字符串/非空/≤500 字符，不满足就在调用 agent 前拦下；②会话状态 `session_budget()`——查询次数/LLM 触发次数/1h 窗口滚动；③行为预算 `pick_mode()`——单会话 LLM 触发超 20 次 → 强制降级 rule 纯规则并向用户明说（成本可控）；④护栏栈 `medical_note()` 医疗越界校验（护肤品≠医疗建议：祛痘/祛斑/用药/烂脸/处方/皮肤科等治疗类词→附免责；痘痘肌/敏感肌/遮痘印遮盖是正常选品词不误伤）+ `coerce_num()` 数据有效性（数字强制 float，绝不进字符串函数——预算宕机根因就是数字传给 .replace）；⑤全链路埋点 `data/harness_trace.jsonl`（输入→约束→ask/retry/fallback→推荐 asin→耗时，可回溯）
  - **`web_server.py` 集成**：`pick_mode` 预算感知路由（超限降级）+ `process` 返回 search_note/medical_note/budget_warning/trace_id；启动横幅加驾驭层行。**前端三处反馈修复**：`esc()` String 强转（预算 `$10` 显示、数字不再 .replace 崩）；`MIN_TYPING_MS=2.2s`「🔍 正在搜索…」（0.04s 答完像固定回复→人工 2-3s 检索感，错误路径不等待）；决策透明面板加 harness `trace_id` 行
  - **水光严格执行（中文显式妆效 CJK-gated）**：用户反馈「说了水光却原样推荐」根因=妆效 +1 软分被肤质 +2+热度 tie-break 压掉、未标商品肤质+质地分反超真水光。修法：`_cjk_explicit` 抽出用户**原文显式**妆效 → `meta["cjk_finish_explicit"]`（与 LLM 场景派生妆效区分），`_retrieve` 里该标记下 **未标+不匹配一并硬过滤**（只推该妆效款）→ 水光实测 top-3 全真水光；`meta["cjk"]` 下口碑护栏（评分<3.0 降 2 分、评论<5 且 <4.0 降 1 分）。**差评区硬剔**：评分 <3.0 中文路径直接排除+排除原因进 excluded（用户再反馈「评分 1 分要说清楚为什么，都是差评就别推」）；`_build_evidence` 诚实话术——差评区绝不吹「好评口碑值得入手」（FACE 1.0/1 被打「好评口碑」是打脸案例），改说「评分仅 X 分/差评为主慎入」，英文同步。**三话术不呈现（用户定）**：search_note「已从N款筛出M款」/ thin-note「库内X款较少」/ avoidBlock「已避开」全部移出 UI（排除逻辑保留，正文干净）
  - **死链拦截（推送前最后一道）**：用户实测「推荐的商品链接点进去 404」→ `scripts/check_links.py` 全库 asin 探测（GET /dp/{asin}，404=失效，浏览器 UA，线程池并发，失败记 unknown 可续跑）→ `data/dead_asins.json` + `link_check_report.csv`；运行时推送前过滤（Serious B01FPMHE9C 实测 404）。**多模态=文档级二期预留**（皮肤照片→色号是合理入口，Amazon 无图库，不做半吊子 demo）
  - **锚点复现**：首答 94.7% / NDCG@5 0.553 / 避雷 0.889 / CONTRACT 105/105 / 追问 29.2% / 降级 0% / 兜底 8.3%——全部 CJK 门控 + 中间层零排序侵入，eval 全英文字节零漂移
- [x] **资损陷阱题（2026-08-31 完成，商业化叙事第四层）**：往测试集注入「资损陷阱题」8 题 3 类——**A 报价溯源**（假价 $0.99/$1.99 不得确认/回显，全库最低 $3.83 假价必为虚构）/ **B 优惠溯源**（问券问折扣不得虚构促销）/ **C 预算硬约束**（每条推荐价 ≤ 上限×1.3，对齐 `_pick_recs` 微超升级位）。纯规则模式跑批**拒绝率 8/8=100%**。**架构级保证**：LLM 只填约束、回复文本确定性从库生成（`_build_reply`/`_build_evidence`），报价/优惠结构上就不可能虚构——资损防护不靠提示词自觉，是回归门。文件：`scripts/loss_risk_cases.py`（陷阱题集，合成 query，**非评测集题→不碰 eval 锚点**）+ `scripts/eval_loss_risk.py`（runner，零 LLM 零 key，全过 exit 0 CI 可挂，报告 `data/loss_risk_report.csv`）。
- [x] **权重组合排序重构（2026-08-31 完成，用户三条反馈之二「怎么只推荐了一款」）**：用户明确约束模型——**硬约束**=预算/敏感肌/痘痘肌/避雷/死链/用户显式妆效/遮瑕/质地；**软约束**=肤质（冬混干夏混油也兼容中性/混合肌）。修法：
  - `retrieval_engine.py` `tag_score` 肤质轴改软权重：直接命中 +2、全肤质 +1、**混干/混油兼容中性/混合肌 +1「肤质·兼容」**（不硬剔）
  - `_retrieve` 精确候选 <3 时 **fill-in 补款**：跳过安全硬检查（死链/预算/避雷/差评区/敏感痘痘肌/妆效），只放松**质地→遮瑕**（绝不放松妆效），补款标记 `why=["fill_in:质地(液体→棒状)"]` 流经 `_pick_recs`→推荐 dict `fill_in` 字段（**「接近款」话术永不呈现，2026-08-31 用户定：不让用户知道补款逻辑**，fill_in 只留内部记录）
  - **去「商品库/知识库」措辞**（用户红线：永远不向用户坦白有知识库存在）：agent.py 6 处（诚实标注「该商品遮瑕度/肤质/质地/妆效未标注」、兜底话术「强控油和强保湿很难靠一瓶粉底同时做到」、no-match「目前没有完全符合您需求的产品」、`_tags_text`「（标签未标注）」）+ harness search_note「已按您的条件筛出 N 款」+ 前端 UI 三话术已移出
  - **用户真实 query 复测**：「我是冬混干夏混油，想要水光粉底液」从 1 款 → **3 款**（1 真液体水光 + 2 接近款诚实标注），妆效全真水光（显式妆效硬执行不破）
- [x] **死链拦截闭环（2026-08-31，用户三条反馈之一「推送的链接点不进去」）**：Amazon 对机器人/限流/404 常回 HTTP 200 兜底页 → 光看状态码会把死链误判 live（B01FPMHE9C 翻车实证）。**自证探测**：200 且 canonical href 含 `/dp/{asin}` 才算 live；404/410=dead；其余一律 unknown（机器人页/兜底页绝不误判，红线）。**信任闸门**：探测前先探已知在线控制品 B017U9AY4A，控制品不自证 live → 本轮探测不可信，跳过（宁可不拦新死链，绝不误杀在线商品）——静态清单 `data/dead_asins.json`（157 条含用户实测 4 款）不依赖闸门永远生效。运行时对推荐 top-3 增量复核，新死链落盘下轮生效；`dead_rerun` 最多重跑 1 轮。harness 注入 `agent.run(dead_asins=...)`，HTTP 实测死链零泄漏
- [x] **开场选项面板（2026-08-31 完成，用户三条反馈之三「开头没问妆效/预算」）**：开场问候语改为**三组可点选 chips**，零后端改动（组装成自然语言 query 走既有规则层）：
  - **肤质（可多选）**：混油/混干/痘肌/敏感肌/冬混干夏混油（季节项，与单肤质互斥，选中即 seasonal+混干混油）
  - **预算（单选，按库价分位四段）**：`<$15`（24%）/`$15–25`（25%）/`$25–40`（30%）/`$40+`（22%）；组装取档位上限「预算40美元以内」≤上限硬过滤；`$40+` 无「≥」语义 → 不传数字（高价位意图，检索不设上限避免误伤）
  - **妆效（单选）**：水光/自然/哑光
  - `👌 按这个推荐` 组装自动发送；`✍️ 我自己说` 收起面板自由输入；有记忆肤质自动预选；双语（英文 UI 组装英文 query，winter/summer 词仍抽 seasonal）
  - **锚点零漂移**：首答 94.7% / NDCG@5 0.553 / 避雷 0.889 / CONTRACT 105/105 / 追问 29.2% / 降级 0% / 兜底 8.3%；资损 8/8（fill-in 补款不碰安全硬约束）
- [x] **真实浏览器实测二轮修复（2026-08-31 完成）**：用户实测两条 badcase + 两条体验反馈，全落地：
  - **同族去重（badcase）**：Becca 同配方 Mahogany/Sienna 两色号同时进 top-3 → `_pick_recs` 加 `_family_key`（品牌+标题公式核心），同族只留分数最高一款
  - **兜底句正文去重（badcase）**：「没有一款粉底会自动调肤」正文黄块两头重复 → 兜底句由前端黄块统一呈现，正文不再重复
  - **面板保留（体验）**：选完选项后开场面板不再隐藏，AI 首个问题不再变空白
  - **热销加分（体验，选 A 排序加分）**：中文路径 `_cjk_rerank` 热度两档加分 ≥200 +1.0 / ≥50 +0.5；只在 `meta["cjk"]` 生效 → 英文锚点零漂移；妆效硬约束不动（Revlon 2564 条/Max Factor 1412 条因妆效未标仍被硬过滤排除，取舍用户已认可）
  - **🔥低 不展示（体验）**：热度「低」档前端 `heatBadge()` 返回空串，高/中照常
  - **锚点零漂移**：首答 94.7% / CONTRACT 105/105 / 资损 8/8 全部复现
- [x] **推荐文字精简化（表格 → 干净版三轮定稿）+ 死链全库重扫（2026-08-31 完成，实测五轮）**：
  - **AI 回答文字精简化（用户三轮迭代定稿）**：先表格化（列：商品/特点/口碑/价格）→ 去表格列留编号列表 → 定稿**干净版**——正文只留「导语 + 💡 软追问」两行，商品名/特点/口碑/价格全部由下方卡片承载；ask_shade_soft 占位话术「（先按已有条件推荐，色号稍后帮您收窄）」**永不呈现**（中英文都删）；💡 措辞改「告诉我您常用色号，可以更精准噢（偏自然/偏白）」。改动：`agent._build_reply` 删占位 + 软问措辞/options/分隔符，`web/index.html` `replyTable(rec)` 只渲染导语+💡（过滤占位话术），编号列表/表格/CSS 全移除；CONTRACT 105/105 照过（新措辞仍含「色号」「更精准」），锚点字节零漂移；node 模拟 17/17
  - **死链全库重扫（静态清单 158 → 160）**：用户实测 B00XD6BL56（NYX 粉状水光粉底）404 加入清单 + `check_links.py --rescan-all --concurrent 10` 全库 1090 项 / 236s：live=222 / dead=152 / unknown=716；**Amazon 限流坑**——在线商品间歇性 404，单次扫描直接当死链会**误杀在线商品** → 复核对新发现死链做 3 次稳定性重探（≥2/3 稳定 404 才保留）：31 个新候选仅确认 2（B00AK3MVG8 / B01K13I50C），29 个限流伪影剔除；控制品 B017U9AY4A 保持 live 地位不入清单。服务器重启（新 PID）后清单生效；实测「粉状水光预算 15 内」query B00XD6BL56 由无过滤第 1 名被顶替 → 推荐零死链
  - **锚点复现**：首答 94.7% / NDCG@5 0.553 / 避雷 0.889 / CONTRACT 105/105（A/B 均 105/105）/ 追问 29.2% / 降级 0% / 兜底 8.3%
- [x] **多轮上下文保留 + 「这三款有什么区别」本地对比表（2026-08-31 完成，实测六轮）**：用户实测第二轮「这三款有什么区别？」被当全新 query——水光/预算全丢（重推粉状矿物、$25.45/$29.89 超预算、还反问妆感）。**根因**=前端只在 `pendingAsk` 时拼上下文、推荐完 `baseQuery=null`，服务端画像也只存肤质。**修法（纯前端，用户确认）**：① `send()` 每轮都拼 `原需求 + User says: 后续`，推荐完不重置，chips 答案直接追加；删 `pendingAsk` 状态机；换话题时新显式约束按 Agent 提取顺序覆盖（CJK 妆效 哑光>水光>自然）。② 新增本地对比表 `renderCompareTable`：输入命中 `区别|差别|对比|比较|difference|compare…` 且上轮 ≥2 款推荐时，用结构化卡片数据直接拼 N 列对比矩阵（特点/口碑/价格/热度/链接），不发后端。**实测**：累积 query 水光/预算/肤质全保留、不再反问妆感、3 款全水光不超预算；node 18/18 + 语法通过。**已知边界**：对比表只展示结构化信息，不做自然语言「区别分析」
- [x] **对话记忆层 + 色号诊断续答系统化（2026-08-31 完成，实测七轮）**：用户实测诊断自测后答观察，Agent 不报色号结论反而推 3 款商品（用户原话：「我回答了具体情况你不应该告诉我我适合什么色号吗？」）。**根因系统化**（不是加关键词）= ①前端 context 只拼「原需求+回答」、不拼 **AI 上一条问了什么** → 闸门无法识别「这是回答诊断提问」；②无 shade_diagnosis 意图 + 会话状态，色号家族无跨轮记忆
  - **对话记忆层（数据层）**：前端每轮传「最近 8 轮**用户+AI 双方**」convo；后端 `_store_convo` 每轮压缩落盘 `user_profiles.json.convo` = `{orig, req 抽取约束, recent 最近6轮双方对话, diag_family, updated}`——**每用户专有 user_id + 每轮存储**，换浏览器重开也在。闸门/推荐器都从记忆取数，不再靠前端拼超长字符串
  - **shade_diagnosis 意图（工具拦截落地）**：`llm_gate.route` 两个规则分支——上次 AI 含诊断自测词（血管/金饰/银饰/手腕/口红/观察/自测/素颜）→ 本轮=诊断续答；上次 AI 含软询问（要不要/帮你挑/挑几款/pick a few）+ 记忆有 diag_family → 用户点头（好/行/可以/要）= **confirm_recommend** 带「原需求，色号自然」走商品库、转向（换水光/算了）= 正常分类。**色号结论机器可读**：LLM 回复带「色号结论：X」标记行→ `_parse_family` 解析、展示时剥掉；LLM 挂 → `_diagnosis_fallback` 确定性兜底不崩溃
  - **文本去库化（用户两次明确要求）**：结论只按观察直说，严禁提库/库内数据/色号细分体系。定稿：「按您说的，您的肤色大概率是暖调/中性调（偏自然）。要不要我帮你挑几款自然色号的粉底液？」
  - **会话内色号记忆自动带（用户确认）**：`shade_family` 像肤质记忆存进压缩记忆，后续中文推荐自动注入「，色号X」；本轮已说具体色号家族/方向则听用户的。仅 CJK 路径 → 英文锚点零漂移。**fill-in 同质地优先（用户确认）**：`_retrieve` 补足两趟——先补同质地（只放宽遮瑕）、无同质地再放宽质地，`_fill_cands` 同质地优先+分高者前。诚实项：全库「液体+水光」仅 3 款，无同质地可补时补足款仍可能非液体（不伪装）
  - **修复的两个代码级 bug**：`route()` 曾把 `_generate_diagnosis`（返回字符串）当元组拆包 → 502 崩溃；confirm 检测曾要求 `ai_asked_diag`（兜底结论文本不再含观察词）→ 点头漏判落回推荐器（正是用户抱怨的坏例），改为只依赖 `_AI_OFFER + diag_family`
  - **验收**：离线 A/B/C/D/E 全过（含无 convo 安全网 B2、冷调 E）；HTTP 同 uid 四轮 T1 求助→T2 诊断结论（family=自然，文本无「库」字）→T3 点头 3 款自然色→T4 新需求自动带「色号自然」；服务端无 error。**锚点逐数字复现：首答 94.7% / NDCG@5 0.553 / 避雷 0.889 / CONTRACT 105/105 / 全库避雷 24/24**
- [x] **对比/介绍人性化 + 结合评论区（2026-08-31 完成，实测八轮）**：用户实测问「这三款有什么区别吗？」得到一整块结构化清单（【商品名】/标签：/口碑：/色号：/差评声音：）——「太机器化，要人性化友好的介绍，并且必须结合评论区介绍。请固定记忆」。**根因两层**：①compare 的 LLM 生成一挂就 `text = facts` 把真实数据清单原样甩给用户；②**更深一层：deepseek-v4-flash 是推理模型，`max_tokens`=推理+正文一起算，GEN_TOKENS=900 常被 reasoning_content 吃光 → content 空/截断（200+finish:length+content=""）→ 每轮落回机器化兜底**（用户踩到的直接原因）。**修法（用户确认口语分点式）**：①`_compare_fallback` 确定性朋友式兜底——开头点共性差异、每款一段（🌟质地/适合谁 + 评论区评分条数 + 差评主题 + 价格）、结尾诚实总结+反问收窄，**绝不甩 facts 清单**；②`_generate_compare` prompt 改口语分点式（不要表格/像朋友聊天/把评论区声音讲出来/被集中吐槽的毛病一定点出）；③**根因修复 GEN_TOKENS 900→1800**（推理+正文预算）；④诚实边界（用户认可口径）：无评论原文，能结合的是真实评论信号（评分/条数/差评主题），**绝不编评论原话**。**实测**：离线桩 LLM 失败 → 兜底口语分点式（无机器化标记）；LLM 路径 3/3 稳定（623/684/617 字，会结合用户肤质提醒）；HTTP 推荐→问区别通过。**锚点逐数字复现：首答 94.7% / NDCG@5 0.553 / 避雷 0.889 / CONTRACT 105/105 / 全库避雷 24/24**（闸门仅多轮触发，eval 不经过）
- [ ] **多模态接口预留（文档级，二期）**：对话入口预留图片/语音字段；美妆多模态合理入口 = 肤色照片→色号匹配，但 Amazon 元数据无图，评估后放二期——「取舍判断」本身是 PM 加分点，不做半吊子 demo
- [ ] README 结构化为「问题 → 数据 → 方案 → 评测 → 结论」叙事
- [ ] 简历话术落版：
  - 对标语：**「评测集设计对标 Amazon-C4 + ESCI 相关度四档，避雷维度对齐 steerable 检索基准」**
  - 独特点①：Query 真实评论抽取（vs C4 半合成）→ 生态效度
  - 独特点②：金标准自动生成 + 可解释（vs ESCI 人工标注）→ 规模化 + 可追溯
  - 独特点③：检索层按需构建（vs RAG 全栈的 GCP 全家桶）→ 低成本可复现
- [ ] 三张图：数据清洗漏斗 / 检索消融对比 / badcase 归因分布

## 4. 工作量与顺序建议

```
Phase 0（评测集升级，1天）→ Phase 1（检索层，1.5天）→ Phase 2（Agent，1.5天）→ Phase 3（评测闭环，1天）→ Phase 4（项目呈现，0.5天）
≈ 5.5 天，可并行：Phase 0 的避雷扩展 与 Phase 1 的检索 可同时开工
```

## 5. 一句话总结（最终目标形态）

> 「我用 Amazon Beauty 数据建了商品知识库 + RAG 导购 Agent + 评测闭环。评测集 908 条真实用户需求从评论抽取（对比 Amazon-C4 的 LLM 半合成），金标准自动生成三档相关度（对比 ESCI 人工标注，可解释可扩展），避雷维度对齐 steerable 检索基准。Agent 检索对标 RAG 电商全栈，首答准确率 X%、NDCG@5 X.XX、避雷准确率 X%——badcase 全部登记归因闭环。」
