# 评测集 v2 · 第二批（避雷型 3 条 + 色号型 3 条）

> 结构对齐第一批（docs/eval_v2_batch1.md）。
>
> - **避雷型** = 用户明确说「不要什么」→ **负约束过滤**（对齐 v12 避雷集「意图相反标签匹配 + 缺陷证据兜底」双机制；避雷商品必须能从可见标签一眼自证「为什么不该推它」）。
> - **色号型** = 色号方向匹配（`shade_tag` 桶自证：白皙/深色方向识别，pale/fair → 白皙）。
> - 素材来源：避雷型前 2 条升级自现有 11 条（id=5 / id=11），第 3 条为**模拟客服咨询场景**（真实评论里只有「我要什么」的 need 句，没有「不要刺激」这类负向咨询，已标注）；色号型 3 条全部升级自现有 11 条（id=2 / id=8 / id=10）。
>
> 每条推荐/避雷商品均已核对**真实商品标签** + 领域常识。
>
> **推荐证据四件套（2026-08-27 用户定）**：每行推荐证据 = **标签 + 价格 + 口碑 + 热度**，末尾带 **🔗 asin 链接占位**（真实场景拼 `https://www.amazon.com/dp/{asin}` 跳转商品页）。热度按评论量分档：**高 ≥200 / 中 50-199 / 低 <50**。**活动/促销价：库内无此字段 → 标「需实时 API」不虚构**（对齐「价格=快照，真实场景接实时价格 API」约定）。

---

## 一、避雷型（需求里带「不要什么」，测负约束过滤）

### A-1 敏感痘痘肌 · 高遮瑕，但不要突出瑕疵（升级自 id=5）

**Query**：I have very dry, combination, sensitive skin that is prone to breakouts, so I need a full coverage foundation that isn't going to draw attention to my blemishes, fine lines/wrinkles, and dry patches.
（我皮肤干+混合+敏感，还容易长痘，需要高遮瑕粉底，但**不能突出**我的瑕疵、细纹和干皮。）

**① 应识别意图**：
- 显式：肤质（干皮+混合+敏感）、遮盖力（高遮瑕）、避雷防刺激（"isn't going to draw attention"）
- 隐藏意图：干皮/混干肤质（dry + dehydrated 反推保湿方向）
- **复合硬约束**：敏感肌 + 痘痘肌 双硬约束（必须适用）+ 高遮瑕 + 保湿滋润

**② 硬约束**：
- 正：高遮瑕（full coverage）+ 敏感肌/痘痘肌适用 + 保湿不拔干
- 负：不能刺激、不能突出瑕疵/细纹/干皮（遮瑕相反 + 缺陷证据两路都要查）

**③ 该推荐 + 理由 + 证据**：

| 商品 | 为什么推 | 证据 |
|---|---|---|
| **EX1 Invisiwear 液体粉底** | 来源满意商品（primary）；标题直接标 **Oil and Fragrance Free**（无油无香精），敏感痘肌友好 | 标签：液体+全肤质+自然；3.4 分 / 9 条；热度：低；🔗 B00M681EX6 |
| **Dermacol Make-up Cover（Waterproof Hypoallergenic）** | 敏感肌标签 + 100% 遮盖力专为高遮瑕设计，正好对「blotchy」 | 标签：乳霜+缎面+敏感肌（库内 coverage_tag 未标，高遮瑕为产品常识）；4.3 分 / 171 条；热度：中；🔗 B077W2RCN7 |
| **Clinique Redness Solutions** | Redness（泛红修复）系列专治瑕疵/泛红——「不要突出瑕疵」的直接对口 | 标签：高遮瑕+液体；4.8 分 / 213 条；热度：高；🔗 B01N1UUETU |

**④ 该避雷**（负约束过滤，可自证）：

| 商品 | 为什么避 | 自证依据 |
|---|---|---|
| **Femme Couture Get Flawless Light** | 轻遮瑕——要高遮瑕却推轻遮瑕 = **意图相反**（v12 主机制） | 标签：轻遮瑕 |
| **MISSHA Glow Tension** | 轻遮瑕（意图相反）+ 光泽（光泽放大瑕疵纹理）+ **有闷痘/刺激证据**（双机制齐中） | 标签：轻遮瑕+光泽+粉状；缺陷证据：闷痘/刺激 |
| **Myconos Magic BB Cushion** | 有闷痘/刺激证据——敏感痘痘肌直接踩雷（缺陷证据兜底） | 缺陷证据：闷痘/刺激 |

> **判断要点**：这条的坑在「高遮瑕 ≠ 一定安全」。真正危险的不是遮瑕不够强的，而是**带闷痘/刺激证据的**——敏感痘痘肌必须把「缺陷证据」当硬负约束，不能只看遮瑕度。

---

### A-2 均匀遮盖轻薄持妆 · 不卡粉、不stripe（升级自 id=11）

**Query**：Looking for even coverage that feels light and lasts all day, doesn't streak and settle into pores.
（想要均匀遮盖、轻薄、持妆一整天，**不卡粉、不stripe、不填毛孔**的。）

**① 应识别意图**：
- 显式：遮盖力（均匀遮盖）、质地肤感（轻薄）、持妆（lasts all day）
- 隐藏意图：轻薄质地（feels light → 轻薄质地轴）
- **负约束轴**：卡粉 / stripe / 填毛孔 → 缺陷证据（卡粉/脱妆）兜底

**② 硬约束**：
- 正：轻薄 + 持妆 + 均匀遮盖
- 负：不卡粉、不stripe、不填毛孔

**③ 该推荐 + 理由 + 证据**：

| 商品 | 为什么推 | 证据 |
|---|---|---|
| **Rimmel Stay Matte 液体粉底（3 件装）** | 轻薄哑光液体、持妆控油口碑款，均匀上妆、粉感轻不卡粉 | 标签：液体+哑光；4.7 分 / 222 条；热度：高；🔗 B00J1OIDZ0 |
| **Mirenesse Skin Clone 矿物粉饼** | 矿物粉质地均匀轻薄、SPF15，持妆方向对口，高分热门 | 标签：粉状+哑光+全肤质；$37.97；4.9 分 / 29 条；热度：低；🔗 B00LTFW9XQ |

> 对比提示：**Estee Lauder Double Wear 粉饼**（$40.7，4.7 分/94 条）持妆最强，但质地更扎实偏厚，**跟「light」诉求有张力**——用户明确要轻薄时，它降为备选，需提示「要最强持妆才值得」。

**④ 该避雷**（卡粉/脱妆证据自证）：

| 商品 | 为什么避 | 自证依据 |
|---|---|---|
| **Myconos Magic BB Cushion** | 有卡粉+脱妆证据（缺陷证据兜底） | 缺陷证据：卡粉/脱妆 |
| **Wanderlust Powder Foundation** | 有卡粉+脱妆证据——这正是本 query 来源评论吐槽的商品（评分 1.0） | 缺陷证据：卡粉/脱妆 |
| **KLAIRS Mochi BB Cushion** | 有卡粉+脱妆证据 | 缺陷证据：卡粉/脱妆 |

> **判断要点**：这条是**纯缺陷证据避雷**（无「意图相反标签」可避，轻薄/持妆没有反面标签轴）。避雷准确率的工程实现 = 查 `product_defect_evidence` 表，命中卡粉/脱妆即排除。

---

### A-3 油皮敏感肌 · 哑光控油但绝不能刺激（模拟客服场景，评论区无此 need 句）

**Query**（模拟）：I have oily, sensitive skin. I need a matte foundation that controls oil, but it can't irritate me — anything with fragrance or alcohol makes my skin break out and turn red.
（我油性敏感肌，要哑光控油粉底，但**不能刺激**——含香精或酒精的就会闷痘泛红。）

**① 应识别意图**：
- 显式：敏感肌、避雷防刺激（"can't irritate"、"fragrance or alcohol makes me break out"）
- 隐藏意图：油皮/混油肤质 + 哑光妆效（控油需求反推，v13 规则 3）
- **张力约束**：油皮控油（常含酒精/刺激成分）× 敏感肌（避刺激）——Agent 要同时满足，不能只挑「控油强的」或只挑「温和的」

**② 硬约束**：
- 正：哑光 + 控油 + 敏感肌适用
- 负：不含刺激成分（香精/酒精）——**成分避雷**，只能推能自证温和的（敏感肌 / 全肤质 标签）

**③ 该推荐 + 理由 + 证据**（哑光 + 敏感肌/全肤质 **双标**，可自证温和）：

| 商品 | 为什么推 | 证据 |
|---|---|---|
| **puroBIO Second-Skin Sublime Drop** | 哑光 + 敏感肌双标签，认证有机温和，控油不刺激两头占 | 标签：哑光+敏感肌；$21.46；4.2 分 / 112 条；热度：中；🔗 B07HCHKFJJ |
| **Almay Clear Complexion** | 哑光 + 敏感肌;混合肌，**Hypoallergenic（低敏认证）**+ Clear Complexion 对痘痘肌友好 | 标签：哑光+敏感肌;混合肌+液体；$13.99；4.4 分 / 101 条；热度：中；🔗 B087J6PP9X |
| **Mary Kay Mineral Powder（Ivory）** | 哑光矿物粉 + 敏感肌，矿物粉温和路线 | 标签：粉状+哑光+敏感肌+白皙；4.7 分 / 66 条；热度：中；🔗 B07BD4QNJ5 |

**④ 该避雷**（哑光控油方向但**不能自证温和** + 刺激/闷痘证据）：

| 商品 | 为什么避 | 自证依据 |
|---|---|---|
| **Rimmel Stay Matte** | 哑光控油热门款，但**无敏感肌/全肤质标签**——不能自证温和，敏感肌有刺激风险（标签自证，v12 主机制） | 标签：液体+哑光（无肤质标签） |
| **Kat Von D Lock-It Powder** | 哑光但**有刺激证据**（缺陷证据兜底） | 缺陷证据：刺激 |
| **Etude House Any Cushion** | 有刺激证据 | 缺陷证据：刺激 |

> **判断要点**：这条测「**复合硬约束协调**」——控油款大多含酒精（刺激源），敏感肌要控油 = 必须在「控油强但刺激」和「温和但控油弱」之间找交集。只推荐**双标签同时命中**的（哑光 ✓ + 敏感肌/全肤质 ✓），把「哑光但无肤质标签」的统统排除。

---

## 二、色号型（色号方向匹配，`shade_tag` 桶自证）

### S-1 Goth 极白皙 · 要极浅色但不想要「死白假面」（升级自 id=2）

**Query**：it's been hard to find a very pale foundation for a Goth without it being white clown makeup. This is perfect for that, and for people with very pale skin.
（很难找到适合哥特风格的极浅色粉底，不会像白脸小丑妆。这款正好，也适合极白皮肤的人。）

**① 应识别意图**：
- 显式：色号=极白皙（very pale）、风格=Goth
- **色号方向识别**：pale → **白皙** 桶（shade_tag 含「白皙」）；Goth 风格 → 偏好**冷调**极浅（避免暖调假白，虽无显式冷调词，属领域常识）
- 硬约束：白皙（色号轴），非死白假面（要能自然融合的极浅）

**② 硬约束**：
- 正：白皙 / 极白 + 冷调方向（Goth 常识加分）
- 负：深色桶（色号偏深 = 反方向）

**③ 该推荐 + 理由 + 证据**（白皙桶，可自证）：

| 商品 | 为什么推 | 证据 |
|---|---|---|
| **Red&Black Full Coverage Cream Compact** | 来源满意商品（primary），白皙桶，防水持妆 | 标签：乳霜+哑光+高遮瑕+白皙；3.5 分 / 13 条；热度：低；🔗 B01MY4I0IA |
| **Tarte Amazonian Clay 12H** | **白皙+冷调**——Goth 极白冷调方向最对口 | 标签：乳霜+高遮瑕+全肤质+白皙;冷调；4.7 分 / 104 条；热度：中；🔗 B0155OPXT8 |
| **Revlon Colorstay** | 色号体系最全（含极浅色号 110 系），白皙桶，大众平价 | 标签：液体+白皙；$9.99；4.0 分 / 2564 条；热度：高；🔗 B014GJH4PE |

**④ 该避雷**（深色桶自证，色号方向完全相反）：

| 商品 | 为什么避 | 自证依据 |
|---|---|---|
| **bareMinerals ORIGINAL Deluxe（Golden Tan）** | 深色+橄榄调——要极白却推深色 = 反方向（v12 意图相反） | 标签：乳霜+全肤质+橄榄;深色 |
| **Demure Dark Warm Mineral** | 深色矿物粉，肤色方向完全相反 | 标签：粉状+自然;深色 |
| **L.A. Girl Pro BB Cream（Dark）** | 深色 BB——色号方向相反 | 标签：乳霜+敏感肌+深色 |

> **判断要点**：色号避雷 = **色号方向识别**。要 pale/fair → 避 `shade_tag=深色` 桶；别把「色号中性（只要合适的色号）」误判成避雷轴（对齐 v12：色号意图中性时色号轴不避）。

---

### S-2 白皙 · 矿物粉（色号 × 质地双轴，升级自 id=8）

**Query**：I typically need a shade described as "very fair", which are harder to find in mineral foundations.
（我通常需要「非常白皙」的色号，这在矿物粉底里比较难找。）

**① 应识别意图**：
- 显式：色号=白皙（very fair）、质地=矿物粉（mineral → 粉状）
- **色号方向识别**：fair → **白皙** 桶
- **复合约束**：色号轴 × 质地轴 双命中

**② 硬约束**：
- 正：白皙 + 矿物粉/粉状
- 负：深色桶——尤其「**深色矿物粉**」是双重反例（质地看着像，色号是反的，最容易推错）

**③ 该推荐 + 理由 + 证据**（白皙 + 粉状双标）：

| 商品 | 为什么推 | 证据 |
|---|---|---|
| **Sweat Cosmetics Foundation Powder** | 来源满意商品（primary），白皙矿物粉 + SPF30 防水抗汗 | 标签：粉状+敏感肌+白皙+自然；3.4 分 / 8 条；热度：低；🔗 B07S4298B7 |
| **Mary Kay Mineral Powder（Ivory）** | 白皙矿物粉，敏感肌温和 | 标签：粉状+敏感肌+哑光+白皙；4.7 分 / 66 条；热度：中；🔗 B07BD4QNJ5 |

**④ 该避雷**（深色矿物粉 = 双重反例）：

| 商品 | 为什么避 | 自证依据 |
|---|---|---|
| **Demure Dark Warm Mineral** | **深色矿物粉**——质地轴命中但色号轴反了，是最容易推错的反例 | 标签：粉状+自然;深色 |
| **bareMinerals ORIGINAL（Golden Tan）** | 深色+橄榄调矿物感粉底 | 标签：乳霜+全肤质+橄榄;深色 |
| **L.A. Girl Pro BB Cream（Dark）** | 深色 BB | 标签：乳霜+敏感肌+深色 |

> **判断要点**：这条的坑在「用户说矿物粉难找白皙」——Agent 极易被「矿物粉」勾走推荐一堆矿物粉，但**必须用 shade_tag 再筛一层**：只有「白皙」桶的矿物粉才合格，深色矿物粉是教科书级假正例。

---

### S-3 极白肤色 · 难找到合适的色号（升级自 id=10）

**Query**：I bought this at the same time I bought Mizon BB snail repair blemish balm because I'm trying to better my skin... I'm extremely pale it's so hard to find things in my shade.
（我买这个的同时买了 Mizon 蜗牛修复 BB，想改善皮肤……我肤色极白，很难找到适合我的色号。）

**① 应识别意图**：
- 显式：色号=极白（extremely pale）
- **色号方向识别**：pale → **白皙** 桶
- 注意：来源评论评分 2.0（非满意购买），**无 primary**——纯色号匹配题，Agent 要自己从库中找白皙桶推荐

**② 硬约束**：
- 正：白皙 / 极白（对白皙桶内商品可按色号细分：自然调 / 冷调，多给选择）
- 负：深色桶

**③ 该推荐 + 理由 + 证据**（白皙桶高分热门，含色号细分）：

| 商品 | 为什么推 | 证据 |
|---|---|---|
| **Rimmel Wake Me Up Ivory 100** | Ivory 100 是极浅色号，SPF15，白皙桶 | 标签：液体+光泽+中度遮瑕+白皙；$18.95；4.5 分 / 447 条；热度：高；🔗 B00864B3QC |
| **Boots No7 Beautifully Matte（Warm Ivory）** | 白皙 + 全肤质，色号覆盖广 | 标签：乳霜+哑光+全肤质+白皙；4.5 分 / 128 条；热度：中；🔗 B079M2PLKM |
| **IT Cosmetics Your Skin But Better** | 白皙+自然+冷调 多桶，肤色细分最全 | 标签：光泽+白皙;自然;冷调；4.1 分 / 128 条；热度：中；🔗 B08KFFW9H1 |

**④ 该避雷**（深色桶自证）：

| 商品 | 为什么避 | 自证依据 |
|---|---|---|
| **bareMinerals ORIGINAL（Golden Tan）** | 深色——极白需求完全反方向 | 标签：橄榄;深色 |
| **Demure Dark Warm Mineral** | 深色 | 标签：粉状+自然;深色 |
| **L.A. Girl Pro BB Cream（Dark）** | 深色 | 标签：乳霜+敏感肌+深色 |

> **判断要点**：三条色号题的**深色避雷一致**（bareMinerals Golden Tan / Demure Dark Warm / L.A. Girl Dark 全带 `深色` 桶）——这就是 v12 想要的效果：**避雷商品靠标签一眼自证**，可批量、可审计。

---

## 三、这批涉及的商品信息核对状态

| 商品 | 标签来源 | 核对状态 |
|---|---|---|
| EX1 Invisiwear | 库内标签 | ✅ 标题直标 Oil & Fragrance Free，与敏感痘肌对口 |
| Dermacol Make-up Cover（Waterproof） | 库内标签 | ✅ 库内核对（乳霜+缎面+敏感肌，4.3/171，B077W2RCN7）；coverage_tag 未标，高遮瑕为产品常识 |
| Clinique Redness Solutions | 库内标签 | ✅ Redness 泛红修复线，与 blotchy 高度对口（与第一批 D-3 交叉，场景不同） |
| Rimmel Stay Matte | 库内标签 | ✅ 经典哑光控油线 |
| Mirenesse Skin Clone | 库内标签 | ✅ 矿物粉高分线 |
| Estee Lauder Double Wear Powder | 库内标签 | ✅ 经典持妆线（质地偏厚，已作对比提示） |
| puroBIO Second-Skin | 库内标签 | ✅ 哑光+敏感肌双标，有机温和线 |
| Almay Clear Complexion | 库内标签 | ✅ Hypoallergenic 低敏线，对敏感痘肌友好 |
| Mary Kay Mineral Powder（Ivory） | 库内标签 | ✅ 矿物粉温和线 |
| Kat Von D Lock-It Powder | 库内标签 | ✅ 有刺激缺陷证据 |
| Tarte Amazonian Clay | 库内标签 | ✅ 高遮瑕经典款，白皙+冷调桶 |
| Revlon Colorstay | 库内标签 | ✅ 色号体系全（110 系含极浅） |
| Red&Black Full Coverage Cream | 库内标签 | ✅ primary，白皙桶 |
| Sweat Cosmetics Powder | 库内标签 | ✅ primary，白皙+矿物粉+SPF30 |
| Rimmel Wake Me Up Ivory 100 | 库内标签 | ✅ Ivory 100 极浅色号，光泽线 |
| Boots No7 Beautifully Matte | 库内标签 | ✅ 白皙+全肤质，色号覆盖广 |
| IT Cosmetics Your Skin But Better | 库内标签 | ✅ 三桶色号细分全 |
| Mary Kay Endless Creme to Powder | — | ❌ 库内无此商品记录，已从 S-2 推荐移除 |
| 避雷三件套（bareMinerals Golden Tan / Demure Dark Warm / L.A. Girl Dark） | 库内标签 | ✅ 全部带 `深色` 桶，可自证 |
| Myconos / Wanderlust / KLAIRS Mochi / Etude House | 缺陷证据表 | ✅ 卡粉/脱妆/刺激证据落盘，可审计 |

> 以上推荐商品均凭**产品线常识 + 库内标签**核对「方向站得住」；具体色号细分、价格、评论详情作为「精标清单」后续补（对齐第一批约定：只精标评测涉及的这几十个，不对全库 1090 个）。
