# Sensor Tower 取数与口径规则

本文定义 Sensor Tower（简称 ST）在本技能中的唯一量化职责、工具白名单、禁用接口、参数契约与分层终止规则。实际调用时，按当前工作区挂载的 ST MCP 工具前缀解析下列工具后缀名，**绝不把内部 MCP 前缀写进成品报告或来源卡**。

---

## 一、角色定位与统计口径

1. **唯一量化主源**：市场大盘、五个研究品类、Top10 主榜、下载增补位、新品序列、近 3 月环比、近 3 月绝对增量、近似 RPD 与 CR10，全部来自 ST MCP，并经 `research_tools.py market` 核算后通过 `[[metric]]` 绑定。
2. **全球统一口径**：所有大盘、榜单与单品序列默认传 `os: "unified"`、`regions: []`（全球 iOS + Google Play 合并估算，**包含中国大陆 iOS**，不含中国大陆第三方安卓商店与小游戏）。对纯中国大陆 iOS 集中的产品，不做地区倒减，在产品卡 `<details>` 展开层附注地区属性。
3. **收入与下载定义**：
   - `revenue` / `revenue_usd` / `revenueInUsdAbsolute`：单位 USD，保留 2 位小数，口径为 **IAP 净收入估算（`revenue_basis: "iap_net"`，已扣除平台渠道分成，不含广告变现 IAA 与站外网页支付）**。
   - `downloads` / `downloadsAbsolute`：单位次（`download_basis: "provider_reported"`）。
   - 严禁凭经验比例把净收入乘系数换算为毛流水；所有 ST 数值均标注「Sensor Tower 估算」。
4. **完整自然月约束**：`research_tools.py market` 的 `window` 必须由完整自然月构成（默认近 6 个完整自然月，例如采集日在 2026-11 则取 `2026-05-01` 至 `2026-10-31`）。未满月的当月不纳入 6 个月主窗口。

---

## 二、可用工具白名单与参数契约

| 工具后缀 | 核心用途 | 关键必填与推荐参数 | 已知边界与注意事项 |
|---|---|---|---|
| `sensortower-list-help-sections` | 新会话/子代理权限探针 | `{}` | 最小只读探针；正常返回帮助分区列表即确认 ST 权限可用 |
| `sensortower-get-app-market-size` | 宏观大盘与品类 6 个月月度序列 | `breakdown`（`game_product_model` / `game_sub_genre`）、`metric`（`revenue` / `downloads`）、`start_date`、`end_date`、`date_granularity: "month"`、`os: "unified"`、`regions: []`、`custom_fields` | 设了 `date_granularity` 时 `limit` 参数会被接口忽略；单次调用只取一个 `metric` |
| `sensortower-get-top-apps` | 品类收入主榜、下载增补榜、新品初筛榜 | `metric`（`revenue` / `downloads`）、`os: "unified"`、`start_date`、`end_date`、`limit`（1–1000，常规用 20）、`include_app_details: true`、`custom_fields` | 开启 `include_app_details: true` **不触发 7012 报错**，直接返回 `unified_publisher_name`、`ios_app_ids`、`android_app_ids`；`total_count` 为分页前命中数，不等同于商店在架总数 |
| `sensortower-get-app-performance-metrics` | 全部入围产品的 6 个月月度收入与下载序列 | `unified_app_ids`（每次 ≤100 个 24 位 hex）、`metric`（`revenue` 或 `downloads`）、`start_date`、`end_date`、`os: "unified"`、`date_granularity: "month"` | 勿与 `app_ids` 同时传入（同时传时 `app_ids` 会覆盖 `unified_app_ids`）；产品上线前月份返回空数组属于有效零值，不判定为缺数 |
| `sensortower-get-sub-app-details` | 边缘产品的 Game IQ 标签精准核查 | `app_id`（美区 iOS 数字 ID）、`country: "US"` | 唯一通过实测的应用详情替代接口；直接读 `game_intel_data.genre.name`、`sub_genre.name`、`product_model.name`；仅反映查询时美国 iOS 主包标签，勿与全地区或历史标签混谈 |
| `sensortower-search-entities` | 单品特例与外部新游线索反查统一 ID | `entity_type: "app"`、`os: "unified"`、`countries: []`、`limit: 2–5`、`terms: [...]` | 因主榜已直开 `include_app_details: true`，本工具**仅用于白名单特例（如 `Match Factory!`）和第三方新游补漏产品**，不再对主榜逐批调用 |
| `sensortower-get-app-reviews-text` | 打标用玩家评论原文样本 | iOS：`os: "ios", app_id: "<ios_id>", regions: ["US"], languages: []`；Android：`os: "android", app_id: "<pkg>", regions: [], languages: ["en"]`；`start_date`、`end_date`、`limit: 30`、`page: 1`、`sort_by: "date"`、`sort_order: "desc"` | **绝不在同一次调用里混用 iOS `regions` 与 Android `languages`**；单页上限 200；查询超过约 93 天前的旧评论可能触发 HTTP 403，优先查近 60–90 天 |
| `sensortower-list-custom-fields` | 按需核查枚举字段名 | `{"os": "all"}` | 返回约 2,365 个字段（~27 万字符），触发落盘属正常现象，不是失败；仅在 `configs/subgenres.json` 需验证新枚举时调用 |

---

## 三、明令禁用的失效接口

以下两个接口在当前工作区存在确定性 schema 缺陷（上游将商店类目编号如 `7012` / `7003` 作为整数返回，而校验 schema 要求 `string | null`），调用必败，**全流程严禁调用**：

1. `sensortower-get-unified-app-details`
2. `sensortower-get-app-details`（同时已标 deprecated）

绕过策略：
- 发行商与双端商店 ID → 全部通过 `sensortower-get-top-apps(include_app_details=true)` 或 `sensortower-search-entities` 取得；
- 边缘产品的 Game IQ 分类 → 通过 `sensortower-get-sub-app-details(app_id=<ios_id>, country="US")` 取得；
- 商店描述、副标题、图标与截图 → 通过 `references/yilan-rules.md` 规定的应用商店元数据通道补齐。

---

## 四、关键过滤与核算纪律

### 4.1 多字段 AND 与正交维度
- `custom_fields` 传入多个对象时按逻辑 **AND** 生效（例如 `Game Product Model = Hybridcasual` AND `Game Genre = Puzzle`，或 `Simulation` 下的 4 子类白名单 AND `Casual/Hybridcasual`）。
- 〔待核〕`custom_fields` 中 `exclude: true` 在当前工作区尚未单独实测；涉及需要排除传统益智子类时，先在 `full` 模式首轮跑 1 次只读 `game_sub_genre` 分解，再确认是否走 `exclude: true`，未确认前一律由 `data_pipeline.py` 做后置精确过滤。
- **正交维度禁加总**：`Game Product Model = Hybridcasual` 与玩法子类（`Match Merge 2/3`、`Match Swap`、`Simulation` 四子类）维度正交，允许跨列重叠产品按 `cross_category_priority` 去重归列，**绝不可将混变益智与前四类相加求和大盘**。

### 4.2 新品初筛硬组合
- 强制组合三项参数，缺一不可：
  1. `{"name": "Release Date (WW)", "values": ["YYYY/MM", ...], "global": true, "exclude": false}`（由 `data_pipeline.py init-run` 按主窗口末月倒推 12 个完整自然月动态生成）；
  2. `unified_app_primary_categories: ["6014"]`（锁死 Games 主类，防止非游戏应用混入）；
  3. `custom_fields_include_unified_apps: false`（防止老产品新挂子包冒充首发）。
- **禁止使用 `Released Days Ago (WW)`**（经实测为 9 个无精确边界的粗时间桶，与自然月不对齐），也不要将 `Earliest Release Date` 与 `Release Date (WW)` 混用。
- ST 初筛出池后，仍必须用商店最早发行日二次核查，剔除换皮重发或主机/PC 老 IP 移植作品。

### 4.3 衍生指标与「合成合计」预聚合
- `market_calculations.py` 与 `report_values.py` 原生计算四类指标，均不增加额外 ST API 调用：
  - **近 3 月环比**：`operation: "percent_change"`（后 3 月 `sum` 对比前 3 月 `sum`）；
  - **近 3 月绝对增量**：`operation: "contribution"`（产出 `contribution_market` 与各产品 `contribution_delta`，避免小基数新品只看百分比失真）；
  - **近似 RPD**：`operation: "ratio"`（同产品 6 月净收入 ÷ 6 月下载，图注与附录明示「非同期群 LTV」）；
  - **CR10**：`operation: "concentration"`（仅纳自动标签池 Top10 为分子、自动池大盘为分母，**严格排除白名单特例与下载增补位**）。
- **合成合计（`merge_combined`）规则**：基座 `report_values.py` 的 `sum` 限制所有输入必须为同一 `entity_id`；因此由 `data_pipeline.py` 将 ST `Match Merge 2` 与 `Match Merge 3` 同口径月度值预聚合为独立品类 `entity_id: "merge_combined"`，完整保留两侧 `evidence_ids` 与非原生总类声明，再输入 `research_tools.py market`。

---

## 五、分层终止规则

1. **必须硬终止（不换源、不拼半成品）**：
   - 宏观 `game_product_model` 大盘或任一目标启用品类的 6 个月大盘缺失且重试 1 次仍无返回；
   - 任一目标启用品类的 6 个月收入主榜（Top10）不可得或 `research_tools.py market` 报口径冲突且无法核消；
   - 新会话/子代理首步 ST 探针报错无权限。
2. **允许降级登记（标 `data-status="missing"` 并记入附录，不阻断整份报告）**：
   - 个别产品的评论样本不足 20 条或 90 天内无英文评论（转用商店描述 + 截图 + 公开评测补证）；
   - 个别新游在 6 个月窗口前段尚未上线（前段月份留空/有效零值，标注首发月份，仅展示绝对增量不硬算环比）；
   - 个别边缘产品缺少美国 iOS 子包导致 `get-sub-app-details(US)` 无返回（改查 Google Play 描述与实图并留痕）。

---

## 六、来源记录与脱敏格式

写入 `research/data/market_input.json` 及 `delivery-input.json` 的 ST `sources` 条目，必须满足 `provider == "Sensor Tower"`（与 `research_tools.py` 硬校验兼容），且 `raw_file` 统一使用**相对报告目录的路径**：

```json
{
  "st_mkt_merge2_rev": {
    "provider": "Sensor Tower",
    "params": {
      "breakdown": "game_sub_genre",
      "metric": "revenue",
      "os": "unified",
      "regions": [],
      "start_date": "2026-05-01",
      "end_date": "2026-10-31",
      "date_granularity": "month",
      "game_sub_genre": ["Match Merge 2"]
    },
    "observation_scope": "全球（iOS + Google Play，含中国大陆 iOS）月度 IAP 净收入估算",
    "retrieved_at": "2026-11-24",
    "status": "ok",
    "raw_file": "research/raw/st_market_merge_2_rev.json"
  }
}
```
