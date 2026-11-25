# Stage 1：取数、预聚合、富化与核算规范

本阶段目标：在 `<报告目录>` 内产出零缺月、口径洁净、双源对齐的 `market_result.json`、`product_roster.json`、大盘/榜单 SVG 与 `tagging_input_bundle.json`（含本地已下载的图标与商店核心截图）。

开工前务必先读本文件，并按需对照 [sensortower-rules.md](sensortower-rules.md) 与 [yilan-rules.md](yilan-rules.md)。

---

## 一、Step 1.0：初始化与探针（1 分钟）

1. **确定 `<报告目录>` 与统计窗**：
   - 未指定目录时，以 `configs/workspace.json` 中的 `default_report_root` 为父目录建立 `<报告目录名>/`（如 `202611_merge2_pilot/`）；严禁写入技能目录或 `readonly_paths_blacklist`。
   - 运行初始化命令（自动校验路径合法性、计算 6 个月主窗口、前后 3 个月对比窗口与动态 12 个月 `Release Date (WW)` 枚举数组，写入 `research/run_state.json`）：
     ```text
     python <Skill目录>/scripts/data_pipeline.py init-run \
       --out-dir <报告目录> \
       --preset <pilot|full> \
       --window-end <YYYY-MM>
     ```
2. **MCP 权限探针**：新会话首步执行 2 次最小只读调用——ST `sensortower-list-help-sections({})` 与元数据通道 `data-yilan-mcp-get-current-time({})`，确认权限有效后再取数。
3. **`full` 模式专属前置反查（`pilot` 跳过）**：
   - **混变益智子类枚举反查（1 次调用）**：按 `configs/subgenres.json` 中 `hybrid_puzzle.full_preflight_subgenre_discovery` 调 1 次 `sensortower-get-app-market-size(breakdown="game_sub_genre", custom_fields=[Hybridcasual, Puzzle])`，落盘 `research/raw/st_preflight_hybrid_puzzle_subgenres.json`。记录实际命中的 `Game Sub-genre` 列表，确认传统益智排除项（`Word` / `Trivia` / `Jigsaw` / `Hidden Objects` / `Riddle` / `Environmental`）在当前 ST 枚举中的准确拼写。2026-11-25 实测：ST 返回的是复数 `Hidden Objects`，`Riddle` 与 `Environmental` 各对应脑筋急转弯与潜行环境解谜；单数 `Hidden Object` 与 `Action Puzzle` 不是 ST 枚举，已从配置与本行删除。〔待核〕若确认 ST `exclude: true` 生效可直接下推过滤，否则一律保留在 `data_pipeline.py` 后置排除。
   - **边缘锚点复查（≤6 次调用）**：对 `configs/subgenres.json` 登记的模拟经营次级待核锚点（`Pizza Ready!`、`Burger Please!`、`My Mini Mart`、`My Supermarket Simulator 3D®`、`Idle Restaurant Tycoon`）及混变下载池边缘款（`Pocket Sort: Coin Merge Puzzle`），必要时用 `sensortower-get-sub-app-details(app_id=<ios_id>, country="US")` 核查其美区 iOS `genre / sub_genre / product_model`，记录留档。

---

## 二、取数顺序总览（严格区分「批量」与「逐个」）

原则：**先全量批量拿大盘、榜单与 6 个月序列，确定去重后的最终入围名单（`pilot` 约 13–15 款，`full` 约 65–80 款），再按品类分批做单品富化**，严禁对未入围产品提前做逐个富化。

| 步骤 | 动作与接口 | 批量 / 逐个 | `pilot` 调用数 | `full` 调用数 | 原始落盘文件 |
|---|---|---|---:|---:|---|
| 1.1 | ST `get-app-market-size`：宏观 `game_product_model`（收入+下载）+ 目标品类 `game_sub_genre`（收入+下载；**`pilot` 同步拉 `merge_2` 与 `merge_3`**） | **批量**（单次取全 6 个月） | 6 | 12 | `research/raw/st_market_*.json` |
| 1.2 | ST `get-top-apps`（`include_app_details=true`, `limit=20`）：每启用品类拉 6 个月 `revenue` 榜与 `downloads` 榜；`full` 混变另用 `search-entities` 补查单一特例 `Match Factory!` | **批量**（每榜 1 次） | 2 | 11 | `research/raw/st_top_*.json` |
| 1.3 | ST `get-top-apps` 新品初筛（近 3 个完整月 `revenue` 与 `downloads` 各取前 20，叠 `Release Date (WW)` 近 12 月枚举 + `6014` + `custom_fields_include_unified_apps: false`）+ 第三方 `competitor_new_app_rankings` 补软启动漏网 | **批量**（每品类 3 次） | 3 | 15 | `research/raw/st_new_*.json`、`yl_new_*.json` |
| 1.4 | 按统一 ID 合并主榜入围产品与全部待核新品候选，调 ST `get-app-performance-metrics`（单次 ≤100 款）拉 6 个月 `revenue` 与 `downloads` 序列；对仅见于第三方新游榜的产品先调 `search-entities` 补统一 ID | **批量**（单次 ≤100 款） | 2–3 | 2–4 | `research/raw/st_perf_*.json` |
| 1.5 | 单品富化：① ST `get-app-reviews-text`（每款 1 页 30 条）；② `app_info`；③ `app_detail_desc`；④ 每品类批量 `competitor_category_rankings` + `material_appgrowing` | ①②③ **逐个**；④ **批量** | **~45**（主会话直跑） | **~220–250**（**拆 5 个 `remote` 子代理，每代理 ≤45–50 次**） | `research/raw/st_rev_*.json`、`yl_app_*.json`、`yl_materials_*.json` |
| 1.6 | 本地跑 `data_pipeline.py build` → `research_tools.py market` → `research_tools.py chart` | 本地纯标准库脚本 | 3–5 条命令 | 7–10 条命令 | `research/data/*`、`research/charts/*`、`research/media/*` |

---

## 三、核心步骤执行细则

### 3.1 大盘与 `merge_combined` 预聚合（Step 1.1）
- **宏观参照**：拉取 `breakdown: "game_product_model"`, `date_granularity: "month"`, `os: "unified"`, `regions: []` 的近 6 个完整自然月 `revenue` 与 `downloads`，提取 `Casual`、`Hybridcasual`、`Hypercasual` 三条月度曲线。
- **品类大盘**：
  - `merge_2`（`Match Merge 2`）、`merge_3`（`Match Merge 3`）、`match_3`（`Match Swap`）：各按 `configs/subgenres.json` 的 `st_filter.custom_fields` 拉 6 个月 `revenue` 与 `downloads`。**注意：`pilot` 模式虽只深挖二合产品，仍必须顺手拉取 `merge_3` 的 6 个月大盘**，用于生成看板的「合成合计（二合 + 三合）」对照行。
  - `simulation`：传 3 条 `custom_fields`（`Game Genre = Simulation` AND `Game Sub-genre ∈ [Tycoon / Crafting, Time Management, Idler, Simulator]` AND `Game Product Model ∈ [Casual, Hybridcasual]`），按月拉 6 个月 `revenue` 与 `downloads`，由 `data_pipeline.py` 将 4 子类同月求和为 `simulation` 大盘。
  - `hybrid_puzzle`：传 `Game Product Model = Hybridcasual` AND `Game Genre = Puzzle` 拉 6 个月 `revenue` 与 `downloads`（如已核实排除子类，扣除经典二合/三合/三消与传统益智子类对应的月度行）。
- **`merge_combined` 预聚合机制**：
  - 基座 `report_values.py` 的 `sum` 要求所有输入记录拥有同一 `entity_id`，不能跨品类直接相加。
  - `data_pipeline.py build` 读取 `st_market_merge_2_*.json` 与 `st_market_merge_3_*.json`，对每个完整自然月用 `Decimal` 精确相加，产出独立品类记录 `entity_id: "merge_combined"`，完整附带两侧 `evidence_ids`（`["st_mkt_merge2_rev", "st_mkt_merge3_rev"]` 等）与 `scope_limitations: ["由 Sensor Tower Match Merge 2 与 Match Merge 3 同口径月度估算值相加得出，非 ST 原生单一分类"]`，再送入 `research_tools.py market` 自动算出 6 月合计、近 3 月环比、近似 RPD 与趋势。

### 3.2 Top10、下载增补位、白名单特例与边缘核查（Step 1.2）
1. **收入主榜（全品类必做）**：每启用品类拉 `metric: "revenue", limit: 20, include_app_details: true`。
2. **下载榜与下载增补位**：每启用品类拉 `metric: "downloads", limit: 20, include_app_details: true`。
   - 对 `configs/subgenres.json` 中 `download_supplement.enabled == true` 的品类（`full` 模式下的 **`simulation`** 与 **`hybrid_puzzle`**），从下载 Top20 中剔除已入围本品类收入 Top10、命中 `excluded_sub_genres` 或 `excluded_apps` 的项后，取前 **5 款**入池，标记 `role: "download_supplement"` 与 `include_in_auto_cr10: false`。
   - 对未开启下载增补的品类（`merge_2`、`merge_3`、`match_3`），下载榜仅作辅助参照，不额外扩编打标池。
3. **单一标签缺口白名单特例（仅 `full` 模式 `hybrid_puzzle`）**：
   - 仅按 `configs/subgenres.json` 的 `hybrid_puzzle.whitelist_exceptions` 纳入 `Match Factory!`（已核实美区 iOS `6449094229`，ST 标为 `Puzzle / Match Pair / Casual`），用 `sensortower-search-entities` 取得 `unified_app_id` 后合入 `hybrid_puzzle` 展示清单；标记 `role: "whitelist_exception"` 与 `include_in_auto_cr10: false`，**绝不放开整个 `Match Pair + Casual` 过滤池**。
4. **跨列去重与边缘 `get-sub-app-details` 核查**：
   - 同一 `unified_app_id` 跨品类命中时，严格按 `cross_category_priority`（`merge_2 (1) > merge_3 (2) > match_3 (3) > simulation (4) > hybrid_puzzle (5)`）决定主归属列，防止混变形态二合/三消被双计进 `hybrid_puzzle`。
   - 对 `simulation` 命中 `Simulator` 子类但名称疑似非商业拟真（载具/物理/恶作剧）的产品，以及 `hybrid_puzzle` 榜中名称含 `Merge` / `Word` / `Trivia` / `Sneak` 的边缘产品，调用 `sensortower-get-sub-app-details(app_id=<ios_id>, country="US")` 查验 `game_intel_data`；确认不符口径者移出主池并记录到 `product_roster.json` 的 `excluded_audit` 供附录披露。

### 3.3 新品初筛与真新品核验（Step 1.3）
1. **ST 前置初筛**：用 `run_state.json` 生成的动态 12 月 `Release Date (WW)` 数组（格式 `YYYY/MM`），叠加 `unified_app_primary_categories: ["6014"]`、`custom_fields_include_unified_apps: false` 及本品类过滤条件，在近 3 个完整自然月窗口内分别拉 `metric: "revenue"` 与 `metric: "downloads"` Top20（`include_app_details: true`）。
2. **软启动补漏**：同品类调用第三方 `competitor_new_app_rankings(play=<external_play_ids>, start_date=<近3月起>, end_date=<近3月止>, min_downloads=50000)`，把尚未触发 ST 全球首发日的高潜新游并入候选。
3. **真新品二次核验与定选（每品类固定五席）**
   - 取最近 3 个完整自然月收入榜与下载榜各前 20，将两榜与软启动补漏候选合并，按 `unified_app_id` 去重。按 Step 1.4 为完整候选并集批量拉取 6 个完整自然月的收入与下载月序列，再定选。
   - ST 全球日序列是新品定选的必需输入。将 `get-app-performance-metrics` 的日粒度回执存为 `research/raw/st_daily_perf_*.json`。在 `research/data/stage1_manifest.json` 的 `st_daily_performance_metrics.revenue` 与 `st_daily_performance_metrics.downloads` 分别列出实际来源文件名。原始行以 `unified_app_id` 和 `date` 对齐，数值字段为 `revenue`（或 `revenue_usd`、`revenueInUsdAbsolute`）与 `downloads`（或 `downloadsAbsolute`）。缺日序列时在 `research/data/gap_register.json` 登记 `L1` 缺口，按 Stage 4 闸门在签字前补齐。离线直读时，可用并集审计的 `first_observed_date_st` 复跑已有候选。
   - 生效首发日先取同一 `unified_app_id` 在 ST 全球收入或下载日序列中的最早正值日，取不到再读并集审计的 `first_observed_date_st`。两处均无可用日期时，以 `excluded_launch_unverified` 记入 `excluded_audit`，运行日志列出统一 ID 与原因。早于近 12 个月门槛 `cutoff_date` 的产品移出新品池并记入 `excluded_audit`，保留主榜资格。老游戏换皮重发、主机或 PC 老 IP 移植、老品买量翻红也记入剔除审计。仅处于单区软启动且通过核验的新品标 `release_stage: "soft_launch"`。
   - 以生效首发日所在月为界，上市前空月补 `0.00`，上市当月及以后缺月记为缺失。上市后序列不全的候选不入排序。
   - 净收入差额为**最近 3 个完整月的 ST 净收入合计减去此前 3 个完整月的合计**。按净收入差额降序，同额按同期下载增量降序，再按统一 ID 升序。下载增量使用同一对三个月窗口。
   - `merge_2`、`merge_3`、`match_3` 取收入排序前五席。`simulation` 与 `hybrid_puzzle` 先取收入增量前三席，再从未入选产品按下载增量降序取两席。下载增量相同时按净收入差额降序，再按统一 ID 升序。合格候选不足五席时登记池子缺口，按 Stage 4 的 `L1` 闸门处理。
   - 第 5 席与第 6 名的净收入差额相对差按 `(第 5 席净收入差额 − 第 6 名净收入差额) / 第 5 席净收入差额` 计算。低于 `15%` 时，第 5 席输出 `is_boundary_seat: true`，新品审计表披露第 6 名两段三个月净收入合计、净收入差额及同期下载增量的实数。第 6 名缺席或第 5 席净收入差额为零时，审计表注明相对差无法计算。正文不得用单一边界席推导题材趋势。

### 3.4 批量 6 个月序列与五品类分轮富化上限（Step 1.4 – 1.5）
1. **批量拉序列（Step 1.4）**：将 Top10、下载增补、白名单特例与全部新品候选按 `unified_app_id` 全局去重，分批（每批 ≤100 个）调 `sensortower-get-app-performance-metrics` 拉取 6 个完整自然月的 `revenue` 与 `downloads`，再按 3.3 冻结名单。冻结池内产品的六个月净收入合计记为 `frozen_roster_6m_revenue_usd`，仅用于池内 Tag 占比。二合 CR10 的分母恒为同期二合全大盘净收入。
2. **单品富化（Step 1.5）**：对去重后的每款产品采集三类材料：
   - **ST 评论**：调 `get-app-reviews-text` 查近 60–90 天美区 iOS 英文评论（`limit=30`）；无美区 iOS 时改查 Android `languages: ["en"]`。
   - **商店元数据与截图**：按 ST `sub_apps` 构造 `app_key` 调 `app_info`（必验 `APP_ID != null`，同时识别 `publisher_area` 是否纯中国大陆发行以置 `cn_ios_only` 标记）与 `app_detail_desc`（取英文完整描述与最多 5 张 `screenshot_urls`）。
   - **买量辅证与图标索引（按品类批量）**：每品类调 1 次 `competitor_category_rankings(dim="m_app_id")` 对齐 `m_app_id` 与图标，再批量调 1–2 次 `material_appgrowing` 取高曝光前 3–5 条英文 slogan。
3. **五品类调用量硬上限与子代理分片**：
   - **单会话/单子代理的逐个富化调用硬上限为 50 次**（对应约 13–17 款产品 × 3 次逐个调用 + 2 次批量调用）。
   - `pilot`（二合 13–15 款，约 45 次）：在主会话 Round 1 内一次完成。
   - `full`（五品类 ~65–80 款，约 220–250 次）：严禁在主会话串行通跑！主会话完成 Step 1.0–1.4 后，按品类拉起最多 **5 个 `isolation: "remote"` 子代理**（每个负责 1 个品类，约 40–50 次调用；严禁 `worktree`），各子代理首步跑只读 MCP 探针并将原始响应直接写入共享的 `<报告目录>/research/raw/`。

---

## 四、Step 1.6：管道构建、核算与出图命令

1. 主会话核对 `<报告目录>/research/raw/` 齐全（有 `truncated: true` 的第三方响应先跑 `data_pipeline.py fetch-oss`），生成原始文件索引 `<报告目录>/research/data/stage1_manifest.json`。
2. 依次运行三条命令（全部退出码须为 `0`）：
   ```text
   python <Skill目录>/scripts/data_pipeline.py build \
     --out-dir <报告目录> \
     --manifest <报告目录>/research/data/stage1_manifest.json

   python <Skill目录>/scripts/research_tools.py market \
     --input <报告目录>/research/data/market_input.json \
     --output <报告目录>/research/data/market_result.json

   python <Skill目录>/scripts/research_tools.py chart \
     --input <报告目录>/research/charts/macro_dual_axis_spec.json \
     --output <报告目录>/research/charts/macro_dual_axis.svg \
     --metadata <报告目录>/research/charts/macro_dual_axis.json
   ```
   （对其余品类 `*_rank_spec.json` 与 `merge_2_dual_axis_spec.json` 依次运行 `research_tools.py chart`。）
3. `data_pipeline.py build` 自动完成：
   - 下载每款产品 1 张图标至 `research/media/icons/`（供内嵌 HTML）与最多 5 张核心商店截图至 `research/media/screenshots/`（供 Stage 2 读图，**不内嵌进 HTML**）；
   - 生成 `product_roster.json`、`market_input.json`（含 `merge_combined`、前后 3 月 `sum`、`percent_change`、`contribution`、`ratio` 近似 RPD、`concentration` CR10）与 `tagging_input_bundle.json`；
   - 更新 `research/run_state.json` 为 `stage1_done`。

---

## 五、Stage 1 出口与轮次衔接

- **`pilot` 模式（2 轮规划）**：确认 `market_result.json` 0 缺月、0 冲突且 `tagging_input_bundle.json` 已就绪后，**不在本处停顿**，同会话直接进入 Stage 2 Pass 1 与 Pass 1.5，停在人工签字词表处结束 Round 1。
- **`full` 模式（3 轮规划）**：确认五品类大盘 0 缺月、边缘锚点无漏网、图片全部落盘后结束 Round 1，向用户回报五品类取数摘要并输出 Round 2 接力指令（附 `<报告目录>` 绝对路径、指定仅读 `references/stage2-tagging.md`）。
