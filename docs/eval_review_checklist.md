# eval_review_50 · 人工复核工作清单（11 条 × 三列）

> **用途**：你（用户）对自动金标准做人工判断 → 算出「自动 vs 人工一致性率」。复核完把分数填到表格右边，或直接告诉我，我来录库算数。

> 生成时间：2026-08-27 ｜ 数据源：MySQL `beauty_agent.eval_review_50`（展示列已含 v12 避雷集 + v13 隐式标记）

## 打分规则（每列 1-6 分）

| 分 | 含义 | 算不算一致 |
|---|---|---|
| 5 | 很准确 | ✅ 一致 |
| 4 | 比较准确 | ✅ 一致 |
| 3 | 一般 | ❌ |
| 2 | 不太准确 | ❌ |
| 1 | 非常不准确 | ❌ |
| 6 | 无法判断 | 排除（不计入） |

> **约定**：对应商品列是「—」/空/None 的，该列**不打分留空**。**【隐式】**标记 = v13 隐藏意图增补商品，重点看「它该不该被加进金标准」。

---

## 第 1 题 [high | medium | medium]

**Query**：I’m over 60 and my skin is blotchy and I need something that will give full coverage and this product did.
**中文**：我60多岁了，皮肤有斑驳色块，需要一款高遮瑕的粉底，这款做到了。
**意图**：肤质;遮盖力

| 判断列 | 金标准商品（含标签） | 打分 |
|---|---|---|
| **PRIMARY**（应主推） | 　1. HydraGel Foundation (Light) [光泽;高遮瑕] | ____ |
| **EXTRAS**（相关但次要） | 　1. Clinique Redness Solutions Makeup Foundation SPF 15 with Pro [高遮瑕;白皙]<br>　2. Maybelline New York Dream Liquid Mousse Foundation, 1 fl. oz [全肤质;缎面;高遮瑕;深色]<br>　3. BAREPRO Performance Wear Powder Foundation-Cool Beige 10 [高遮瑕;自然;冷调] | ____ |
| **NEGATIVE**（该避雷） | 　1. Tarte Rainforest of the Sea Water Foundation Broad Spectrum  [光泽;轻遮瑕;白皙;橄榄]<br>　2. ION DE CUSHION Natural Beige 02 [自然;轻遮瑕;自然]<br>　3. IMVELY VELYVELY Aura Glow Cushion 17g with refill 17g (21 Li [光泽;轻遮瑕;白皙] | ____ |

## 第 2 题 [high | complex | hard]

**Query**：it's been hard to find a very pale foundation for a Goth without it being white clown makeup.This is perfect for that, and for people with very pale skin.
**中文**：很难找到一款够白、又不至于像小丑妆一样惨白的哥特风粉底。这款对白皙肤色的人很完美。
**意图**：色号

| 判断列 | 金标准商品（含标签） | 打分 |
|---|---|---|
| **PRIMARY**（应主推） | 　1. Full Coverage Cream Compact Foundation, Waterproof Long Wear [哑光;高遮瑕] | ____ |
| **EXTRAS** | — | 不打 |
| **NEGATIVE**（该避雷） | 　1. bareMinerals ORIGINAL Deluxe Foundation SPF 15, Golden Tan,  [全肤质;中度遮瑕;橄榄;深色]<br>　2. Demure Mineral Make Up, Dark Warm Mineral Foundation Makeup, [中性;混合肌;混干;混油;自然;深色]<br>　3. L.A.girl Pro BB Cream HD Beauty Balm (Dark) [敏感肌;深色] | ____ |

## 第 3 题 [high | complex | hard]

**Query**：I have Lupus, so I have been through my fair share of full coverage foundations, trying to find the right coverage, the right shade, and a product that can last all day without numerous applications throughout the course of my day...This Wanderlust Powder Foundation is it for me!
**中文**：我患有红斑狼疮，试过很多高遮瑕粉底，一直在找合适的遮瑕度、合适的色号，以及一整天不用反复补涂的产品……这款 Wanderlust 粉状粉底就是我要的！
**意图**：遮盖力;持妆;色号

| 判断列 | 金标准商品（含标签） | 打分 |
|---|---|---|
| **PRIMARY**（应主推） | 　1. Wanderlust Powder Foundation Light Medium (light medium with [哑光;高遮瑕] | ____ |
| **EXTRAS**（相关但次要） | 　1. BAREPRO Performance Wear Powder Foundation-Cool Beige 10 [高遮瑕;自然;冷调]<br>　2. Beauty Deals Mineral Liquid Powder Foundation Broad Spectrum [全肤质;敏感肌;高遮瑕;白皙]<br>　3. Femme Couture Get Flawless 8-in-1 Foundation TAN, 1.0 fl. oz [高遮瑕;深色] | ____ |
| **NEGATIVE**（该避雷） | 　1. [Missha] Glow Tension No. 23 (LINE FRIENDS Edition) - anti-a [光泽;轻遮瑕]<br>　2. UNNY CLUB Cover Glow Cushion 0.3 Oz / 11g Beige Color SPF50+ [自然;轻遮瑕;白皙;自然]<br>　3. Tarte Rainforest of the Sea Water Foundation Broad Spectrum  [光泽;轻遮瑕;白皙;橄榄] | ____ |

## 第 4 题 [high | short | easy]

**Query**：I have dry, dehydrated skin so I'm always looking for moisture.
**中文**：我是干性缺水皮肤，一直在找补水保湿的产品。
**意图**：肤质

| 判断列 | 金标准商品（含标签） | 打分 |
|---|---|---|
| **PRIMARY**（应主推） | 　1. CLIO Big Aurora Glow Cushion (003 LINEN) [光泽] | ____ |
| **EXTRAS**（相关但次要） | 　1. Youngblood Natural Loose Mineral Foundation, Neutral | Vegan [全肤质;缎面;自然;冷调]<br>　2. **【隐式】**Boots No7 Stay Perfect Foundation (Latte) [干皮;油皮] | ____ |
| **NEGATIVE** | — | 不打 |

## 第 5 题 [high | complex | hard]

**Query**：I have very dry, combination, sensitive skin that is prone to breakouts, so I need a full coverage foundation that isn't going to draw attention to my blemishes, fine lines/wrinkles, and dry patches.
**中文**：我是很干的混合性敏感肌，容易长痘，所以需要一款不会放大痘印、细纹和干皮的高遮瑕粉底。
**意图**：防刺激;肤质;遮盖力;保湿

| 判断列 | 金标准商品（含标签） | 打分 |
|---|---|---|
| **PRIMARY**（应主推） | 　1. EX1 Cosmetics Invisiwear Liquid Foundation F100 - Oil and Fr [全肤质;自然] | ____ |
| **EXTRAS**（相关但次要） | 　1. NYX Cosmetics Define & Refine Powder Foundation DRPF05 - San [全肤质;水光;自然]<br>　2. **【隐式】**Boots No7 Stay Perfect Foundation (Latte) [干皮;油皮] | ____ |
| **NEGATIVE**（该避雷） | 　1. Femme Couture Get Flawless Light 8 in 1 Foundation Light [轻遮瑕;白皙]<br>　2. Myconos Magic BB CC Moist Air Cushion Compact Korean Cover F [光泽]<br>　3. [Missha] Glow Tension No. 23 (LINE FRIENDS Edition) - anti-a [光泽;轻遮瑕] | ____ |

## 第 6 题 [high | short | easy]

**Query**：I prefer a light coverage so I don’t apply much
**中文**：我喜欢轻薄的遮盖力，所以不用涂太多。
**意图**：遮盖力

| 判断列 | 金标准商品（含标签） | 打分 |
|---|---|---|
| **PRIMARY**（应主推） | 　1. [KLAIRS] Mochi BB Cushion Pact, cushion foundation, foundati [无标签] | ____ |
| **EXTRAS**（相关但次要） | 　1. [SomeByMi] Killing Moisture Cushion Cover Foundation Korean  [痘痘肌;轻遮瑕;白皙;自然]<br>　2. VELY VELY Aura Glow Cushion 17g (21 Light) Single - Moisturi [光泽;轻遮瑕;白皙]<br>　3. IMVELY VELYVELY Aura Glow Cushion 17g with refill 17g (21 Li [光泽;轻遮瑕;白皙] | ____ |
| **NEGATIVE** | — | 不打 |

## 第 7 题 [mid | short | easy]

**Query**：Lightweight powder that eliminates shine.
**中文**：能消除油光的轻质粉饼。
**意图**：质地;控油

| 判断列 | 金标准商品（含标签） | 打分 |
|---|---|---|
| **PRIMARY**（应主推） | 　1. Demure Mineral Make Up, Dark Warm Mineral Foundation Makeup, [中性;混合肌;混干;混油] | ____ |
| **EXTRAS**（相关但次要） | 　1. Mirenesse Skin Clone Mineral Powder Foundation SPF15, 4-in-1 [全肤质;哑光]<br>　2. LA MER The Soft Fluid Long Wear Foundation SPF20 30 ml.# Nat [全肤质;自然;轻遮瑕;白皙;自然;冷调]<br>　3. Powder Foundation by Revlon, ColorStay Face Makeup, Longwear [自然;自然]<br>　4. **【隐式】**Boots No7 Stay Perfect Foundation (Latte) [干皮;油皮]<br>　5. **【隐式】**(3 Pack) RIMMEL LONDON Stay Matte Liquid Mousse Foundation - [哑光;自然] | ____ |
| **NEGATIVE**（该避雷） | 　1. Myconos Magic BB CC Moist Air Cushion Compact Korean Cover F [光泽]<br>　2. [Missha] Glow Tension No. 23 (LINE FRIENDS Edition) - anti-a [光泽;轻遮瑕]<br>　3. Wanderlust Powder Foundation Light Medium (light medium with [哑光;高遮瑕;白皙;自然] | ____ |

## 第 8 题 [high | medium | medium]

**Query**：I typically need a shade described as "very fair", which are harder to find in mineral foundations.
**中文**：我通常需要最浅的色号，但矿物粉底里很难找到这么浅的色号。
**意图**：色号

| 判断列 | 金标准商品（含标签） | 打分 |
|---|---|---|
| **PRIMARY**（应主推） | 　1. Sweat Cosmetics Foundation Powder Jar SPF 30 Water + Sweat R [敏感肌;自然] | ____ |
| **EXTRAS**（相关但次要） | 　1. Mirenesse Skin Clone Mineral Powder Foundation SPF15, 4-in-1 [全肤质;哑光]<br>　2. Mary Kay Mineral Powder Foundation 0.28 oz. - Ivory 2 [敏感肌;哑光;白皙]<br>　3. Estee Lauder Double Wear Stay-in-Place Powder Foundation 3N1 [中性;哑光;白皙;自然] | ____ |
| **NEGATIVE**（该避雷） | 　1. bareMinerals ORIGINAL Deluxe Foundation SPF 15, Golden Tan,  [全肤质;中度遮瑕;橄榄;深色]<br>　2. Demure Mineral Make Up, Dark Warm Mineral Foundation Makeup, [中性;混合肌;混干;混油;自然;深色]<br>　3. L.A.girl Pro BB Cream HD Beauty Balm (Dark) [敏感肌;深色] | ____ |

## 第 9 题 [high | medium | medium]

**Query**：I bought this because I'm going to Cancun in two weeks and I really want to find a good waterproof foundation.
**中文**：我买这款是因为两周后要去坎昆，我真的很想找一款好的防水粉底。
**意图**：持妆

| 判断列 | 金标准商品（含标签） | 打分 |
|---|---|---|
| **PRIMARY** | —（来源评分<4，无 primary） | 不打 |
| **EXTRAS**（相关但次要） | 　1. **【隐式】**bareMinerals ORIGINAL Deluxe Foundation SPF 15, Golden Tan,  [全肤质;中度遮瑕;橄榄;深色] | ____ |
| **NEGATIVE**（该避雷） | 　1. [Missha] Glow Tension No. 23 (LINE FRIENDS Edition) - anti-a [光泽;轻遮瑕]<br>　2. April Skin Magic Snow Cushion Pink -02. Green SPF50+/ PA+++  [白皙;冷调]<br>　3. Wanderlust Powder Foundation Light Medium (light medium with [哑光;高遮瑕;白皙;自然] | ____ |

## 第 10 题 [high | complex | hard]

**Query**：I bought this at the same time I bought Mizon BB snail repair blemish balm because I'm trying to better my skin and it has amazing reviews and seemed like it would actually be my color I'm extremely pale it's so hard to find things in my shade
**中文**：我买 Mizon 蜗牛修复 BB 霜时一起买了这款，因为我想改善皮肤，它评价很好、色号看起来也合适。我皮肤极白，很难找到适合我色号的产品。
**意图**：肤质;遮盖力;色号

| 判断列 | 金标准商品（含标签） | 打分 |
|---|---|---|
| **PRIMARY** | —（来源评分<4，无 primary） | 不打 |
| **EXTRAS** | — | 不打 |
| **NEGATIVE**（该避雷） | 　1. bareMinerals ORIGINAL Deluxe Foundation SPF 15, Golden Tan,  [全肤质;中度遮瑕;橄榄;深色]<br>　2. Demure Mineral Make Up, Dark Warm Mineral Foundation Makeup, [中性;混合肌;混干;混油;自然;深色]<br>　3. L.A.girl Pro BB Cream HD Beauty Balm (Dark) [敏感肌;深色] | ____ |

## 第 11 题 [high | complex | hard]

**Query**：Looking for even coverage that feels light and lasts all day, doesn't streak and settle into pores (even after pore filling primer?) Look elsewhere because this stuff is terrible.
**中文**：想找一款上妆均匀、质地轻薄、持妆一整天、不卡纹不卡毛孔的粉底（即使打了填毛孔的妆前乳也如此）？别买这款，太糟糕了。
**意图**：遮盖力;质地肤感;持妆

| 判断列 | 金标准商品（含标签） | 打分 |
|---|---|---|
| **PRIMARY** | —（来源评分<4，无 primary） | 不打 |
| **EXTRAS** | — | 不打 |
| **NEGATIVE**（该避雷） | 　1. Myconos Magic BB CC Moist Air Cushion Compact Korean Cover F [光泽]<br>　2. Wanderlust Powder Foundation Light Medium (light medium with [哑光;高遮瑕;白皙;自然]<br>　3. [KLAIRS] Mochi BB Cushion Pact, cushion foundation, foundati [无标签] | ____ |

---

## 一致性率怎么算（给你看的公式）

- 一致性率 = 打 **4/5 分**的判断数 ÷ 排除「6 无法判断」后的总判断数
- 分别算 primary / extras / negative 三列，也可算整体；每列还能按 complexity/intent 分组看
- 备注：可打分列统计 = PRIMARY 8 条（id=1-8）+ EXTRAS 9 条（id=1/3/4/5/6/7/8/9）+ NEGATIVE 9 条（id=1/2/3/5/7/8/9/10/11）