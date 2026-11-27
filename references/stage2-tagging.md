# Stage 2：三层打标、受控词表签字与独立上下文盲检规范

本阶段目标：把 Stage 1 产出的 `tagging_input_bundle.json` 与本地商店截图，经过「Pass 1 自由发散 → Pass 1.5 词表收敛与人工批复 → Pass 2 闭集定标 → `remote` 子代理盲态反向证伪重测 → `tag_qc.py` 三项门槛与低置信终裁」，冻结为可 100% 追溯的 `research/data/tagged_corpus.json`。

所有门槛数值以 [configs/tag_blacklist.json](../configs/tag_blacklist.json) 为唯一权威来源。

---

## 一、三层打标材料与读图深度

每款入围产品在 `tagging_input_bundle.json` 中结构化提供三层材料：

| 层级 | 材料内容 | 在打标中的权重与证据规则 |
|---|---|---|
| **第一层：商店官方事实** | 游戏名、副标题、完整英文商店描述、发行商、ST 生效首发日、商店发行日、地区属性（`cn_ios_only`）、ST Game IQ 参考字段 | 英文商店描述为**主题材主依据**；ST Game IQ 标 `[reference_only]` 仅作对照，盲态重测时物理剥离 |
| **第二层：视觉呈现（必看实图）** | `research/media/screenshots/<id>_01..05.<ext>`（已落盘最多 5 张商店截图）与买量封面 URL | 每款用 `Read` 按 1 到 5 顺序全扫已落盘截图，先剔除封面、剧情拼贴、人物立绘与功能弹窗，再按品类首选顺位锁定判定单元。商店截图核验 `presentation`，买量封面仅作辅证。 |
| **第三层：玩家原声与买量辅证** | ST 近 60–90 天过滤后评论（20–30 条，剔除 <10 词水评与纯崩溃反馈，兼顾高低星，保留评论 `id` 与日期）+ 境外高曝光前 3–5 条买量 `slogan` | 玩家评论为**真实剧情/玩法体感强依据**；买量 `slogan` 一律标 **`marketing_only: true`（仅辅证，绝不能单独支撑任何终稿 Tag）** |

**呈现判定单元**。全扫后按下表锁定最早出现的合格截图。同一张截图若同时包含两种区域，仍须分别标出棋盘和主场景的位置。

| `visual_unit` | 判定对象与顺位 |
|---|---|
| `board_and_scene_pair` | 合成与三消首选。棋盘和主场景都要有实机证据，可取同图内的两处区域或分取两张图。 |
| `gameplay_board` | 混变益智首选。取核心玩法棋盘或实际操作区。合成与三消缺主场景时降为此单元。 |
| `main_scene` | 模拟经营首选。取主要经营场景。合成与三消缺棋盘时降为此单元。 |
| `promo_or_portrait_only` | 补证后仍无实机核心图，只见宣传图、立绘、过场插画或卖点拼贴，或图源缺失。 |

- 合成与三消全扫仍缺一半时，在 `research/data/gap_register.json` 记 `L3`，置信上限为 `medium`。同一品类在词表阶段超过 30% 产品缺半边时，先联网补图。
- 全扫后没有实机核心图，先查另一端公开商店页或实机评测补证。仍为零实机图时记 `L2`，填 `visual_unit: "promo_or_portrait_only"` 和 `confidence: "low"`。正式跨产品呈现占比的分母排除这类产品。报占比前先列判定单元分布，跨产品比较限同一判定单元。
- 在 `tags_final.json` 与 `blind_retest_output.json` 的呈现标签对象，以及词表预标 `hit_evidence` 中，填写 `screenshot_index`（与来源对应的正整数或正整数数组）、`visual_unit`、`primary_visual_region`。偏离首选时，另填 `visual_unit_fallback_reason`，写明全扫情况、缺少的画面与实际采用的单元。`screenshots_reviewed` 列出已阅截图。完全无图时把缺图记入缺口，截图序号和来源按实际材料登记。

**条件联网补证**。查公开商店页、Fandom 或实机评测并记录来源 URL，限下列情形。
1. 英文商店描述缺失或不足 80 词。
2. 买量 `slogan` 出现强狗血、复仇或求生剧情，商店描述和评论均未提及剧情。
3. 全扫已落盘截图后仍无法判断画风或玩法融合，或触发上述缺图补证。

### `remote` 子代理读图核验与证据链条款

- **首次与换版探针**。首次在本工作区运行或宿主换版本时，用 `isolation: "remote"` 子代理通过 `Read` 核验 1 张当前工作区内的本地实图（要求复述图中可核对细节以确认读图通道可用），无需每次盲测前重复抽检。
- **日常盲测证据链**。日常盲测在同一独立上下文中全扫已落盘截图并按品类首选顺位锁定判定单元，通过逐款必填 `screenshot_index`、`visual_unit`、`primary_visual_region` 与对应四项可见特征（人物与物体比例、可见材质与光影、色彩饱和度、空间立体感）的 `evidence_quote` 守住读图证据链。
- **通道失效硬停**。若 `remote` 子代理读图通道失效或无法读取工作区实图，直接硬停并在 `research/data/gap_register.json` 登记 `L1` 缺口，不做手工双会话拼接。

---

## 二、Pass 1：自由词发散（每款 5–8 个）

- **分批节奏**。每次处理 **5–6 款同品类产品**。每款按 1 到 5 顺序全扫已落盘截图，先剔除封面、剧情拼贴、人物立绘与功能弹窗，再按品类首选顺位锁定判定单元。`full` 可按四品类分派 `isolation: "remote"` 子代理并行跑 Pass 1。
- **呈现候选**。按人物与物体比例、可见材质与光影、色彩饱和度、空间立体感四项描述实图，不从画面反推渲染技术。未实证核实技术时，呈现词名和 ID 禁用 2D/3D。呈现定义禁含题材对象词。
- **融合候选**。除合成棋盘与常规赚币开区域、交单自动装修、三选一换皮外，须有玩家能独立作出的可玩决策或独立副玩法循环。缺此类实证时留空。
- **禁泛词前置意识**：禁止输出品类/平台/空洞形容词（完整黑名单见 `configs/tag_blacklist.json`），必须写玩家与编辑能直接想象画面的具象词。
- **落盘文件**：合并写入 `research/data/pass1_free_tags.json`。

### 提示词模板 A：Pass 1 自由词发散（可直接复制给本批会话或 `remote` 子代理）

```text
你是资深休闲游戏内容编辑。下面为你提供 <N> 款游戏的材料包（位于 <报告目录>/research/data/tagging_input_bundle.json 中的指定产品列表）以及它们各自的本地商店截图路径。

执行步骤：
1. 对每款产品，阅读英文商店描述和过滤后玩家评论，用 Read 按 1 到 5 顺序全扫已落盘截图，先剔除封面、剧情拼贴、人物立绘与功能弹窗，再按品类首选顺位锁定判定单元。记录截图序号与观察区域，降级时记录原因。
2. 用玩家与编辑的具象语言，为每款产品提出 5–8 个自由候选 Tag，按三个主轴标注 axis，另记叙事强度和按强度联动的母题：
   - theme_primary（主题材：玩家反复经营、修复或处理的具体对象，如「烘焙店」「海滨餐厅」）
   - presentation（呈现形式：按人物与物体比例、可见材质与光影、色彩饱和度、空间立体感四项可见特征定标，须绑定商店截图。禁止反推渲染技术。未经实证核实，标签名和 ID 禁用 2D/3D。定义禁含题材对象词。）
   - fusion_secondary（除合成棋盘与常规赚币开区域、交单自动装修、三选一换皮外，须有独立可玩决策或副玩法循环。缺实证时留空。严禁在本槽使用剧情、抉择、故事、悬疑等叙事词。）
   - narrative_intensity（必填，按第四节判为重剧情、轻剧情或无剧情）
   - narrative_motif（叙事类型：可选，按配置中的受控枚举从家族八卦、豪门遗产、复仇翻身、失踪悬案、灾后求生中选一个，不占主槽，无剧情留空）
3. 严禁使用品类、平台或空泛形容词（如二合、三消、混变、模拟经营、休闲、益智、有趣、解压、现代题材、剧情向）。
4. 每个候选词必须附带：
   - evidence_type：store_desc | store_screenshot | st_review | web_wiki_review | ad_creative
   - evidence_source_id：对应材料包中的 source_id（截图写本地截图 source_id）
   - evidence_quote：≤40 词的原文摘录或截图画面客观描述
    - marketing_only：仅来自 ad_creative 时为 true，其余为 false
    - presentation 另填 screenshot_index、visual_unit、primary_visual_region、visual_unit_fallback_reason（命中首选时可为 null）。
5. 严格按如下 JSON 数组结构写入 <输出文件路径>，不输出多余解释：
[
  {
    "unified_app_id": "...",
    "app_name": "...",
    "subgenre_id": "...",
    "screenshots_reviewed": ["<已阅相对路径1>", "<已阅相对路径2>", "<其余已阅路径>"],
    "narrative_intensity": "轻剧情",
    "narrative_motif": null,
    "free_tags": [
      {
        "axis": "presentation",
        "label_zh": "≤6字中文短词",
        "label_en": "concise English label",
        "evidence_type": "store_desc|store_screenshot|st_review|web_wiki_review|ad_creative",
        "evidence_source_id": "...",
        "evidence_quote": "≤40 words quote or factual visual observation",
        "marketing_only": false,
        "screenshot_index": [1, 2],
        "visual_unit": "board_and_scene_pair",
        "primary_visual_region": "第 1 张下半部棋盘，第 2 张中央主场景",
        "visual_unit_fallback_reason": null
      }
    ]
  }
]
```

---

## 三、Pass 1.5：词表收敛与人工签字（全流程唯一人工停点）

1. **冻结名单与收敛规则**。
   - 按 Stage 1 的 `product_roster.json` 冻结入围名单，再按同一名单汇总 `pass1_free_tags.json` 和预标结果。送签前核对命中产品与冻结名单一致。
   - 合并同义与近义表达。颗粒相近但所指不同的词，要写清纳入与排除边界。
   - 剔除只出现 1 次且可归入上位具体词的过窄词。`emerging: true` 在 `pilot` 仅表示由滚动近一年内已核验新品引入的试点单例。它不能推及休闲游戏全盘。扩至 `full` 后累计命中至少 2 款时改为 `false`。命中数由脚本按 `hit_app_ids` 派生，与 `emerging` 分列。
   - 规模为 `pilot` **12–20 词**、`full` **25–40 词**。试点扩全量时保留合成试点已签字的 `tag_id`，补入其余三类词条。
   - 每条填写 `tag_id`（小写蛇形 `^[a-z][a-z0-9_]{1,39}$`）、`label_zh`（建议 ≤6 字，硬上限 8 字）、`label_en`、`axis`、`definition`、`include_examples`、`exclude_examples` 和 `hit_app_ids`（去重的统一 ID，主轴词条至少 1 个有效 ID）。主题材另填 `parent_domain`，取 `primary_domains` 的 ID。`full_only` 一级只在全量使用，一级只作分组。
   - 呈现先按可见特征区分绘制型与立体型，再按四项特征划分具体词条。同一呈现词条的命中数占呈现轴去重命中数超过 40% 时，逐产品附不可再分说明（`irreducibility_note`），写明已核对的判定单元、实图与排除的拆分方式。超过 60% 且无人工 `concentration_waiver` 时，脚本硬拦截。是否豁免在词表签字时一并确认。
   - 融合无合格词条时可归零。在 `fusion_zero_audit` 逐候选产品写实证与反证覆盖表，列出统一 ID、候选玩法、来源、可见操作或循环、反证及排除理由。
2. **脚本预检**。落盘 `research/data/tag_vocab_draft.json` 后运行 `validate-vocab`，取得脚本告警并逐条写入签字页。签字页落盘后带 `--draft-md` 再跑预检，处理错误后送签。
   ```text
   python <Skill目录>/scripts/tag_qc.py validate-vocab \
     --vocab <报告目录>/research/data/tag_vocab_draft.json \
     --preset <pilot|full> \
     --draft-md <报告目录>/research/data/tag_vocab_draft.md
   ```
3. **送签前收口、签字页与提问**。签字页存为 `research/data/tag_vocab_draft.md` 并通过 `validate-vocab --draft-md` 校验后，在送签前先运行阶段收口，将状态推进至 `stage2_draft_ready`（落盘 `run_state.json`、`handoff_resume.md`、`packet_manifest.json` 三件套）：
   ```text
   python -B <Skill目录>/scripts/data_pipeline.py checkpoint --out-dir <报告目录> --stage stage2_draft_ready
   ```
   随后在对话中用纯文本给出草稿和样例，按 `SKILL.md` 第二节将待确认问题写入 `research/data/handoff_questions.md` 的「## 待人工拍板事项」。等用户直接用中文打字批复。
   - **受控词表草稿**。列 `tag_id`、`一级`、`中文短词`、`轴向`、`一句话定义`、`纳入例`、`排除例`、`预计命中数（脚本派生）`、`emerging`。主题材的一级列填 `parent_domain` 对应中文名，其余轴不适用。
   - **脚本告警**。逐条原样保留脚本的每条告警，同一行含词条 ID 与告警原文。原文后紧跟中文说明，交代对应证据、已处理的动作或待确认的取舍。
   - **判定单元分布汇总**。按品类列四种 `visual_unit` 的产品数，标明 `L3` 缺半边与 `L2` 零实机图的产品。
   - **5 款预标样例**。优先选易混边界与降级样例。列 `产品名`、`主题材（1）`、`呈现（1）`、`融合（0–1）`、`叙事强度`、`叙事类型（0–1）`、`判定单元`、`截图序号`、`核心依据摘要`。主题材写「一级 › 二级」。每格有依据，空槽说明为何留空，序号缺失说明查过的图源。枚举值后禁止添加「（待复核）」「（待补证）」或半角括号形式，待核情况写进依据或缺口记录。
   - **签字抽核**。签字人对照截图序号抽核五个样例，重点检查降级单边截图是否与判定单元相符。遮住定义试读新增词、告警词与易混词，确认需合并、拆分或改写的词和样例。
4. **签字落盘**。收到文字批复后，按意见定稿写入 `research/data/tag_vocab.json`。`approved_by`、`approved_date`、`signoff_note` 写入 JSON 顶层，三字段不作为独立对话块发送。

---

## 四、Pass 2：闭集定标（每款 2–3 个受控 Tag）

- **槽位硬规则**：
  - `theme_primary`：必选 **1 个**；
  - `presentation`：必选 **1 个**，按第一节绑定实机证据或记录零实机图的降级情况。
  - `fusion_secondary`：选填 **0–1 个**，须通过第二节的独立可玩决策或副玩法循环判据。
  - 主槽总数严格 **2–3 个**，所有 `tag_id` 必须来自签字版 `tag_vocab.json` 且轴向匹配。`narrative_intensity` 和 `narrative_motif` 单独记录，按下节联动。
- **实机证据硬门禁**：每个终稿 Tag 至少绑定 1 条 `marketing_only: false` 的依据；若某产品仅靠买量文案显示某特征而实机与评论均无支撑，**严禁贴该 Tag**。

### 4.1 轻剧情判据与证据阶梯

| `narrative_intensity` | 判断依据 | `narrative_motif` 联动 |
|---|---|---|
| 重剧情 | 持续的角色目标、冲突和故事推进贯穿主要进程，章节、任务或解锁与主线推进相连。 | 必填一个受控母题。 |
| 轻剧情 | 可核实的角色或事件在部分任务、章节、对话中推进。主要玩法循环可独立开展。 | 有明确证据时可填一个受控母题，否则填 `null`。 |
| 无剧情 | 核实实机体验或公开材料后，仅见题材背景、角色装饰或功能提示，未见可追踪的情节推进。 | 必须填 `null`。 |

母题显示名为「叙事类型」，取 `configs/tag_blacklist.json` 的 `narrative_motif.controlled_enum`。强度和母题都不占主槽。

- 一级来源为 `store_screenshot`、`st_review`、`web_wiki_review`。二级来源为 `store_desc`。三级买量材料仅作辅证，禁止单独用于定标，也不得写入 `narrative_evidence`。
- 轻剧情和重剧情的终稿至少有 2 条独立非买量证据，且至少 1 条来自一级来源。证据按 `evidence_type` 与 `evidence_source_id` 的组合去重。无剧情至少有 1 条非买量核实记录，交代查过的内容。
- 轻剧情或重剧情证据不足时，Pass 2 可暂记 `confidence: "low"`，在终裁前补证。终裁仍缺一级非买量叙事原证时，降为「无剧情」并将 `narrative_motif` 写为 `null`。每条叙事证据需在材料包 `sources` 中找到对应来源。

轻剧情示例（来源 ID 按实际材料包填写）：

```json
{
  "narrative_intensity": "轻剧情",
  "narrative_motif": null,
  "narrative_evidence": [
    {
      "evidence_type": "store_screenshot",
      "evidence_source_id": "shot_example_01",
      "evidence_quote": "完成订单后弹出两名角色商量餐厅重开的短对话。",
      "marketing_only": false
    },
    {
      "evidence_type": "store_desc",
      "evidence_source_id": "meta_desc_example",
      "evidence_quote": "Unlock short conversations as you restore rooms. Keep serving orders between events.",
      "marketing_only": false
    }
  ]
}
```

### 提示词模板 B：Pass 2 闭集定标（可复制）

```text
请严格依据已签字的受控词表 <报告目录>/research/data/tag_vocab.json，为下列 <N> 款产品完成闭集定标。

硬约束：
1. 每款严格输出 2–3 个受控 Tag：1 个 theme_primary + 1 个 presentation + 0–1 个 fusion_secondary；只能使用词表中存在的 tag_id，且 slot 必须与词条 axis 一致。
2. 按 1 到 5 顺序全扫已落盘截图，先剔除封面、剧情拼贴、人物立绘与功能弹窗，再按品类首选顺位锁定判定单元。presentation 按人物与物体比例、可见材质与光影、色彩饱和度、空间立体感定标，并绑定已阅截图。缺半边最高 medium。判为 promo_or_portrait_only 时，产品 confidence 必填 "low"。fusion_secondary 按第二节核实独立可玩决策或副玩法循环，严禁填剧情、抉择、故事、悬疑等叙事词。按 4.1 填写 narrative_intensity、narrative_motif 和 narrative_evidence。
3. 严禁任何 Tag 仅依赖 marketing_only=true 的买量文案；每个 Tag 至少 1 条非买量实证（store_desc / store_screenshot / st_review / web_wiki_review）。
4. 每款必须填写 excluded_near_tags（至少 1 条：说明为何排除最接近的次选词）与 confidence（high | medium | low；材料不足或两词难分时如实标 low）。
5. 若签字词表缺少正确用词，在 vocab_gap 记下缺词的轴、候选词与证据，并标 confidence: "low"。严禁硬塞错词。必选槽缺词时，回到同一个词表签字环节，补齐并取得词表增补批复后再重跑该产品。待审记录不得进入终稿校验。
6. 严格按以下 JSON 结构写入 <输出文件路径>：
{
  "products": [
    {
      "unified_app_id": "...",
      "app_name": "...",
      "subgenre_id": "...",
      "screenshots_reviewed": ["research/media/screenshots/..."],
      "narrative_intensity": "无剧情",
      "narrative_motif": null,
      "narrative_evidence": [
        {
          "evidence_type": "st_review",
          "evidence_source_id": "st_review_...",
          "evidence_quote": "I just complete orders and there are no story scenes between levels.",
          "marketing_only": false
        }
      ],
      "vocab_gap": [],
      "tags": [
        {
          "slot": "theme_primary",
          "tag_id": "restaurant_cooking",
          "label_zh": "餐厅烹饪",
          "evidence_type": "store_desc",
          "evidence_source_id": "meta_desc_...",
          "evidence_quote": "Serve coffee, sandwiches, and seafood to rebuild your family restaurant.",
          "marketing_only": false
        },
        {
          "slot": "presentation",
          "tag_id": "bright_toon_volume",
          "label_zh": "圆润亮彩立体",
          "evidence_type": "store_screenshot",
          "evidence_source_id": "shot_...",
          "evidence_quote": "棋盘物件比例夸张，主场景可见高饱和配色、柔和阴影和前后遮挡。",
          "marketing_only": false,
          "screenshot_index": [1, 2],
          "visual_unit": "board_and_scene_pair",
          "primary_visual_region": "第 1 张下半部棋盘，第 2 张中央主场景",
          "visual_unit_fallback_reason": null
        }
      ],
      "confidence": "high",
      "excluded_near_tags": ["mansion_renovation: 翻修对象为海滨餐厅而非封闭老宅"],
      "web_verified": false
    }
  ]
}
```

合并全部批次落盘 `research/data/tags_final.json` 后，先跑格式与证据门禁校验：
```text
python <Skill目录>/scripts/tag_qc.py validate-tags \
  --vocab <报告目录>/research/data/tag_vocab.json \
  --tags <报告目录>/research/data/tags_final.json \
  --roster <报告目录>/research/data/product_roster.json \
  --bundle <报告目录>/research/data/tagging_input_bundle.json \
  --preset <pilot|full>
```

---

## 五、独立上下文盲态反向证伪重测与 `tag_qc.py` 三项门槛

禁止模型覆盖。只有可核验的 `harness_model_record` 才能支撑模型名披露或跨模型验证；缺记录时严禁写模型名或宣称跨模型验证，统一使用固定披露话术：`独立上下文盲态重测（未指定跨模型覆盖，运行环境不暴露子代理模型标识，不等于跨模型交叉验证）`。首次在本工作区运行或宿主换版本后，按第一节用当前工作区实图重验一次 `remote` 读图通道。

1. **生成脱敏盲测输入包**（自动分层抽样并物理剥离 Pass 1、Pass 2 答案与 ST Game IQ 字段）：
   ```text
   python <Skill目录>/scripts/tag_qc.py sample \
     --bundle <报告目录>/research/data/tagging_input_bundle.json \
     --tags <报告目录>/research/data/tags_final.json \
     --roster <报告目录>/research/data/product_roster.json \
     --preset <pilot|full> \
     --output <报告目录>/research/data/blind_retest_input.json
   ```
   - 抽样规模：`pilot` **5 款**（1 头部强剧情 + 2 易混/脏标签 + 2 新品或低置信）；`full` **12 款**（每品类分层抽 3 款）。
2. **拉起 `isolation: "remote"` 全新子代理**（严禁 `worktree`）。首次在本工作区或宿主换版本时先按第一节完成工作区实图读图核验；若读图通道失效直接硬停并登记 `L1` 缺口。日常盲测只用 `tag_vocab.json`、`blind_retest_input.json` 及抽样产品的本地商店截图，在同一独立上下文中按 1 到 5 顺序全扫已落盘截图，先剔除封面、剧情拼贴、人物立绘与功能弹窗，再按品类首选顺位锁定判定单元。盲测独立选图，禁止读取主判序号、`pass1_free_tags.json` 或 `tags_final.json`。

### 提示词模板 C：独立上下文盲态反向证伪重测（直接发给 `remote` 子代理）

```text
你是独立复核编辑，正在执行盲态一致性重测。禁止模型覆盖；无 harness_model_record 时严禁写模型名或宣称跨模型验证，统一使用固定披露话术：独立上下文盲态重测（未指定跨模型覆盖，运行环境不暴露子代理模型标识，不等于跨模型交叉验证）。首次在本工作区或宿主换版本时须按第一节重验一次 remote 读图通道。只读取以下两份文件及对应产品的本地商店截图，主判截图序号与其他历史打标文件均禁止读取。
- 受控词表：<报告目录>/research/data/tag_vocab.json
- 盲测原料包：<报告目录>/research/data/blind_retest_input.json

对盲测原料包中的每款产品，严格采用「先证伪排除、后锁定定标」顺序：
1. 阅读英文商店描述与评论样本。用 Read 独立按 1 到 5 顺序全扫已落盘截图，先剔除封面、剧情拼贴、人物立绘与功能弹窗，再按品类首选顺位锁定判定单元。presentation 按人物与物体比例、可见材质与光影、色彩饱和度、空间立体感四项可见特征定标，并独立写入 screenshot_index、visual_unit、primary_visual_region 与对应 evidence_quote。判为 promo_or_portrait_only 时，产品 confidence 必填 "low"。若读图通道失效无法读取工作区实图，直接硬停并在返回中说明，不做猜测。
2. 先列出 2 个在词表中最容易混淆、但必须排除的近义词，并为每个排除词写出 1 句基于原文或画面的反证。
3. 再从剩余受控词中选出 2–3 个最终 Tag（1 个 theme_primary + 1 个 presentation + 0–1 个 fusion_secondary），每词附非买量证据。融合须有独立可玩决策或副玩法循环，禁用剧情、抉择、故事、悬疑等叙事词。按第四节独立复核叙事强度、母题与证据。缺一级非买量叙事原证时，记「无剧情」并把 narrative_motif 写为 null。
4. 将结果按如下 JSON 结构写入 <报告目录>/research/data/blind_retest_output.json：
{
  "protocol": "blind_falsification_first_remote_retest",
  "protocol_disclosure": "独立上下文盲态重测（未指定跨模型覆盖，运行环境不暴露子代理模型标识，不等于跨模型交叉验证）",
  "products": [
    {
      "unified_app_id": "...",
      "app_name": "...",
      "narrative_intensity": "轻剧情",
      "narrative_motif": null,
      "confidence": "medium",
      "falsified_exclusions": [
        {"tag_id": "...", "counter_evidence": "..."},
        {"tag_id": "...", "counter_evidence": "..."}
      ],
      "tags": [
        {"slot": "theme_primary", "tag_id": "<主题材 ID>", "evidence_type": "store_desc", "evidence_quote": "..."},
        {
          "slot": "presentation",
          "tag_id": "<呈现 ID>",
          "evidence_type": "store_screenshot",
          "evidence_quote": "棋盘图中的物件比例夸张，配色饱和，物体遮挡与柔和阴影可见。",
          "screenshot_index": 2,
          "visual_unit": "gameplay_board",
          "primary_visual_region": "第 2 张下半部的棋盘操作区",
          "visual_unit_fallback_reason": "合成样本已全扫所有落盘截图，仅有实机棋盘，缺主场景。"
        }
      ]
    }
  ]
}
```

3. **核算三项门槛**：
   ```text
   python <Skill目录>/scripts/tag_qc.py evaluate \
     --tags <报告目录>/research/data/tags_final.json \
     --blind <报告目录>/research/data/blind_retest_output.json \
     --preset <pilot|full> \
     --output <报告目录>/research/data/qc_report.json
   ```
   - 主题材一致率 `theme_primary_agreement >= 80%`（`pilot` 至少 4/5，`full` 至少 10/12）。
   - 呈现一致率 `presentation_agreement >= 80%`（`pilot` 至少 4/5，`full` 至少 10/12）。
   - 主槽标签平均 Jaccard 重叠率 ≥ 75%。逐款按 `|A ∩ B| / |A ∪ B|` 核算，再求样本平均。A、B 分别为两轮的主槽标签集合，叙事维不纳入集合。
   - 按轴披露一致率与 `colliding_pairs_by_axis`，同时核算判定单元一致率 `visual_unit_agreement`。`evaluate` 自动校验 `blind_retest_output.json` 的 `protocol_disclosure`，并写入 `qc_report.json`。
   - **不达标处置**：根据 `qc_report.json` 定位冲突集中的词条对 → 修订 `tag_vocab.json` 的 `exclude_examples` → 仅重跑受影响词条关联产品的 Pass 2 → 重跑盲测直至达标。
4. **运行内终裁**。
   - 主会话读取当前档位的 `adjudication_owner`，按产品统一 ID 在 `research/data/adjudications.json` 写明确结论与证据。主题材、呈现、融合、叙事强度、叙事母题或判定单元有分歧的产品，以及 `confidence == "low"` 的产品，均须逐款裁决。默认 `main_session` 由主会话在本次运行内终裁。仅在本次已明确取得人工裁决时记人工来源。严禁生成 `adjudications_pending.json`。
   - 裁决变更叙事时，在 `adjudications.json` 写入对应的 `narrative_intensity`、`narrative_motif` 和 `narrative_evidence`。终裁仍缺一级非买量叙事原证时，写 `narrative_intensity: "无剧情"`、`narrative_motif: null`，并保留至少一条非买量核实记录。仅在 `note` 或 `decision` 写结论，不能替代结构化字段。
   - `finalize` 支持叙事字段、自由文本 `adjudicated_by` 和布尔标记 `adjudicated_by_human`。主会话终裁填实际裁决人并置 `false`，实际人工裁决置 `true`。用 `--bundle` 校验裁决后的叙事证据并冻结语料。
     ```text
     python <Skill目录>/scripts/tag_qc.py finalize \
       --vocab <报告目录>/research/data/tag_vocab.json \
       --tags <报告目录>/research/data/tags_final.json \
       --qc <报告目录>/research/data/qc_report.json \
        --adjudications <报告目录>/research/data/adjudications.json \
        --bundle <报告目录>/research/data/tagging_input_bundle.json \
        --output <报告目录>/research/data/tagged_corpus.json
      ```
   - `tagged_corpus.json` 的 `qc_summary.protocol_disclosure` 沿用 `qc_report.json` 中的披露。`finalize` 通过后，调用阶段收口命令（校验 Stage 2 产物并同步落盘 `research/run_state.json`、`research/handoff_resume.md`、`research/data/packet_manifest.json` 三件套，将状态更新为 `stage2_verified`；非停点阶段严禁生成 `handoff_questions.md`）：
     ```text
     python -B <Skill目录>/scripts/data_pipeline.py checkpoint --out-dir <报告目录> --stage stage2_verified
     ```
