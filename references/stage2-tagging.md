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

- 合成与三消全扫仍缺一半时，在 `research/data/gap_register.json` 记 `L3`，置信上限为 `medium`。同一品类缺半边的产品超过 30% 时，先联网补图并登记补图记录。未登记时，`validate-vocab` 与 `validate-tags` 均硬拦。
- 全扫后没有实机核心图，先查另一端公开商店页或实机评测补证。仍为零实机图时记 `L2`，填 `visual_unit: "promo_or_portrait_only"` 和 `confidence: "low"`。正式跨产品呈现占比的分母排除这类产品。报占比前先列判定单元分布，跨产品比较限同一判定单元。
- 在 `tags_final.json` 与 `blind_retest_output.json` 的呈现标签对象，以及词表预标 `hit_evidence` 中，填写 `screenshot_index`（与来源对应的正整数或正整数数组）、`visual_unit`、`primary_visual_region`。偏离首选时，另填 `visual_unit_fallback_reason`，写明全扫情况、缺少的画面与实际采用的单元。`screenshots_reviewed` 列出已阅截图。完全无图时把缺图记入缺口，截图序号和来源按实际材料登记。

### P6 逐图分类与呈现主证

Pass 1、Pass 2 与盲测的逐图记录须覆盖 `roster.screenshot_files` 中该款的全部落盘截图，按清单从 1 起依次记录正整数 `screenshot_index` 与 `image_kind`，禁止跳号或重复。
图源、图类与采用的判定单元分开记录。一张图含棋盘和主场景时，只记一个图类，分别记录两处观察区域。

| `image_kind` | 判别依据 | 呈现主证 |
|---|---|---|
| `raw_gameplay` | 直接截取的实际游戏画面，核心操作区或主场景可辨 | 使用截图中的实机区域 |
| `framed_gameplay` | 带宣传边框、标题或卖点框，但能辨认框内实机画面 | 只使用可定位的实机区域 |
| `promo_only` | 封面、立绘、过场插画、卖点拼贴等，缺少可辨认的实机区域 | 禁止作为呈现主证 |

- `image_kind` 取值使用 `visual_unit_policy.image_kind_enum`。买量或商店是取图渠道，不能据此代填图类。
- 选双区域判定单元时，两处主证都须来自合格实机区域。仅有棋盘或主场景时，按顺位降级并登记缺少的一半。
- 先记录宣传图的图类，再从呈现主证候选中剔除。补证后仍无合格实机图时执行 `L2`，相关截图仍留在逐图记录里。
- 两个校验命令读取冻结名单 `--roster` 与 Pass 1 记录 `--pass1`，按名单逐款核对全扫记录。只列已选主证的序号，不能充当全扫记录。
- 缺半边比例按同品类冻结名单核算，同一产品只计一次。全扫后超过 30% 时，`validate-vocab` 与 `validate-tags` 从 `research/data/gap_register.json` 核验补图记录，缺记录时硬拦。两条命令均可用 `--gaps` 指定该文件，缺省自动探测 `--roster` 同目录的 `gap_register.json`。
- 补图记录应能按统一 ID 找到补查来源、实际图源及结果。补到实机图后重做受影响产品的判定单元并同步证据索引。

**逐图记录与判定单元核对**

| 核对对象 | 记录方式 |
|---|---|
| 已落盘截图 | `screenshots_reviewed` 按实际阅读顺序列出相对路径 |
| 每张截图的图类 | 产品对象的 `screenshot_review` 逐张记录 `screenshot_index` 和 `image_kind`。序号用实际正整数，补图后与当前截图清单对齐 |
| 同图两处实机区域 | 逐图记录只列一次，呈现证据分别定位到棋盘和主场景 |
| 两张图组成判定单元 | 两张都进入逐图记录，呈现证据的序号与观察区域逐一对应 |
| 只有宣传图或找不到图 | 分别记录实际读过的图或缺失情况，按 `L2` 登记补查与降级 |

逐图核对从冻结名单开始。取该款所有已落盘截图，依次核对序号、路径与图类，再检查主证是否能回到同一款的合格实机区域。
已落盘但不参与定标的图也要留在全扫记录。补图前后的证据若有更换，缺口审计须能追溯到本轮采用的截图。

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
1. 对每款产品，阅读英文商店描述和过滤后玩家评论，用 Read 按 1 到 5 顺序全扫已落盘截图，先剔除封面、剧情拼贴、人物立绘与功能弹窗，再按品类首选顺位锁定判定单元。逐张按第一节填 image_kind，记录截图序号与观察区域，降级时记录原因。promo_only 不得作为呈现主证。
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
5. 命名 2–8 字、硬顶 10 字。严格按如下 JSON 数组结构写入 <输出文件路径>，不输出多余解释：
[
  {
    "unified_app_id": "...",
    "app_name": "...",
    "subgenre_id": "...",
    "screenshots_reviewed": ["<已阅相对路径1>", "<已阅相对路径2>", "<已阅相对路径3>"],
    "screenshot_review": [
      {"screenshot_index": 1, "image_kind": "raw_gameplay"},
      {"screenshot_index": 2, "image_kind": "framed_gameplay"},
      {"screenshot_index": 3, "image_kind": "promo_only"}
    ],
    "narrative_intensity": "轻剧情",
    "narrative_motif": null,
    "free_tags": [
      {
        "axis": "presentation",
        "label_zh": "<中文短词>",
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

### 两段闸门

脚本闸门先验真阻断。冻结名单、Pass 1 记录和词表草稿齐备后，先用 `validate-vocab` 核验，再用 `render-signoff` 生成签字页与摘要。
两个命令均退出 0，且告警原文完整、词表草稿、冻结名单与 Pass 1 记录的哈希齐备时，才可送签。
脚本错误在送签前修正。三份输入中任一改动，均须用更新后的输入重新验真并渲染。

人工闸门只拍业务裁决，例如合并或拆分词条、修订纳入与排除边界、裁定告警词是否保留。
两段之间不设自由聊天环节。禁止把手抄的脚本数字作为裁决依据。

| `tag_vocab.json` 顶层字段 | 写入者 | 内容 |
|---|---|---|
| `script_gate_receipt` | 脚本 | 验真回执，含脚本版本、命令退出码和输入哈希 |
| `signoff_note` | 签字人 | 人工裁决说明，记录决定及所依据的签字页条目 |

脚本回执与本轮送签输入对应。签字人以页内锚点指向词条、告警或样例，人工说明不能充作脚本回执。
签字意见改变词名、边界、证据或命中名单时，重新验真和渲染，并以最新签字稿及其哈希落盘。

1. **冻结名单与收敛规则**。
   - 按 Stage 1 的 `product_roster.json` 冻结入围名单，再按同一名单汇总 `pass1_free_tags.json` 和预标结果。三轴内各词条的 `hit_app_ids` 同轴严格互斥。主题材与呈现各覆盖冻结名单的 100%。融合的命中为冻结名单的互斥子集，单款最多命中 1 个融合词。
   - 合并同义与近义表达。颗粒相近但所指不同的词，要写清纳入与排除边界。
   - 剔除只出现 1 次且可归入上位具体词的过窄词。`validate-vocab` 按冻结名单核验 `emerging`。`pilot` 的 `emerging: true` 须为单例，该款在名单内标为新品，且 ST 生效首发日落在本轮滚动新品窗内，含起止日。扩至 `full` 后累计命中 ≥2 款时改回 `false`。无名单时不得签正式绿灯。命中数由脚本按 `hit_app_ids` 派生，与 `emerging` 分列。
   - 规模为 `pilot` **12–20 词**、`full` **25–40 词**。试点扩全量时保留合成试点已签字的 `tag_id`，补入其余三类词条。
   - 每条填写 `tag_id`（小写蛇形 `^[a-z][a-z0-9_]{1,39}$`）、`label_zh`（命名 2–8 字、硬顶 10 字）、`label_en`、`axis`、`definition`、`include_examples`、`exclude_examples` 和 `hit_app_ids`（去重的统一 ID，主轴词条至少 1 个有效 ID）。主题材另填 `parent_domain`，取 `primary_domains` 的 ID。`full_only` 一级只在全量使用，一级只作分组。
   - **主题材命名**。以玩家反复接触的对象或场景名词为主。词义已经完整时不硬加「经营／建设／管理」。玩法词不进题材轴。
     - 二级词描述可由多款产品共享的对象或场景。归属写在 `parent_domain`，词名无需重抄一级。
     - 两种近义场景若各有不同纳入条件，在定义和例子中交代可见差异。
   - **呈现命名**。先按可见特征区分绘制型与立体型，再依据四项可见特征划分具体词条。
     - 词名写可见材质与光影。画面能支持时，留意「高光明显或柔和体积明暗」，并在定义里写出可复核的判据。
     - 色彩、比例与空间线索可作为辅助区分。题材对象留在主题材轴，呈现定义与词名按实际看见的画面写。
   - **融合命名**。词名写「对象 + 独立循环」，选能体现排除边界的循环用词。
     - 同一对象既有自动推进也有独立可玩决策时，用词名指明命中的独立循环。纳入例写玩家能做的操作，排除例写看似相同却没有该循环的情况。
     - 词名即使看起来像独立玩法，实证仍须说明玩家的决策与循环。
   - 融合无合格词条时可归零。在 `fusion_zero_audit` 逐候选产品写实证与反证覆盖表，列出统一 ID、候选玩法、来源、可见操作或循环、反证及排除理由。
   - **三轴集中度**。主题材、呈现、融合均按 40%／60% 核验。单个标签占比按该词去重命中数除以该轴可计入的去重命中产品数计算。
     - 命中至少 5 款，或该轴分母至少 10 款（分别对应 `min_hits` 和 `min_axis_denominator`）时，才启动下表的集中度核验。
     - 呈现轴按第一节处理零实机图产品。融合轴仅在有合格实证时记命中，整轴无命中时不计算占比。
     - 比较门槛用未四舍五入的占比，展示值由脚本派生。
     - 分母资格先于命中计数判定。不能计入正式呈现分布的产品也不进入该项集中度的分子。

     | 样本与占比条件 | 签字前要求 |
     |---|---|
     | 命中数与轴分母都未达门槛（命中少于 5 款且分母少于 10 款） | 报小分母提示，不要求不可再分说明 |
     | 达到计数门槛，且占比不超过 40% | 无需集中度说明 |
     | 达到计数门槛，且占比超 40%、不超过 60% | 附两层不可再分表，逐产品说明 |
     | 达到计数门槛，且占比超 60% | 仍须附两层表。无人工 `concentration_waiver` 时脚本硬拦 |

     表注：门槛指命中至少 5 款或该轴分母至少 10 款，二者满足其一即可。

     人工豁免以实际裁决为据。出现硬拦时先修订草稿，或附上已有且适用于本轮词条的人工豁免，再重新验真。

   - **两层不可再分表**。每个触发说明的词条都在 `irreducibility_note` 下给出分组论证表和逐款明细。分组用于解释可行的拆分方式及其边界，逐款明细用于复核每个命中是否支持论证。

     | 层级 | 一行对应 | 每行所需依据 |
     |---|---|---|
     | 分组论证表 | 一个已经核查的候选分组 | 分组判据、所涉产品 ID、比较过的实证、不能按该判据再分的原因 |
     | 逐款明细 | 一款命中产品 | `unified_app_id`、对应分组、来源与证据定位、该款不能再分的具体原因 |

     - 呈现词的逐款明细带判定单元、截图序号和观察区域。主题材、融合词引用能判定对象、场景或玩法循环的原证。
     - 所有逐款明细 ID 的并集必须等于本词 `hit_app_ids`，每个 ID 能在冻结名单中找到。
     - 逐款行写本款的实证与理由，禁用「同上／如上／同前／见上」等占位词。共用判断写进分组表，逐款行仍须能独立核对。

   - **纳入例与排除例**。两类例子都要能说明定义的适用边界，名单 ID 用 `unified_app_id`。
     - `include_examples` 每条必带 `unified_app_id`，该 ID 必须属于本词 `hit_app_ids`。例中依据要能在对应产品材料中查到。
     - `exclude_examples` 如带名单 ID，该 ID 不得属于本词 `hit_app_ids`。排除理由写出与纳入判据冲突的原证。
     - 合并、拆分或调整命中时，同步更新例子、逐款明细和 `hit_app_ids`。一个例子引用的 ID 必须与所写依据来自同一产品。

2. **脚本预检与先验后渲**。落盘 `research/data/tag_vocab_draft.json` 后运行以下预检。

   ```text
   python -B <Skill目录>/scripts/tag_qc.py validate-vocab \
     --vocab <报告目录>/research/data/tag_vocab_draft.json \
     --preset <pilot|full> \
     --roster <报告目录>/research/data/product_roster.json \
     --pass1 <报告目录>/research/data/pass1_free_tags.json \
     [--gaps <报告目录>/research/data/gap_register.json]
   ```

   - 预检按名单逐款核对按 1–5 全扫的截图记录、词表命中、证据及缺半边的补图记录。
   - `validate-vocab` 通过后，按剔除顶层 `script_gate_receipt` 字段后的确定性内容计算草稿的回执哈希，将回执写入 `tag_vocab_draft.json`。退出码为 0 后才执行 `render-signoff`，使用本轮回执、最终草稿、冻结名单和 Pass 1 记录。
   - `render-signoff` 是签字文件的唯一渲染来源。将 `--summary-md` 指向 `research/data/tag_vocab_draft.md`，即可与报告根目录的 `tag_vocab_signoff.html` 同趟生成。两份使用同一组输入和脚本读数。

   ```text
   python -B <Skill目录>/scripts/tag_qc.py render-signoff --vocab ... --roster ... --pass1 ... --preset <pilot|full> --output <报告目录>/tag_vocab_signoff.html [--prev-vocab ...] [--summary-md ...]
   ```

   #### 签字页五区

   正常送签使用单页静态 HTML，按以下顺序把拍板决策放在证据明细之前。零 `<script>`、零 `<form>`、零 `localStorage`。翻阅、定位词条及核对告警均可离线完成。
   回执写入草稿后，对最终 `tag_vocab_draft.json`、冻结名单与 Pass 1 记录三份文件取哈希并嵌入 HTML，Markdown 摘要显示同一组哈希。三份文件中任一改动，均须重跑 `validate-vocab` 和 `render-signoff`。

   | 顺序 | 区域 | 脚本渲染内容 |
   |---|---|---|
   | 1 | 顶栏 | 本轮范围、脚本绿灯回执入口，以及融合轴允许部分产品留空的覆盖率提示 |
   | 2 | 3–5 张拍板决策卡 | 每张写清发生了什么、为何需人工拍板、默认与备选方案，以及可直接回复的一句话 |
   | 3 | 轮次 diff | 用 `--prev-vocab` 传入 `prev_vocab_draft.json`，按 `tag_id` 对照新增、删除、改名、边界与命中变化 |
   | 4 | 5 款样例 | 展示预标结果与证据定位，优先覆盖易混边界、实机降级和本轮变更 |
   | 5 | 三轴总览 | 按主题材、呈现、融合列出词条、定义、纳入例、排除例、命中数与占比，并挂接词条 diff |

   首轮没有 `--prev-vocab` 时省略轮次 diff 区块，只在顶栏以灰字注明「首轮无前稿」。
   页尾用一个折叠的 `<details>` 附录，依次展示带处置列的告警表、两层集中度表、判定单元与 `L3` 表、`fusion_zero_audit`。判定单元与 `L3` 表按品类列四种 `visual_unit` 的产品数，标出 `L3` 缺半边、`L2` 零实机图与补图结果。

   **三轴表与告警表**

   - 三轴表显示 `tag_id`、一级、中文短词、轴向、一句话定义、纳入例、排除例、预计命中数及 `emerging`。
   - 主题材的一级显示 `parent_domain` 对应中文名，其余轴留空。涉及集中度时显示轴分母、小分母提示及两层说明入口。
   - 告警表同一行保留 `tag_id` 与告警原文，附中文说明，处置列写处理动作或待定取舍并指向证据。告警列表为空时照载脚本的空结果，告警表仍占固定位置。
   - 告警里的数值、词条 ID 与门槛读数从本轮脚本输出带入。

   **判定单元表与样例**

   - 判定单元表能追到涉及的名单 ID 和补图记录，方便核对缺半边是否集中在同一品类。
   - 样例列出产品名、主题材、呈现、融合、叙事强度、叙事类型、判定单元、截图序号与核心依据摘要。
   - 主题材用「一级 › 二级」。空槽说明留空原因，缺截图序号时说明查过的图源。
   - 枚举值后禁加「（待复核）」「（待补证）」等括号注记，待核情况写进依据或缺口记录。
   - 签字人对照截图序号抽核五个样例。若样例降为单边，核对判定单元是否与画面相符，再试读新增词、告警词及易混词。

   **送签读数的来源**

   | 读数 | 本轮来源 |
   |---|---|
   | 词条命中、轴分母与占比 | 冻结名单、去重命中与脚本计算结果 |
   | 缺半边、零实机图与补图 | Pass 1 逐图记录、判定单元与缺口审计 |
   | 告警与脚本退出码 | 本轮脚本闸门的原始回执 |
   | 词条及命中变化 | 本轮词表草稿与 `prev_vocab_draft.json` 的差异 |

   **拍板问题**

   - 决策卡标明编号并附证据锚点。若问题涉及合并或拆分，选项应能落实到词条及命中名单。
   - 每个锚点定位到对应的词条、告警、判定单元或样例。决策卡只用静态内容展示，批复走第 3 条的文字回复。

3. **送签前收口与纯文本兜底**。两条脚本命令均为 0、签字文件与回执齐备后，送签前运行阶段收口。状态推进至 `stage2_draft_ready`，落盘 `run_state.json`、`handoff_resume.md`、`packet_manifest.json` 三件套。

   ```text
   python -B <Skill目录>/scripts/data_pipeline.py checkpoint --out-dir <报告目录> --stage stage2_draft_ready
   ```

   - 正常送签交付静态 HTML。宿主不能直接打开静态 HTML 时，使用 `render-signoff` 的 Markdown 摘要作纯文本兜底，照搬脚本渲染稿。
   - 仅纯文本兜底沿用固定顺序：三轴表 → 告警表 → 判定单元表 → 5 款样例 → 拍板问题。禁止手写任何数字。
   - 将待拍板事项按 `SKILL.md` 第二节写入 `research/data/handoff_questions.md` 的「## 待人工拍板事项」。
   - 对话只发短裁决清单（编号、问题、选项、锚点）与下载指针，请用户直接打字批复。

4. **签字落盘**。收到文字批复后，按意见定稿写入 `research/data/tag_vocab.json`。
   - `approved_by`、`approved_date`、`signoff_note` 写入顶层，随脚本写入的顶层 `script_gate_receipt` 一起保存。签字字段不作为独立对话块发送。
   - 送签后若依据发生变化，回到脚本闸门。以重新验真并经人工裁决的稿件进入 Pass 2。

---

## 四、Pass 2：闭集定标（每款 2–3 个受控 Tag）

- **槽位硬规则**：
  - `theme_primary`：必选 **1 个**；
  - `presentation`：必选 **1 个**，按第一节绑定实机证据或记录零实机图的降级情况。
  - `fusion_secondary`：选填 **0–1 个**，须通过第二节的独立可玩决策或副玩法循环判据。
  - 主槽总数严格 **2–3 个**，所有 `tag_id` 必须来自签字版 `tag_vocab.json` 且轴向匹配。`narrative_intensity` 和 `narrative_motif` 单独记录，按下节联动。
- **实机证据硬门禁**：每个终稿 Tag 至少绑定 1 条 `marketing_only: false` 的依据；若某产品仅靠买量文案显示某特征而实机与评论均无支撑，**严禁贴该 Tag**。

### P7 排除对象与叙事证据

- 逐款核对 `excluded_near_tags` 与终稿 `tags`。排除项不得含该款终稿自身的任何 `tag_id`，即便理由只针对该词的某种解释。
- 排除理由要能指出最接近的词在本款缺少的条件。换词后同步更新排除项，避免把新定稿的词留在旧排除记录里。
- `narrative_evidence` 写角色、事件、任务或对话如何推进。无剧情的核实记录写查过的内容及可观察到的情节缺口。
- `narrative_evidence` 不得复制呈现截图的描述。同一截图既出现景物又出现可核实的叙事内容时，分别定位呈现区域与叙事区域，各写对应观察。

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
2. 按 1 到 5 顺序全扫已落盘截图，逐张记录 screenshot_index 与 image_kind。promo_only 不得作为呈现主证。先剔除封面、剧情拼贴、人物立绘与功能弹窗，再按品类首选顺位锁定判定单元。presentation 按人物与物体比例、可见材质与光影、色彩饱和度、空间立体感定标，并绑定已阅截图。缺半边最高 medium。判为 promo_or_portrait_only 时，产品 confidence 必填 "low"。fusion_secondary 按第二节核实独立可玩决策或副玩法循环，严禁填剧情、抉择、故事、悬疑等叙事词。按 4.1 填写 narrative_intensity、narrative_motif 和 narrative_evidence。
3. 严禁任何 Tag 仅依赖 marketing_only=true 的买量文案；每个 Tag 至少 1 条非买量实证（store_desc / store_screenshot / st_review / web_wiki_review）。
4. 每款必须填写 excluded_near_tags（至少 1 条，说明为何排除最接近的次选词。不得含终稿自身 tag_id）与 confidence（high | medium | low；材料不足或两词难分时如实标 low）。narrative_evidence 不得复制呈现截图描述。
5. 若签字词表缺少正确用词，在 vocab_gap 记下缺词的轴、候选词与证据，并标 confidence: "low"。严禁硬塞错词。必选槽缺词时，回到同一个词表签字环节，补齐并取得词表增补批复后再重跑该产品。待审记录不得进入终稿校验。
6. 严格按以下 JSON 结构写入 <输出文件路径>：
{
  "products": [
    {
      "unified_app_id": "...",
      "app_name": "...",
      "subgenre_id": "...",
      "screenshots_reviewed": ["<已阅相对路径1>", "<已阅相对路径2>"],
      "screenshot_review": [
        {"screenshot_index": 1, "image_kind": "raw_gameplay"},
        {"screenshot_index": 2, "image_kind": "framed_gameplay"}
      ],
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
python -B <Skill目录>/scripts/tag_qc.py validate-tags \
  --vocab <报告目录>/research/data/tag_vocab.json \
  --tags <报告目录>/research/data/tags_final.json \
  --roster <报告目录>/research/data/product_roster.json \
  --pass1 <报告目录>/research/data/pass1_free_tags.json \
  --bundle <报告目录>/research/data/tagging_input_bundle.json \
  --preset <pilot|full> \
  [--gaps <报告目录>/research/data/gap_register.json]
```

`validate-tags` 按冻结名单依次核对 Pass 1、Pass 2 在 1–5 范围内的全部已落盘截图，再核对闭集主证。
同品类缺半边超 30% 时按第一节读取 `research/data/gap_register.json` 核验补图记录，补齐缺失记录后再跑。

---

## 五、独立上下文盲态反向证伪重测与 `tag_qc.py` 三项门槛

禁止模型覆盖。只有可核验的 `harness_model_record` 才能支撑模型名披露或跨模型验证；缺记录时严禁写模型名或宣称跨模型验证，统一使用固定披露话术：`独立上下文盲态重测（未指定跨模型覆盖，运行环境不暴露子代理模型标识，不等于跨模型交叉验证）`。首次在本工作区运行或宿主换版本后，按第一节用当前工作区实图重验一次 `remote` 读图通道。

1. **生成脱敏盲测输入包**（自动分层抽样并物理剥离 Pass 1、Pass 2 答案与 ST Game IQ 字段）：
   ```text
   python -B <Skill目录>/scripts/tag_qc.py sample \
     --bundle <报告目录>/research/data/tagging_input_bundle.json \
     --tags <报告目录>/research/data/tags_final.json \
     --roster <报告目录>/research/data/product_roster.json \
     --preset <pilot|full> \
     --output <报告目录>/research/data/blind_retest_input.json
   ```
   - 抽样规模：`pilot` **5 款**（1 头部强剧情 + 2 易混/脏标签 + 2 新品或低置信）；`full` **12 款**（每品类分层抽 3 款）。
2. **拉起 `isolation: "remote"` 全新子代理**（严禁 `worktree`）。首次在本工作区或宿主换版本时先按第一节完成工作区实图读图核验；若读图通道失效直接硬停并登记 `L1` 缺口。日常盲测只用 `tag_vocab.json`、`blind_retest_input.json` 及抽样产品的本地商店截图，在同一独立上下文中按 1 到 5 顺序全扫已落盘截图，先剔除封面、剧情拼贴、人物立绘与功能弹窗，再按品类首选顺位锁定判定单元。盲测独立选图，禁止读取主判序号、`pass1_free_tags.json` 或 `tags_final.json`。
   - 每张已阅截图都按第一节登记 `image_kind`。分类由盲测子代理按实图独立完成，`promo_only` 不得作为呈现主证。
   - 若正向证据分布在同一张图的两处区域，分别注明位置。若实机证据不足，按第一节记录采用的降级单元与原因。

### 提示词模板 C：独立上下文盲态反向证伪重测（直接发给 `remote` 子代理）

```text
你是独立复核编辑，正在执行盲态一致性重测。禁止模型覆盖；无 harness_model_record 时严禁写模型名或宣称跨模型验证，统一使用固定披露话术：独立上下文盲态重测（未指定跨模型覆盖，运行环境不暴露子代理模型标识，不等于跨模型交叉验证）。首次在本工作区或宿主换版本时须按第一节重验一次 remote 读图通道。只读取以下两份文件及对应产品的本地商店截图，主判截图序号与其他历史打标文件均禁止读取。
- 受控词表：<报告目录>/research/data/tag_vocab.json
- 盲测原料包：<报告目录>/research/data/blind_retest_input.json

对盲测原料包中的每款产品，严格采用「先证伪排除、后锁定定标」顺序：
1. 阅读英文商店描述与评论样本。用 Read 独立按 1 到 5 顺序全扫已落盘截图，逐张记录 screenshot_index 与 image_kind。promo_only 不得作为呈现主证。先剔除封面、剧情拼贴、人物立绘与功能弹窗，再按品类首选顺位锁定判定单元。presentation 按人物与物体比例、可见材质与光影、色彩饱和度、空间立体感四项可见特征定标，并独立写入 screenshot_index、visual_unit、primary_visual_region 与对应 evidence_quote。判为 promo_or_portrait_only 时，产品 confidence 必填 "low"。若读图通道失效无法读取工作区实图，直接硬停并在返回中说明，不做猜测。
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
      "screenshots_reviewed": ["<已阅相对路径1>", "<已阅相对路径2>", "<已阅相对路径3>"],
      "screenshot_review": [
        {"screenshot_index": 1, "image_kind": "promo_only"},
        {"screenshot_index": 2, "image_kind": "raw_gameplay"},
        {"screenshot_index": 3, "image_kind": "promo_only"}
      ],
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
   python -B <Skill目录>/scripts/tag_qc.py evaluate \
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
   - `finalize` 支持叙事字段、自由文本 `adjudicated_by` 和布尔标记 `adjudicated_by_human`。主会话终裁填实际裁决人并置 `false`，实际人工裁决置 `true`。用 `--bundle` 校验裁决后的叙事证据。终裁改标后按最终命中复验一次 `emerging`，再冻结语料。
     ```text
     python -B <Skill目录>/scripts/tag_qc.py finalize \
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
