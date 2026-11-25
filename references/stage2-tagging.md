# Stage 2：三层打标、受控词表签字与独立上下文盲检规范

本阶段目标：把 Stage 1 产出的 `tagging_input_bundle.json` 与本地商店截图，经过「Pass 1 自由发散 → Pass 1.5 词表收敛与人工批复 → Pass 2 闭集定标 → `remote` 子代理盲态反向证伪重测 → `tag_qc.py` 三项门槛与低置信终裁」，冻结为可 100% 追溯的 `research/data/tagged_corpus.json`。

所有门槛数值以 [configs/tag_blacklist.json](../configs/tag_blacklist.json) 为唯一权威来源。

---

## 一、三层打标材料与读图深度

每款入围产品在 `tagging_input_bundle.json` 中结构化提供三层材料：

| 层级 | 材料内容 | 在打标中的权重与证据规则 |
|---|---|---|
| **第一层：商店官方事实** | 游戏名、副标题、完整英文商店描述、发行商、ST 生效首发日、商店发行日、地区属性（`cn_ios_only`）、ST Game IQ 参考字段 | 英文商店描述为**主题材主依据**；ST Game IQ 标 `[reference_only]` 仅作对照，盲态重测时物理剥离 |
| **第二层：视觉呈现（必看实图）** | `research/media/screenshots/<id>_01..05.<ext>`（已落盘最多 5 张商店截图）与买量封面 URL | **每款产品必用 `Read` 查看前 2–3 张核心商店截图**，用于实证核验 `presentation` 呈现槽位（画风与渲染形态）；第 4–5 张截图与买量封面仅在画风难辨或图文冲突时按需补看 |
| **第三层：玩家原声与买量辅证** | ST 近 60–90 天过滤后评论（20–30 条，剔除 <10 词水评与纯崩溃反馈，兼顾高低星，保留评论 `id` 与日期）+ 境外高曝光前 3–5 条买量 `slogan` | 玩家评论为**真实剧情/玩法体感强依据**；买量 `slogan` 一律标 **`marketing_only: true`（仅辅证，绝不能单独支撑任何终稿 Tag）** |

**条件联网补证触发规则**：仅当满足以下任一情形时，调用 `web-search` 或 `chromium-headless` 查公开商店页/Fandom/实机评测并登记来源 URL：
1. 英文商店描述缺失或不足 80 词；
2. 买量 `slogan` 出现强狗血/复仇/求生剧情，但商店描述与评论均未提及剧情（防买量假剧情污染）；
3. 看完前 3 张商店截图仍无法区分核心画风或玩法融合形态。

### 云端子代理双路径读图条款

拉起 `remote` 子代理执行盲检前，使用 1 张样本外截图做读图探针。探针要求复述画面中可核对的细节，以确认该执行环境具备图像通道。

- **图像通道可用**。子代理在同一独立上下文中读文本和每款前 2–3 张实机图，完成各轴盲态重测。
- **图像通道不可用**。子代理只盲测主题材、融合与叙事维的文本轴。另开洁净且具视觉通道的会话，只用签字版词表、样本 ID 和前 2–3 张实机图独立重测 `presentation`。视觉会话不读取原始定标或文本盲测结果。主会话按 `unified_app_id` 合并两路结果至 `blind_retest_output.json`，并将 `protocol_disclosure` 写为「文本与视觉分通道独立上下文重测」。

---

## 二、Pass 1：自由词发散（每款 5–8 个）

- **分批节奏**：每次处理 **5–6 款同品类产品**（每款看 2–3 张核心截图，单批 10–18 张图，保持多模态上下文清晰）。`full` 模式可按四品类分派 `isolation: "remote"` 子代理并行跑 Pass 1。
- **禁泛词前置意识**：禁止输出品类/平台/空洞形容词（完整黑名单见 `configs/tag_blacklist.json`），必须写玩家与编辑能直接想象画面的具象词。
- **落盘文件**：合并写入 `research/data/pass1_free_tags.json`。

### 提示词模板 A：Pass 1 自由词发散（可直接复制给本批会话或 `remote` 子代理）

```text
你是资深休闲游戏内容编辑。下面为你提供 <N> 款游戏的材料包（位于 <报告目录>/research/data/tagging_input_bundle.json 中的指定产品列表）以及它们各自的本地商店截图路径。

执行步骤：
1. 对每款产品，先阅读其英文商店描述与过滤后玩家评论，再使用 Read 工具实际查看前 2–3 张核心商店截图（若风格或玩法存疑再补看第 4–5 张或联网核实）。
2. 用玩家与编辑的具象语言，为每款产品提出 5–8 个自由候选 Tag，按三个主轴标注 axis，另记叙事强度和按强度联动的母题：
   - theme_primary（主题材：玩家反复经营、修复或处理的具体对象，如「烘焙店」「海滨餐厅」）
   - presentation（呈现形式：先看实机棋盘与主场景，按人物与物体比例、渲染质感与光影、色彩饱和度、空间立体度四项定标，须有商店截图实证）
   - fusion_secondary（有独立操作或进度的副题材或玩法融合，如「家园装修」「商业经营」）。严禁在 fusion_secondary 中使用剧情、抉择、故事、悬疑一类叙事词。
   - narrative_intensity（必填，按第四节判为重剧情、轻剧情或无剧情）
   - narrative_motif（叙事类型：可选，按配置中的受控枚举从家族八卦、豪门遗产、复仇翻身、失踪悬案、灾后求生中选一个，不占主槽，无剧情留空）
3. 严禁使用品类、平台或空泛形容词（如二合、三消、混变、模拟经营、休闲、益智、有趣、解压、现代题材、2D、3D、剧情向）。
4. 每个候选词必须附带：
   - evidence_type：store_desc | store_screenshot | st_review | web_wiki_review | ad_creative
   - evidence_source_id：对应材料包中的 source_id（截图写本地截图 source_id）
   - evidence_quote：≤40 词的原文摘录或截图画面客观描述
   - marketing_only：仅来自 ad_creative 时为 true，其余为 false
5. 严格按如下 JSON 数组结构写入 <输出文件路径>，不输出多余解释：
[
  {
    "unified_app_id": "...",
    "app_name": "...",
    "subgenre_id": "...",
    "screenshots_reviewed": ["<相对路径1>", "<相对路径2>"],
    "narrative_intensity": "轻剧情",
    "narrative_motif": null,
    "free_tags": [
      {
        "axis": "theme_primary|presentation|fusion_secondary",
        "label_zh": "≤6字中文短词",
        "label_en": "concise English label",
        "evidence_type": "store_desc|store_screenshot|st_review|web_wiki_review|ad_creative",
        "evidence_source_id": "...",
        "evidence_quote": "≤40 words quote or factual visual observation",
        "marketing_only": false
      }
    ]
  }
]
```

---

## 三、Pass 1.5：词表收敛与人工签字闸门（唯一必停停顿）

1. **收敛规则**：
   - 汇总全量 `pass1_free_tags.json`；
   - 合并同义与近义表达（如「烘焙甜点」「餐厅做菜」视颗粒度决定合并或显式切分边界）；
   - 剔除仅出现 1 次且可归入上位具体词的过窄词；**例外**：若该孤立词来自近 12 个月已核验新品且代表全新品类/题材方向，允许保留并标 `emerging: true`。命中数由脚本从 `hit_app_ids` 派生，与 `emerging` 分列，禁止人工填写。
   - **规模门槛**：`pilot` 控在 **12–20 词**；`full` 控在 **25–40 词**；
   - **试点扩全量冻结规则**：从 `pilot` 扩充至 `full` 时，**冻结合成试点已签字的原有 `tag_id` 不改名、不删除**，补入其余三类所需的新词条；
   - 每个词条必写 8 个字段：`tag_id`（小写蛇形 `^[a-z][a-z0-9_]{1,39}$`）、`label_zh`（建议 ≤6 字，硬上限 8 字）、`label_en`、`axis`、`definition`、`include_examples`、`exclude_examples`（用于区分易混词）、`hit_app_ids`（去重的命中产品统一 ID 数组，主轴词条至少 1 个有效 ID）。禁写大类名与玩法名，如二合、消除。
   - 主题材词条另须填写 `parent_domain`，取 `primary_domains` 的 `id`。标为 `full_only` 的一级仅在 `full` 使用。一级只作分组，不进标签计数。
2. **脚本预检**：落盘 `research/data/tag_vocab_draft.json` 后运行：
   ```text
   python <Skill目录>/scripts/tag_qc.py validate-vocab \
     --vocab <报告目录>/research/data/tag_vocab_draft.json \
     --preset <pilot|full>
   ```
3. **人工签字输出格式（对话内直接批复，同步存盘 `research/data/tag_vocab_draft.md`）**：
   在对话回复中直接输出以下两张表并**立即停止等待人工批复**：
   - **表一 · 受控词表草稿（共 X 词）**：列 `tag_id` | `一级` | `中文短词` | `轴向` | `一句话定义` | `纳入例` | `排除例` | `预计命中数（脚本派生）` | `emerging`。主题材的一级列填 `parent_domain` 对应的中文名，其余轴留空。
   - **表二 · 5 款代表产品预标样例**：列 `产品名` | `主题材（1）` | `呈现（1）` | `融合（0–1）` | `叙事强度` | `叙事类型（0–1）` | `核心依据摘要`（专挑易混淆边界产品方便校准）。主题材列写「一级 › 二级」。
   - **签字前试读**：遮住定义，请试读者仅凭显示名试读新增词、告警词与易混词对。
   - **附 3 行批复提示**：请直接回复「`通过`」，或指出需合并/拆分的 `tag_id` 与样例改法；批复后我将锁定 `tag_vocab.json` 并完成定标、盲检与看板生成。
4. **签字落盘**：收到人工批复后，按修改意见定稿写入 `research/data/tag_vocab.json`（顶部记录 `approved_by`、`approved_date`、`signoff_note`）。

---

## 四、Pass 2：闭集定标（每款 2–3 个受控 Tag）

- **槽位硬规则**：
  - `theme_primary`：必选 **1 个**；
  - `presentation`：必选 **1 个**（必须有已核验的商店截图实证）；
  - `fusion_secondary`：选填 **0–1 个**（仅双核心并重时填写，单核产品留空）；
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
- 轻剧情或重剧情证据不足时，Pass 2 可暂记 `confidence: "low"`，在终裁前补齐证据并复核判断。每条叙事证据需在材料包 `sources` 中找到对应来源。

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
2. 查看前 2–3 张核心商店截图。presentation 先看实机棋盘与主场景，按人物与物体比例、渲染质感与光影、色彩饱和度、空间立体度四项定标，并绑定已阅截图。严禁在 fusion_secondary 中使用剧情、抉择、故事、悬疑一类叙事词。按 4.1 填写 narrative_intensity、narrative_motif 与 narrative_evidence。
3. 严禁任何 Tag 仅依赖 marketing_only=true 的买量文案；每个 Tag 至少 1 条非买量实证（store_desc / store_screenshot / st_review / web_wiki_review）。
4. 每款必须填写 excluded_near_tags（至少 1 条：说明为何排除最接近的次选词）与 confidence（high | medium | low；材料不足或两词难分时如实标 low）。
5. 若签字词表缺少正确用词，在 vocab_gap 记下缺词的轴、候选词与证据，并标 confidence: "low"。严禁硬塞错词。必选槽缺词时，先登记缺口，补齐并取得词表增补批复后再重跑该产品。待审记录不得进入终稿校验。
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
          "tag_id": "cartoon_3d",
          "label_zh": "欧美Q版3D",
          "evidence_type": "store_screenshot",
          "evidence_source_id": "shot_...",
          "evidence_quote": "棋盘物件与人物比例夸张，饱和配色搭配柔和阴影，主场景采用有纵深的立体布景。",
          "marketing_only": false
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

当前工作区实测单模型运行，因此通过**四项隔离手段**保障重测独立性：

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
2. **拉起 `isolation: "remote"` 全新子代理**（严禁 `worktree`）：先执行第一节的读图探针并选择通道。重测只用 `tag_vocab.json`、`blind_retest_input.json` 及抽样产品的前 2–3 张本地截图，严禁读取 `pass1_free_tags.json` 或 `tags_final.json`。

### 提示词模板 C：独立上下文盲态反向证伪重测（直接发给 `remote` 子代理）

```text
你是独立复核编辑，正在执行盲态一致性重测。请只读取以下两份文件及对应产品的本地商店截图，不要读取目录中任何其他历史打标文件：
- 受控词表：<报告目录>/research/data/tag_vocab.json
- 盲测原料包：<报告目录>/research/data/blind_retest_input.json

对盲测原料包中的每款产品，严格采用「先证伪排除、后锁定定标」顺序：
1. 阅读英文商店描述与评论样本，有图像通道时用 Read 查看前 2–3 张本地商店截图。presentation 先看实机棋盘与主场景，按人物与物体比例、渲染质感与光影、色彩饱和度、空间立体度四项定标。无图像通道时按第一节分通道重测。
2. 先列出 2 个在词表中最容易混淆、但必须排除的近义词，并为每个排除词写出 1 句基于原文或画面的反证。
3. 再从剩余受控词中锁定 2–3 个最终 Tag（1 个 theme_primary + 1 个 presentation + 0–1 个 fusion_secondary），每词附非买量证据。严禁在 fusion_secondary 中使用剧情、抉择、故事、悬疑一类叙事词。按第四节独立复核 narrative_intensity、narrative_motif 与叙事证据。
4. 将结果按如下 JSON 结构写入 <报告目录>/research/data/blind_retest_output.json：
{
  "protocol": "blind_falsification_first_remote_retest",
  "protocol_disclosure": "同模型独立上下文重测",
  "products": [
    {
      "unified_app_id": "...",
      "app_name": "...",
      "narrative_intensity": "轻剧情",
      "narrative_motif": null,
      "falsified_exclusions": [
        {"tag_id": "...", "counter_evidence": "..."},
        {"tag_id": "...", "counter_evidence": "..."}
      ],
      "tags": [
        {"slot": "theme_primary", "tag_id": "<主题材 ID>", "evidence_type": "store_desc", "evidence_quote": "..."},
        {"slot": "presentation", "tag_id": "<呈现 ID>", "evidence_type": "store_screenshot", "evidence_quote": "..."}
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
   - 按轴披露一致率与 `colliding_pairs_by_axis`。分通道重测时，`evaluate` 写出 `qc_report.json` 后，将 `blind_retest_output.json` 的 `protocol_disclosure` 写回质检报告同名字段，再运行 `finalize`，以便如实传递到附录。
   - **不达标处置**：根据 `qc_report.json` 定位冲突集中的词条对 → 修订 `tag_vocab.json` 的 `exclude_examples` → 仅重跑受影响词条关联产品的 Pass 2 → 重跑盲测直至达标。
4. **低置信与分歧项人工终裁**：
   - 主题材、呈现、融合、叙事强度四轴分歧均须逐款显式裁决。叙事母题分歧也列入裁决表，并同步复核强度联动。`confidence == "low"` 的产品一并交人工裁决。
   - 若裁决改变叙事强度或母题，先在 `tags_final.json` 回写对应字段与证据，再重跑 `validate-tags` 和 `evaluate`。将裁决结果写入 `research/data/adjudications.json` 后运行冻结命令：
     ```text
     python <Skill目录>/scripts/tag_qc.py finalize \
       --vocab <报告目录>/research/data/tag_vocab.json \
       --tags <报告目录>/research/data/tags_final.json \
       --qc <报告目录>/research/data/qc_report.json \
       --adjudications <报告目录>/research/data/adjudications.json \
       --output <报告目录>/research/data/tagged_corpus.json
     ```
   - `tagged_corpus.json` 的 `qc_summary.protocol_disclosure` 沿用 `qc_report.json` 的披露文字。核对披露后更新 `run_state.json` 为 `stage2_verified`。
