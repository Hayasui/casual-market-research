---
name: casual-tag-market-research
description: "研究休闲游戏四品类（合成、三消、混变益智、模拟经营）市场图景与可追溯产品 Tag 供给差：用 Sensor Tower 核算全球大盘、榜单与新品，结合商店元数据与实机截图定标题材、呈现与融合 Tag，输出供给差矩阵、立项机会卡与影视剧情借鉴，交付离线看板 HTML。不用于单品纯玩法竞品深挖、不用于指定 IP 改编评级、不用于文化或时代专题。"
---

# 休闲品类 Tag 市场研究与立项看板
当前版本：v0.2.0

服务休闲游戏（优先合成）立项选题：按「概览 → 市场盘面 → 标签分布矩阵与立项方向 → 品类明细 → 高增新品观察 → 影视参考（开启时）→ 附录」编排离线 HTML 看板，提供一屏概览、矩阵与品类明细、折叠深读。

## 一、预设模式与配置文件

**开工直报默认值**。首段用实际日期和人话报清本次口径。默认跑合成试点，市场看最近 6 个完整自然月，新品看从一年前同日到运行当日的滚动窗口，起止日都算。低置信与盲测分歧由主会话按 `adjudication_owner` 在本次运行内终裁。用户未提出异议即开跑。若用户指定档位或日期，首段报实际采用的口径。

品类展示顺序固定为**合成、三消、混变益智、模拟经营**。合成（`merge`）等于 ST `Match Merge 2 + Match Merge 3`。

开机先读取三份配置文件（不要凭记忆改写枚举、门槛或路径）：

- 路径、读图与脱敏边界：[configs/workspace.json](configs/workspace.json)
- 四品类 ST 标签过滤、排除项、下载增补开关与 `pilot`/`full` 预设：[configs/subgenres.json](configs/subgenres.json)
- 三轴槽位、叙事强度与母题、禁泛词黑名单、词表规模与盲态抽检门槛：[configs/tag_blacklist.json](configs/tag_blacklist.json)

| 预设 | 覆盖范围 | 受控词表 / 抽检 | 机会卡 / 影视 | HTML 脚本模式 | 人工停点与续跑 |
|---|---|---|---|---|---|
| `pilot`（默认首跑） | 合成（`merge`）单品类 | 12–20 词 / 抽 5 款 | 机会卡占位。影视参考默认关闭（`enable_film_refs: false`），开启后选两部作现状对照 | **严格零 `<script>`**（纯 CSS Tab + `<details>`） | 仅词表签字 1 次人工停点。签字交付单页零脚本 HTML `tag_vocab_signoff.html`，其余阶段同会话推进或按 `checkpoint` 续跑 |
| `full` | 合成、三消、混变益智、模拟经营四品类全量 | 25–40 词 / 抽 12 款（每品类 3 款） | 完整机会卡。影视参考默认关闭（`enable_film_refs: false`），开启后选六到八部 | 允许白名单 ≤150 行无外链内联脚本（禁脚本仍可读） | 仅词表签字 1 次人工停点。签字交付单页零脚本 HTML `tag_vocab_signoff.html`，其余阶段同会话推进或按 `checkpoint` 续跑 |

上表的影视数量只在开启后生效。看板的 HTML 脚本模式按档位执行，词表签字页在两档均为单页静态 HTML。

## 二、目录黑板、环境自检与阶段收口

1. **目录位置**：报告目录建在 Skill 之外，显式 `--out-dir` 优先；未指定时按 `configs/workspace.json` 的 `default_report_subdir` 与 `report_root_resolution` 推导为 `<pwd>/reports/<报告目录名>/`；脚本仍强制通过 `--out-dir` 传入绝对路径，**不向技能目录或 `readonly_paths_blacklist`（如 `/data-cfs-extra/project_data/用研市场调研助手`）写入任何文件**。
2. **开机自检与按需加载**：开工先跑 `python -B <Skill目录>/scripts/data_pipeline.py doctor --out-dir <报告目录> --write-probe`，校验环境、配置与路径边界；`doctor` 输出的 `agent_level_checks_pending` 须由主会话补跑两条只读 MCP 探针（ST 与元数据通道），首次在本工作区运行或宿主换版本时再补一次 `remote` 子代理读图核验。单人跨会话续跑时先读 `<报告目录>/research/handoff_resume.md` 与 `<报告目录>/research/run_state.json`，**只加载当前阶段对应的 1–2 份 `references/`**，不要整目录读入。
3. **子代理规则**：本工作区默认单模型通跑；并发富化或盲态重测拉起子代理时**只用 `isolation: "remote"`，严禁 `worktree`**。主会话与新拉起子代理在取数前须确认只读 MCP 探针可用再取数。
4. **阶段收口与续跑**：四个阶段末统一调用 `python -B <Skill目录>/scripts/data_pipeline.py checkpoint --out-dir <报告目录> --stage <阶段>`（`stage1_done`、`stage2_draft_ready`、`stage2_verified`、`stage3_done`、`stage4_built`、`delivered`），落盘并校验 `run_state.json`、`handoff_resume.md`、`packet_manifest.json` 三件套；取消为防上下文长度而强行换会话的手写接力仪式，单人跨会话续跑先读 `research/handoff_resume.md`。**非停点阶段严禁生成 `handoff_questions.md`**。
5. **人工停点与提问形式**。受控词表签字是全流程唯一人工停点，仅在该停点按需将待确认事项逐段写入 `research/data/handoff_questions.md` 的「## 待人工拍板事项」，并在对话中用纯文本说明（非停点阶段严禁生成 `handoff_questions.md`）。每段写清「现在要你做什么」「为什么要你确认」「不做或直接通过会怎样」，末尾请用户直接用中文打字回复。禁止选项卡片与「请选择 A/B/C」式提问。若卡片已经弹出，用纯文本重述问题，并说明「忽略上方卡片，直接打字回复」。用户既点卡片又发文字时，以文字为准。

6. **两段闸门**。脚本闸门先核验输入并拦截错误。按顺序执行 `validate-vocab` 和 `render-signoff`，两者退出码均为 0 且告警完整、词表草稿、冻结名单与 Pass 1 记录的哈希齐备时才放行。人工闸门只拍业务裁决。
   - 脚本写入 `script_gate_receipt`，人工裁决写入 `signoff_note`。两项均保存在 `tag_vocab.json` 顶层。
   - 对话只传短裁决清单和下载指针。禁止手抄脚本数字充作裁决依据。签字页格式及纯文本兜底见 [Stage 2 第三节](references/stage2-tagging.md)。

## 三、全阶段硬红线

1. **量化唯一源与全球口径**：大盘、Top10、新品收入/下载、近似 RPD、近 3 月环比与增量全部来自 Sensor Tower MCP，并经 `research_tools.py market` 核算，正文与图表一律用 `[[metric]]` 绑定。统一采用 **ST 全球口径（`regions: []`，含中国大陆 iOS）**；纯国服产品在产品卡 `<details>` 展开层标注地区属性。第三方元数据接口只补发行日、英文商店描述、截图、买量辅证与新游线索，绝不混入 ST 数值计算。
2. **ST 接口纪律**。禁用必报类型错的 `sensortower-get-unified-app-details` 与 `sensortower-get-app-details`。主榜开启 `include_app_details=true` 直接取发行商与双端商店 ID。边缘产品的 Game IQ 标签核查使用 `sensortower-get-sub-app-details(app_id, country="US")`。新品初筛组合使用运行日滚动近一年的 `Release Date (WW)` 月枚举、`unified_app_primary_categories: ["6014"]` 与 `custom_fields_include_unified_apps: false`，再核实 ST 全球生效首发日。门槛日含当天。第三方新游榜只提供发现线索，排序池仅纳入经 ST 数据核验合格的候选。
3. **混变与正交维度纪律**：混变按 `Hybridcasual × Puzzle` 标签筛选并排除经典二合/三合/三消及传统字谜/脑筋急转弯/潜行解谜；仅对配置登记的单一标签缺口特例（`Match Factory!`）单列披露，**绝不放开整个 `Match Pair + Casual`**。混变与其余三类玩法品类（合成、三消、模拟经营）维度正交，**绝不与这三类相加求和大盘**；白名单特例与模拟经营/混变的下载增补位均**不计入对应品类 CR10 的主榜 Top10 净收入合计**。
4. **合成双子类合并与明细留痕**。收入、下载各用一次 ST 大盘查询，同时取 `Match Merge 2` 与 `Match Merge 3`。6 个完整月的两项指标均须有双子类月值。`data_pipeline.py` 按月合并为 `merge`，在 `subgenre_breakdown_audit.merge` 保留双子类月值与期间合计。收入榜用 `check-coverage` 核验合并榜的统一 ID 都能归入两个单子类榜的并集，要求 `unattributable_rows == 0`，并按 `0.5%` 容差核对第 20 名。
5. **读图与 Tag 门禁**。每款最多下载 5 张商店截图。按 1–5 顺序全扫全部已落盘商店截图，锁定受控判定单元并记录截图序号与观察区域。零实机图按 Stage 2 的 `L2` 规则处理。每款终稿锁定 **2–3 个受控 Tag**（1 主题材 + 1 呈现 + 0–1 融合），禁黑名单泛词，严禁只凭买量素材（`marketing_only`）定稿。呈现命名未经渲染技术实证不得含 2D/3D，定义禁含题材词。标为 `promo_only` 的截图不得作为呈现主证。融合轴允许按实证留空。

   主题材、呈现、融合按统一的集中度门槛核查。命中数至少 5 款，或该轴分母至少 10 款时，单个标签占比超 40% 须附逐产品不可再分说明，超 60% 且无人工豁免由脚本拦截。两项计数都低于门槛时，报小分母提示。

   受控词表签字是唯一人工停点。签字后进入闭集定标，再用独立 `remote` 子代理剥离历史结果与 Game IQ，按反向证伪提示词重测。禁止模型覆盖；无 `harness_model_record` 时严禁写模型名或宣称跨模型验证，统一使用固定披露话术：`独立上下文盲态重测（未指定跨模型覆盖，运行环境不暴露子代理模型标识，不等于跨模型交叉验证）`；宿主换版本或首次在本工作区运行时重验一次 `remote` 读图通道。`tag_qc.py` 核对三项门槛（主题材一致率 ≥ 80%、呈现一致率 ≥ 80%、主槽标签平均 Jaccard 重叠率 ≥ 75%），附录按 Stage 2 披露重测通道。主会话按 `adjudication_owner` 在运行内完成终裁。
6. **`pilot` 不出立项结论**。矩阵只报供给分布，机会卡只写判据、所需数据与启用阈值。影视按高频现状标签挂接，并标「现状对照·非立项推荐」（卡片灰标用「高频现状对照·非立项推荐」）。
7. **影视红线**：`full` 按机会卡分组选片，**欧美高知名度剧影优先**（仅当机会卡明确指向亚洲文化时补日韩作品）。两档均须遵守以下格式。每部必写 **2–3 段完整剧情介绍**（禁仅摘 IMDb 短梗概），**必须报 4 位整数年份 `release_year`**，中文名加《》，英文原名用 `<i>` 斜体。具体上映日期（月/日）、演员与目标受众仍禁止。
8. **成品与来源卡脱敏**：正文、卡片、附录与 `[[sources]]` 来源卡一律只写研究定义与客观来源名（`Sensor Tower`、应用商店公开元数据、第三方投放监测素材、公开维基与影评资料），**禁止出现服务器绝对路径（`/data-cfs-extra/...`）、MCP 前缀（`mcp__xiaoqi-...`）、内部平台简称、模型运行时名以及「运营月报 / 运营口径」字样**；`raw_file` 仅留相对报告目录的路径。
9. **分层终止与禁止手写长 HTML**：ST 大盘或目标品类主榜不可得时硬终止；单品评论、个别截图或买量素材缺失时标 `data-status="missing"` 并登记附录。缺口若涉及必选槽，按 Stage 4 的 `L1` 闸门在签字前拦截。所有数据、Tag、机会与影视均落结构化 JSON，由 `compile_report.py` 生成稿源并走 `research_tools.py html/validate` 输出双版 HTML，严禁手写长 HTML。
10. **两级主题材与叙事维**。每款使用 2–3 个正式标签，主题材写作「一级 › 二级」，一级取自 `primary_domains`，两级合计占一个主题材槽。叙事维必填 `narrative_intensity`（重剧情／轻剧情／无剧情），并按强度联动填写可选的 `narrative_motif`。两字段均不占主槽。

## 四、四阶段执行流与按需查阅

| 阶段 | 必读参考文档 | 核心动作与脚本 | 阶段产物与闸门 |
|---|---|---|---|
| **Stage 1 · 取数与核算** | [stage1-acquisition.md](references/stage1-acquisition.md)、[sensortower-rules.md](references/sensortower-rules.md)、[yilan-rules.md](references/yilan-rules.md) | 先跑 `python -B <Skill目录>/scripts/data_pipeline.py doctor --out-dir <报告目录> --write-probe` 与两条只读 MCP 探针，再跑 `init-run`；查 ST 大盘（含宏观与合成双子类）、主榜/下载榜/新品榜与单品 6 月序列；按双端 ID 补商店元数据、核心截图与买量素材；运行 `data_pipeline.py build` 与 `research_tools.py market/chart`；末步运行 `python -B <Skill目录>/scripts/data_pipeline.py checkpoint --out-dir <报告目录> --stage stage1_done` | `research/data/market_result.json`、`product_roster.json`、`tagging_input_bundle.json`、大盘/榜单 SVG 与 `stage1_done` 三件套（`run_state.json`、`handoff_resume.md`、`packet_manifest.json`） |
| **Stage 2 · 打标与盲检** | [stage2-tagging.md](references/stage2-tagging.md) | 跨会话续跑可按需复跑 `python -B <Skill目录>/scripts/data_pipeline.py doctor --out-dir <报告目录>`；Pass 1 读文并按 1–5 顺序全扫全部已落盘商店截图，锁定受控判定单元并记录截图序号与观察区域，产出 5–8 个自由词 → Pass 1.5 收敛词表草稿，先用 `validate-vocab` 核验，再用 `python -B <Skill目录>/scripts/tag_qc.py render-signoff` 输出决策前置的静态 HTML 与纯文本兜底用的 Markdown 摘要，两个命令均通过后运行 `checkpoint --stage stage2_draft_ready`，进入人工签字（唯一人工停点）→ Pass 2 闭集定标 → `remote` 子代理独立盲测 → `tag_qc.py` 核算，主会话在运行内终裁并运行 `python -B <Skill目录>/scripts/data_pipeline.py checkpoint --out-dir <报告目录> --stage stage2_verified` | `tag_vocab_signoff.html`、`research/data/tag_vocab_draft.md`、`tag_vocab.json`、`tags_final.json`、`qc_report.json`、`adjudications.json`、`tagged_corpus.json`（`tag_qc.py` 退出码 0）及 `stage2_draft_ready` / `stage2_verified` 三件套 |
| **Stage 3 · 矩阵、机会与影视** | [stage3-synthesis-films.md](references/stage3-synthesis-films.md) | 跨会话续跑可按需复跑 `python -B <Skill目录>/scripts/data_pipeline.py doctor --out-dir <报告目录>`；为「标签分布矩阵与立项方向」汇总 Tag 供给差热力矩阵与「主题材 × 呈现」二元组合，再接机会卡。`full` 写 3–5 张机会卡（必含合成适配条件、反例与代价），`pilot` 只展示供给分布和机会卡占位。影视默认关闭（`enable_film_refs: false`），此时跳过选片，`stage3_done` 跳过 `film_refs.json`。开启后，`full` 按机会卡配影视，`pilot` 按高频现状标签选两部欧美剧影。两档均深读 Wikipedia Plot 或长影评，每部写 2–3 段完整剧情；末步运行 `python -B <Skill目录>/scripts/data_pipeline.py checkpoint --out-dir <报告目录> --stage stage3_done` | `research/data/supply_matrix.json`、`opportunities.json`、`film_refs.json`（开启影视后）与 `stage3_done` 三件套 |
| **Stage 4 · 看板编译与交付** | [stage4-html-delivery.md](references/stage4-html-delivery.md) | 跨会话续跑可按需复跑 `python -B <Skill目录>/scripts/data_pipeline.py doctor --out-dir <报告目录>`；运行 `compile_report.py` 编译一屏概览、矩阵与品类明细及 `<details>` 折叠深读，按概览 → 市场盘面 → 标签分布矩阵与立项方向 → 品类明细 → 高增新品观察 → 影视参考 → 附录编排并做脱敏校验。影视默认关闭（`enable_film_refs: false`），开启后才展示影视卡片墙→ `research_tools.py html` → `research_tools.py validate` → `checkpoint --stage stage4_built` → `chromium-headless` 截图 + `Read` 逐张目检 → 上传知识库 `~` 并删临时 `upload.sh` → `python -B <Skill目录>/scripts/data_pipeline.py checkpoint --out-dir <报告目录> --stage delivered` | `<报告名>.html`、`<报告名>.base64.html`（< 3 MB）、`<报告名>_bundle.zip`、`research/delivery.json`、验收截图与 `stage4_built` / `delivered` 三件套 |

## 五、交付形态与验收出口

- **双包与打开说明**：同时交付单文件自包含 `<报告名>.base64.html`（推荐直接下载本地双击浏览）与含 `image/` 的 `<报告名>_bundle.zip`。知识库上传回执首部必须附固定说明：短链受门户鉴权限制，不提供在线静态网页托管，请下载 `.base64.html` 在本地浏览器打开。
- **完整交付边界**：仅当全部满足 ① `research_tools.py market` 与 `tag_qc.py` 退出码均为 0；② 词表经人工批复且盲态重测达标；③ `research_tools.py validate` 返回 `checked` 且脱敏扫描零命中；④ `chromium-headless` 实际截图经 `Read` 确认中文字体、SVG、卡片栅格、折叠层与窄屏/打印布局无异常时，才可声明**完整交付**（完整 10 项验收读数见 [stage4-html-delivery.md](references/stage4-html-delivery.md)）。
- **无截图硬降级**：若运行环境无法完成浏览器截图，严禁宣称完整交付，必须在交付首段声明「仅完成结构与数据绑定校验（`validate == checked`），未完成浏览器视觉目检，请在本地浏览器人工复核」。
