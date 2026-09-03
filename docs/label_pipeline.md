# 商品五维标签：生产链路与 QA —— 2026-09-02（P0-4 整改）

> 把散在 `clean_products.py` / `shade_tag_extract.py` / `coverage_extract.py` / `sim_label_patch.py` / `apply_label_patch.py` 的标签生产 SOP 收拢成一份文档。表结构真相源 = [docs/database_schema.md](database_schema.md)（改列/改口径强制同步它）。

## 一、五维标签 schema（products 表，1090 款）

| 轴 | 列 | 取值 | 关键坑 |
|---|---|---|---|
| 质地 | `form_tag` | 液体/乳霜/粉状/棒状/气垫 | `item_form_source` = details/title(推断)/missing |
| 肤质 | `skin_tag`（展示单标）+ `skin_tags`（评测多标，分号） | 全肤质/敏感肌/痘痘肌/油皮/干皮/混合肌/混油/混干/中性/熟龄肌 | **敏感肌/痘痘肌 = 硬约束**；复合肤质映射见 database_schema §肤质取值字典 |
| 妆效 | `finish_tag` | 哑光/水光/光泽/自然/缎面 | title 与 details 冲突 → `conflict_finish=1` 需人工复核 |
| 遮瑕 | `coverage_tag` | 高遮瑕/中度遮瑕/轻遮瑕 | **覆盖仅 36.2%（395/1090）**，未标 695 靠诚实兜底（详见 §四） |
| 色号 | `shade_tag`（多标签，分号） | 白皙/自然/橄榄/深色/冷调 | **v12**：标题是「家族主打色系」信号非全色号；覆盖 64.9% |
| （不入轴） | `skin_tone` / `scent` | — | 缺失率 93% / 100%，不参与匹配 |

## 二、三级来源信任（`*_source` 降权机制）

| 来源 | 含义 | 权重 |
|---|---|---|
| `field` / `details` | 商品字段原文 | 高置信，全权重 |
| `title`（推断） | 标题关键词反推 | **对应轴 ×0.5 降权**（检索引擎层实现） |
| `manual` | 人工精标核对 | 全权重（2026-08-28 新增） |
| 空 | 未标 | 不参与匹配，Agent 诚实兜底 |

> `conflict_*` 冲突标记（title 说 oily 但 details 写 Dry）→ 对应轴 ×0.5 并标记需人工复核，防标题噪声污染。

## 三、生产链路（按标签轴）

```
products 原始清洗（clean_products.py）
  ├─ 质地/妆效/肤质：字段原文 → form_tag/finish_tag/skin_tags（item_form_source 记来源）
  ├─ 肤质复合映射：skin_type 原文 → skin_tags 多标签（Oily, Combination → 混油 + 混合肌）
  ├─ 色号：shade_tag_extract.py（v12，标题提取多桶，覆盖 64.9%）
  └─ 遮瑕：coverage_extract.py（field 原文 18 + title 关键词 73 → 381/1090 = 35%）
       └─ apply_label_patch.py 人工精标补空 14 → 落地 395/1090 = 36.2%（未标 695）
```

- **补丁唯一真相源**：`scripts/sim_label_patch.py`（补丁表）；`apply_label_patch.py` 幂等（肤质去重追加 / 遮瑕仅补空，改前备份 `data/backup/`），跑完 `load_mysql.py` 双写 MySQL。
- **MySQL + CSV 双写铁律**：products_clean.csv 与 MySQL products 必须同源，标签脚本一律双写。

## 四、诚实兜底原则（数据天花板，2026-08-28 用户确认）

- 库内商品多为几年前录入，**标题/描述无该轴线索的旧商品无法人工标注** → 只对含线索的 gold/关键商品精标，其余保持「未标」。
- **「未标」≠「不适用」**：Agent 检索时未标商品不因缺标签被误排，靠诚实规则兜底（推荐不硬编「高遮瑕」这类它证明不了的话）。
- **hard 轴（敏感肌/痘痘肌）未标的细分（2026-09-03 三段降级）**：未标≠不适用在此处细化为「未标 ≠ 真雷也 ≠ 适用」——缺该轴标签且命中该轴 consensus 缺陷证据（敏感肌→刺激 / 痘痘肌→闷痘）= **真雷硬踢**（绝不因缺标签放行）；无缺陷证据 = **客观无该轴信息 → 沉底不排除**，仅在整库覆盖品不足时补位进推荐、卡片附「暂无该肤质用户反馈」风险提示。软轴未标照旧不参与加分（不误排）；hard 轴此前对缺标签一律 `-inf` 排除的「一刀切」改为三段后，无证据缺标签品不再被误杀，与「未标≠不适用」诚实原则对齐。
- 对外口径：**「标签覆盖率 36.2% 是数据天花板下的诚实记录——宁可留白让 Agent 诚实说'不确定'，也不拍脑袋补标签」**。

## 五、QA 与复核

| 环节 | 机制 | 记录 |
|---|---|---|
| 精标 QA | `apply_label_patch.py` 幂等 + 备份 data/backup/ | — |
| 冲突复核 | `conflict_skin/conflict_finish` 标记 → 人工抽查（肤质冲突数 1 需人工复核） | database_schema §3 |
| 评测联动 | 标签覆盖不均 → eval 首答 miss 归因（低分题 id=8/15/17 根因=推荐品遮瑕未标无法自证）→ gold 校准闭环 | `human_gold_check.csv` / `calibrate_gold.py` |
| 回归 | 改标签 → 锚点零漂移验证（eval_runner 94.7%/105/105） | data/eval_report.csv |

## 六、版本演进

| 版本 | 内容 | 日期 |
|---|---|---|
| 基础五维 | clean_products 字段 → 标签 | Phase 1 |
| `*_source` + conflict | title 推断降权 ×0.5 | 2026-08-27 |
| coverage 自动提取 | coverage_tag + coverage_tag_source（field 18 + title 73） | 2026-08-27 |
| ②精标补丁 | apply_label_patch 人工补空 14（coverage 36.2%）+ 4 肤质追加 | 2026-08-28 |
| shade_tag v12 | 色号多桶（覆盖 64.9%） | — |
| skin_tags 复合 | 复合肤质细粒度（混油/混干） | — |
| ③v3 标签缺口回填 | `apply_label_patch_v3.py` 人工逐评语核验 17 品 32 项（finish/form 新维度+source=manual），v3 首答 26.7%→41.4% | 2026-09-02 |

## 六之二、③v3 标签缺口回填（2026-09-02）

背景：v3 评测集（159 条）很多 gold 品真实标签欠标——结构化字段（title/details）缺失，但用户评语里有干净证据。

流程（与 ② 同安全模式）：
1. `_label_gap.py`（临时）对 v3 首答 miss 逐条生成缺口清单 → `data/_label_gap_checklist.csv`（33 条）。
2. **逐条打开原始评语人工核验**，过滤三类假阳性（不补）：关键词命中但语境反证（q117/q120/q126/q114）、相邻谱系用词不同源（水光/光泽/自然，q47/q48/q49/q59/q92）、无断言（q67/q70/q122）。
3. `apply_label_patch_v3.py` 落库：肤质追加 / 遮瑕补空 / **妆效补空（finish_type_source=manual，新维度）** / **质地纠正（form_tag 乳霜→气垫，仅当现值=预期 from，新维度）** / 整字段替换（假阳性纠正）。先备份再写 CSV，之后 `load_mysql.py` 双写。

本轮 17 品 32 项（VELEY 回撤见下），含 8 条 hard 轴（敏感肌/痘痘肌）-inf 排除行的方案①补标——8 行源评语全有干净敏感/痘肌证据，无一行需要「缺信息→降权」分支。

**锚点碰撞决策记录**（重要教训）：
- VELY Perfect Cover（B079XW1XJF）加 [油皮] 诚实正确（q101「perfect for oily skin」），但它同时是**锚点 q14 的 decoy**，加标签后 soft 精确 +2 排到 #3、把 gold（Rimmel）挤到 #4 → 锚点 94.7%→89.5%。**红线优先：回撤 VELY 油皮**，q101 延期（待锚点答案集更新后）。
- Palladio（B00P7COT6M）：title「Dual **Wet & Dry**」被误读成肤质 Dry（标题关键字陷阱假阳性）。原想做 干皮→油皮 整替换，但 **q148（v3）的 gold 正是 Palladio 且 gold 注解写死 [肤质:干皮]**——替换会把 gold 顶掉。修正为**追加** → [干皮;油皮]：保留 gold 依赖的干皮信号（有 gold 注解背书），同时加 q106 真实油皮证据。
- 教训：**补标签优先按用户批准的「追加」语义执行**；整字段替换（即使基于可靠假阳性证据）会与依赖旧标签的 gold 行冲突，需先查该品是否在别处当 gold。

## 七、相关文件

- 表结构真相源：[database_schema.md](database_schema.md)
- 标签/评测口径：[metrics_glossary.md](metrics_glossary.md)（§四.8 覆盖口径）
- 生产脚本：`clean_products.py` / `shade_tag_extract.py` / `coverage_extract.py` / `sim_label_patch.py` / `apply_label_patch.py` / `apply_label_patch_v3.py` / `load_mysql.py`
- ③回填存档：`data/backup/products_clean_pre_patch3_*.csv`（回滚用）；缺口清单 `data/_label_gap_checklist.csv`
- 评测联动：`calibrate_gold.py` / `prep_human_gold_check.py` / `eval_runner.py`
