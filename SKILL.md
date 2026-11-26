---
name: casual-tag-market-research
description: "生成或修订休闲游戏四品类（合成、三消、混变益智、模拟经营）看板式市场图景与可追溯产品 Tag 供给差研究：用 Sensor Tower MCP 核算近 6 个月全球大盘、Top10 与近 12 月首发新品，补齐商店描述、核心截图与投放辅证，经「自由词→人工签字受控词表→闭集定标→独立上下文盲态重测」为每款产品打 2–3 个题材/呈现/融合 Tag，试点输出合成供给分布、机会卡占位与两部影视现状对照，全量输出跨品类供给差热力矩阵、立项机会卡与按机会卡挂钩的影视长剧情借鉴，交付双版离线看板 HTML。关键词：休闲品类 Tag、题材与呈现 Tag、供给差矩阵、合成立项题材、混变益智题材、多品类 Top10 打标、影视剧情借鉴。不用于单品类纯玩法机制竞品深挖、付费循环拆解或 YouTube 社区反馈报告（用 game-market-competitor-report）；不用于围绕指定影视/综艺/IP 池做 1–5 星改编适配评级与图文双栏逐作分析（用 topic-adaptation-research）；不用于文化/时代背景专题研究（用 cultural-era-background-report）。"
---

# 休闲品类 Tag 市场研究与立项看板
当前版本：v0.1.4

服务休闲游戏（优先合成）立项选题：把「宏观与细分盘面 → Top10 与新品产品卡 → 可追溯受控 Tag → 跨品类供给差与机会卡 → 影视长剧情借鉴」装配成一屏概览、品类分章、折叠深读的离线 HTML 看板。

## 一、预设模式与配置文件

**开工直报默认值**。首段用实际日期和人话报清本次口径。默认跑合成试点，市场看最近 6 个完整自然月，新品看从一年前同日到运行当日的滚动窗口，起止日都算。低置信与盲测分歧由主会话按 `adjudication_owner` 在本次运行内终裁。用户未提出异议即开跑。若用户指定档位或日期，首段报实际采用的口径。

品类展示顺序固定为**合成、三消、混变益智、模拟经营**。合成（`merge`）等于 ST `Match Merge 2 + Match Merge 3`。

开机先读取三份配置文件（不要凭记忆改写枚举、门槛或路径）：

- 路径、读图与脱敏边界：[configs/workspace.json](configs/workspace.json)
- 四品类 ST 标签过滤、排除项、下载增补开关与 `pilot`/`full` 预设：[configs/subgenres.json](configs/subgenres.json)
- 三轴槽位、叙事强度与母题、禁泛词黑名单、词表规模与盲态抽检门槛：[configs/tag_blacklist.json](configs/tag_blacklist.json)

| 预设 | 覆盖范围 | 受控词表 / 抽检 | 机会卡 / 影视 | HTML 脚本模式 | 计划轮次 |
|---|---|---|---|---|---|
| `pilot`（默认首跑） | 合成（`merge`）单品类 | 12–20 词 / 抽 5 款 | 机会卡占位、影视两部现状对照 | **严格零 `<script>`**（纯 CSS Tab + `<details>`） | 2 轮（1 次人工签字） |
| `full` | 合成、三消、混变益智、模拟经营四品类全量 | 25–40 词 / 抽 12 款（每品类 3 款） | 完整机会卡与六到八部影视 | 允许白名单 ≤150 行无外链内联脚本（禁脚本仍可读） | 3 轮（含 1 次人工签字） |

## 二、目录黑板与分轮接力

1. **目录位置**：报告目录建在 Skill 之外，默认根路径见 `configs/workspace.json`（`/data-cfs-extra/project_data/ps-2078-4397/reports/<报告目录名>/`），所有脚本通过 `--out-dir` 接收绝对路径，**不向技能目录或 `readonly_paths_blacklist`（如 `/data-cfs-extra/project_data/用研市场调研助手`）写入任何文件**。
2. **按需加载参考文档**：每次开工或续跑先读 `<报告目录>/research/run_state.json`，**只加载当前阶段对应的 1–2 份 `references/`**，不要整目录读入。
3. **子代理规则**：本工作区默认单模型通跑；并发富化或盲态重测拉起子代理时**只用 `isolation: "remote"`，严禁 `worktree`**。新会话或子代理首步先做一次最小只读 MCP 探针，确认权限可用再取数。
4. **轮末接力指令**。每阶段交接时同步落盘 `research/run_state.json` 与 `research/data/packet_manifest.json`。每轮落盘并校验后，输出一条可粘贴的接力指令，写明报告的完整绝对路径、当前状态与下轮唯一必读的 `references/` 文件。
5. **人工停点与提问形式**。受控词表签字是全流程唯一人工停点。需要人判断的事项逐段写入 `research/data/handoff_questions.md` 的「## 待人工拍板事项」，并在对话中用纯文本说明。每段写清「现在要你做什么」「为什么要你确认」「不做或直接通过会怎样」，末尾请用户直接用中文打字回复。禁止选项卡片与「请选择 A/B/C」式提问。若卡片已经弹出，用纯文本重述问题，并说明「忽略上方卡片，直接打字回复」。用户既点卡片又发文字时，以文字为准。

## 三、全阶段硬红线

1. **量化唯一源与全球口径**：大盘、Top10、新品收入/下载、近似 RPD、近 3 月环比与增量全部来自 Sensor Tower MCP，并经 `research_tools.py market` 核算，正文与图表一律用 `[[metric]]` 绑定。统一采用 **ST 全球口径（`regions: []`，含中国大陆 iOS）**；纯国服产品在产品卡 `<details>` 展开层标注地区属性。第三方元数据接口只补发行日、英文商店描述、截图、买量辅证与新游线索，绝不混入 ST 数值计算。
2. **ST 接口纪律**。禁用必报类型错的 `sensortower-get-unified-app-details` 与 `sensortower-get-app-details`。主榜开启 `include_app_details=true` 直接取发行商与双端商店 ID。边缘产品的 Game IQ 标签核查使用 `sensortower-get-sub-app-details(app_id, country="US")`。新品初筛组合使用运行日滚动近一年的 `Release Date (WW)` 月枚举、`unified_app_primary_categories: ["6014"]` 与 `custom_fields_include_unified_apps: false`，再核实 ST 全球生效首发日。门槛日含当天。第三方新游榜只提供发现线索，排序池仅纳入经 ST 数据核验合格的候选。
3. **混变与正交维度纪律**：混变按 `Hybridcasual × Puzzle` 标签筛选并排除经典二合/三合/三消及传统字谜/脑筋急转弯/潜行解谜；仅对配置登记的单一标签缺口特例（`Match Factory!`）单列披露，**绝不放开整个 `Match Pair + Casual`**。混变与其余三类玩法品类（合成、三消、模拟经营）维度正交，**绝不与这三类相加求和大盘**；白名单特例与模拟经营/混变的下载增补位均**不计入对应品类 CR10 的主榜 Top10 净收入合计**。
4. **合成双子类合并与明细留痕**。收入、下载各用一次 ST 大盘查询，同时取 `Match Merge 2` 与 `Match Merge 3`。6 个完整月的两项指标均须有双子类月值。`data_pipeline.py` 按月合并为 `merge`，在 `subgenre_breakdown_audit.merge` 保留双子类月值与期间合计。收入榜用 `check-coverage` 核验合并榜的统一 ID 都能归入两个单子类榜的并集，要求 `unattributable_rows == 0`，并按 `0.5%` 容差核对第 20 名。
5. **读图与 Tag 门禁**。每款最多下载 5 张商店截图。按 1–5 顺序全扫全部已落盘商店截图，锁定受控判定单元并记录截图序号与观察区域。零实机图按 Stage 2 的 `L2` 规则处理。每款终稿锁定 **2–3 个受控 Tag**（1 主题材 + 1 呈现 + 0–1 融合），禁黑名单泛词，严禁只凭买量素材（`marketing_only`）定稿。呈现命名未经渲染技术实证不得含 2D/3D，定义禁含题材词。呈现单桶超 40% 须附逐产品不可再分说明，超 60% 且无人工豁免由脚本拦截。融合轴允许按实证留空。

   受控词表签字是唯一人工停点。签字后进入闭集定标，再用独立 `remote` 子代理剥离历史结果与 Game IQ，按反向证伪提示词重测。盲测子代理继承主会话模型，禁止模型覆盖。无可核验的 harness 运行记录，不写模型名。宿主换版本后重测模型继承与读图通道。`tag_qc.py` 核对三项门槛（主题材一致率 ≥ 80%、呈现一致率 ≥ 80%、主槽标签平均 Jaccard 重叠率 ≥ 75%），附录按 Stage 2 披露重测通道。主会话按 `adjudication_owner` 在运行内完成终裁。
6. **`pilot` 不出立项结论**。矩阵只报供给分布，机会卡只写判据、所需数据与启用阈值。影视按高频现状标签挂接，并标「现状对照·非立项推荐」（卡片灰标用「高频现状对照·非立项推荐」）。
7. **影视红线**：`full` 按机会卡分组选片，**欧美高知名度剧影优先**（仅当机会卡明确指向亚洲文化时补日韩作品）。两档均须遵守以下格式。每部必写 **2–3 段完整剧情介绍**（禁仅摘 IMDb 短梗概），**必须报 4 位整数年份 `release_year`**，中文名加《》，英文原名用 `<i>` 斜体。具体上映日期（月/日）、演员与目标受众仍禁止。
8. **成品与来源卡脱敏**：正文、卡片、附录与 `[[sources]]` 来源卡一律只写研究定义与客观来源名（`Sensor Tower`、应用商店公开元数据、第三方投放监测素材、公开维基与影评资料），**禁止出现服务器绝对路径（`/data-cfs-extra/...`）、MCP 前缀（`mcp__xiaoqi-...`）、内部平台简称、模型运行时名以及「运营月报 / 运营口径」字样**；`raw_file` 仅留相对报告目录的路径。
9. **分层终止与禁止手写长 HTML**：ST 大盘或目标品类主榜不可得时硬终止；单品评论、个别截图或买量素材缺失时标 `data-status="missing"` 并登记附录。缺口若涉及必选槽，按 Stage 4 的 `L1` 闸门在签字前拦截。所有数据、Tag、机会与影视均落结构化 JSON，由 `compile_report.py` 生成稿源并走 `research_tools.py html/validate` 输出双版 HTML，严禁手写长 HTML。
10. **两级主题材与叙事维**。每款使用 2–3 个正式标签，主题材写作「一级 › 二级」，一级取自 `primary_domains`，两级合计占一个主题材槽。叙事维必填 `narrative_intensity`（重剧情／轻剧情／无剧情），并按强度联动填写可选的 `narrative_motif`。两字段均不占主槽。

## 四、四阶段执行流与按需查阅

| 阶段 | 必读参考文档 | 核心动作与脚本 | 阶段产物与闸门 |
|---|---|---|---|
| **Stage 1 · 取数与核算** | [stage1-acquisition.md](references/stage1-acquisition.md)、[sensortower-rules.md](references/sensortower-rules.md)、[yilan-rules.md](references/yilan-rules.md) | 查 ST 大盘（含宏观与合成双子类）、主榜/下载榜/新品榜与单品 6 月序列；按双端 ID 补商店元数据、核心截图与买量素材；运行 `data_pipeline.py` 与 `research_tools.py market/chart` | `research/data/market_result.json`、`product_roster.json`、`tagging_input_bundle.json`、大盘/榜单 SVG；`full` 模式在此确认四品类无缺月后进下轮 |
| **Stage 2 · 打标与盲检** | [stage2-tagging.md](references/stage2-tagging.md) | Pass 1 读文，按 1–5 顺序全扫全部已落盘商店截图，锁定受控判定单元并记录截图序号与观察区域，产出 5–8 个自由词 → Pass 1.5 收敛词表草稿并等待人工签字（唯一人工停点）→ Pass 2 闭集定标 → `remote` 子代理独立盲测 → `tag_qc.py` 核算，主会话在运行内终裁 | `research/data/tag_vocab_draft.md`、`tag_vocab.json`、`tags_final.json`、`qc_report.json`、`adjudications.json`、`tagged_corpus.json`（`tag_qc.py` 退出码 0） |
| **Stage 3 · 矩阵、机会与影视** | [stage3-synthesis-films.md](references/stage3-synthesis-films.md) | 汇总 Tag 供给差热力矩阵与「主题材 × 呈现」二元组合，`full` 写 3–5 张机会卡（必含合成适配条件、反例与代价），按机会卡配影视。`pilot` 下机会卡改为占位，矩阵不出方向信号，按高频现状标签选两部欧美剧影。两档均深读 Wikipedia Plot 或长影评，每部写 2–3 段完整剧情 | `research/data/supply_matrix.json`、`opportunities.json`、`film_refs.json` |
| **Stage 4 · 看板编译与交付** | [stage4-html-delivery.md](references/stage4-html-delivery.md) | 运行 `compile_report.py` 编译看板模板（一屏概览 + 品类分章 + `<details>` 折叠深读 + 脱敏校验）→ `research_tools.py html` → `research_tools.py validate` → `chromium-headless` 截图 + `Read` 逐张目检 → 上传知识库 `~` 并删临时 `upload.sh` | `<报告名>.html`、`<报告名>.base64.html`（< 3 MB）、`<报告名>_bundle.zip`、`research/delivery.json` 与验收截图 |

## 五、交付形态与验收出口

- **双包与打开说明**：同时交付单文件自包含 `<报告名>.base64.html`（推荐直接下载本地双击浏览）与含 `image/` 的 `<报告名>_bundle.zip`。知识库上传回执首部必须附固定说明：短链受门户鉴权限制，不提供在线静态网页托管，请下载 `.base64.html` 在本地浏览器打开。
- **完整交付边界**：仅当全部满足 ① `research_tools.py market` 与 `tag_qc.py` 退出码均为 0；② 词表经人工批复且盲态重测达标；③ `research_tools.py validate` 返回 `checked` 且脱敏扫描零命中；④ `chromium-headless` 实际截图经 `Read` 确认中文字体、SVG、卡片栅格、折叠层与窄屏/打印布局无异常时，才可声明**完整交付**（完整 10 项验收读数见 [stage4-html-delivery.md](references/stage4-html-delivery.md)）。
- **无截图硬降级**：若运行环境无法完成浏览器截图，严禁宣称完整交付，必须在交付首段声明「仅完成结构与数据绑定校验（`validate == checked`），未完成浏览器视觉目检，请在本地浏览器人工复核」。
