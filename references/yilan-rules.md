# 第三方商店元数据与素材通道规则

本文定义用于补齐应用商店元数据、核心截图、境外买量辅证与新游线索的第三方数据 MCP 调用规则（当前工作区对应内部报表动作网关，工具后缀以 `data-yilan-mcp-*` 标识）。**本通道只负责非量化元数据与打标材料，绝不进入 Sensor Tower 市场核算链，也不在成品看板正文或来源卡中露出内部平台简称与 MCP 前缀。**

---

## 一、职责边界与成品脱敏映射

| 业务用途 | 允许调用的报表 `identify` | 在本技能中的作用 | 成品来源卡 `provider` 显示名 |
|---|---|---|---|
| 应用基础信息与首发日核验 | `app_info` | 补 `publisher_start`、`subtitle`、发行地区（识别纯国服 iOS）、商店链接 | `应用商店公开元数据` |
| 英文商店描述与核心截图 | `app_detail_desc` | 补完整英文 `description` 与最多 5 张 `screenshot_urls`（供打标必看前 2–3 张） | `应用商店公开元数据` |
| `m_app_id` 与图标索引 | `competitor_category_rankings` | 仅取 `m_app_id` 与 `gameIcon` 映射供批量查素材与下载图标；**严禁采用其收入/下载数值** | `应用商店公开元数据` |
| 境外买量素材辅证 | `material_appgrowing` | 按 `m_app_ids` 批量取高曝光前 3–5 条英文 `slogan` 与封面 URL；**一律标 `marketing_only: true`** | `第三方投放监测素材` |
| 软启动新品补漏 | `competitor_new_app_rankings` | 补充尚未触发 ST 全球发行日的近期新品线索；**入围后仍回 ST 拉全套 6 月序列** | `第三方新游监测索引` |

**绝对禁区**：
- 绝不把本通道任何收入、下载、RPD 或增速字段写入 `market_input.json` 或 `[[metric]]`；
- 绝不调用或混用境内 ADX 素材接口（`material_list_with_detail` / `game_material_list`）替代境外 AppGrowing（`material_appgrowing`）；
- 绝不直接采用本通道返回的 `themes` / `styles` 作为受控 Tag（实测存在明显错标与大量「暂无」）。

---

## 二、强制调用链与参数纪律

每次新会话或子代理首次使用本通道，必须严格按四步链执行：

```text
get-current-time（探针）→ list-actions → get-action-params(identify) → resolve-param-options（按需）→ query-action
```

1. **禁止猜测 `identify`**：报表 `identify` 必须与 `list-actions` 返回值一致，不得使用中文报表名替代。
2. **会话追踪与顶层字段**：
   - 每轮生成 1 个 32 位小写 hex UUID（去横线）作为 `mcp_trace_id`，本轮复用；
   - `mcp_user_question` 与 `mcp_trace_id` 仅作为工具顶层参数传递，**严禁塞入 `params` 对象内**；
   - 严禁向 `params` 写入保留键：`c`、`a`、`token_sign`、`\_source`、`mcp_user_question`、`mcp_trace_id`。
3. **动态下拉枚举**：`play` 必须传叶节点纯数字字符串（例如二合 `"10050213"`、三合 `"10050214"`、三消 `"10050218"`、模拟经营 `"10040201,10040204,10040206,10040209"`，对应 `configs/subgenres.json` 中的 `metadata_reference.external_play_ids`），传中文必报 `code: 0`。
4. **并发节流**：〔待核〕服务端未声明隐藏 QPS 限流配额，统一采用**单批次内串行调用 + 失败重试 1 次**，不做高并发轰炸。

---

## 三、ST → `app_key` 零模糊桥接规则

主榜产品全部已在 ST `get-top-apps(include_app_details=true)` 拿到 `ios_app_ids` 与 `android_app_ids`，按以下优先级构造 `app_key` 调用 `app_info` 与 `app_detail_desc`：

1. **首选海外主包 iOS**：遍历 `ios_app_ids`，拼装 `appstore_<ios_id>`（若同一产品存在多个 iOS ID，优先选英文名/美区主包，避开仅国服特供包）；
2. **次选 Google Play 主包**：若全部 iOS `app_key` 验空或缺失，遍历 `android_app_ids` 拼装 `googleplay_<package_name>`；
3. **兜底网页抓取**：若双端 `app_key` 均返回空壳（`APP_ID == null`），记录 `metadata_fallback: "app_store_public"`，改用 `chromium-headless` 或 `web-search` 访问公开商店页补齐描述与前 2–3 张截图。

**严禁把数字型主游戏 ID `m_app_id`（如 `"3242"`）传给 `app_info` 或 `app_detail_desc`**——该误传不会报错，而是静默返回 `code: 1` 且全部字段为 `null` / `0`。

---

## 四、五大常见陷阱与脚本硬校验

`data_pipeline.py` 已把以下五项检查写成硬拦截逻辑，Agent 落盘原始响应时也须同步遵守：

### 4.1 静默伪空陷阱（最危险）
- **现象**：传入错误 `app_key` 或未收录包名时，返回 `code: 1, message: "success"`，但 `data.APP_ID` 为 `null`、`publisher_start` 为 `"暂无"`、`download` 为 `0`。
- **处置**：绝不以 `code == 1` 判定成功；必须校验 `data.APP_ID` 为非空字符串。若为空，立即轮询下一个 `sub_app` 的 `app_key`，全空则走网页兜底并在 `product_roster.json` 标记。

### 4.2 OSS 响应截断陷阱
- **现象**：大列表返回 `truncated: true`，`data` 内只保留 `demo_rows`（常为 1–10 行样本），完整文件在 `data_url`（有时效的签名地址）中，目标数组位置由 `rows_path` 指明。
- **处置**：凡 `truncated == true`，必须立即调用 `data_pipeline.py fetch-oss --raw-file <原响应.json> --out-file <完整落盘.json>` 下载全量 JSON 并核对行数等于 `total_rows`；**绝不拿 `demo_rows` 当全量数据**；落盘来源卡时严禁保留含签名凭据的临时 `data_url`。

### 4.3 父级 `category_name` 错位陷阱
- **现象**：传入叶子 `play`（如二合 `10050213` 或三消 `10050218`），返回体中的 `category_id` / `category_name` 仍固定显示父级 `100502 / 消除`。
- **处置**：涉及品类索引时一律以**请求入参 `play`** 对应 `configs/subgenres.json` 的业务品类为准，完全忽略返回体中的 `category_name`。

### 4.4 首发日期字段辨析
- `app_info` 同时返回 `publisher_start`（最早产生收入/下载数据的真实发行日，格式 `YYYY.MM.DD`）与 `start_date`（商店上架日）及 `BOOKING_START_TIME`（预约日）。
- **本技能新品核验统一以 `publisher_start` 为主判据**（缺失时再对照 `app_detail_desc.game_info` 与商店公开页）。

### 4.5 金额字段隔离（防 40% 偏差）
- `ori_income` / `income` 为未扣渠道分成的毛流水，`ori_actual_income` / `actual_income` 为约 70% 的估算实收；`web_data_ranking` 甚至不提供 `ori_income`。
- 本技能**一律不读取上述金额字段进入任何表格或计算**；仅使用该通道的文本描述、图标、截图 URL、首发日、发行地区、买量 slogan 与新游名单。

---

## 五、批量拉取买量素材的标准写法

为避免逐款拉取触发过多调用，单品类入围产品（约 13–18 款）通过 `competitor_category_rankings(dim="m_app_id")` 对齐到 `m_app_id` 后，合并为 **1–2 次批量调用**：

```json
{
  "identify": "material_appgrowing",
  "params": {
    "m_app_ids": "<以英文逗号拼接的本品类入围 m_app_id 列表>",
    "page": 1,
    "page_size": 50,
    "sort_field": "impression",
    "sort_order": "DESC"
  }
}
```

- 按 `app_info[].m_app_id` 回分至各产品，每款保留曝光前 3–5 条英文 `slogan` 与 `material_cover` URL；
- 若某产品未命中 `competitor_category_rankings` 的 `m_app_id`，允许用 `game_search(keyword="<游戏名>")` 查单款 `m_app_id` 补齐；若仍无海外投放记录，在材料包中记 `ad_creatives: []` 并注「无近期高曝光投放素材」，不影响打标。

---

## 六、脱敏来源卡格式

写入 `delivery-input.json` 的 `sources` 时，统一套用 `configs/workspace.json` 的 `provider_display_map` 与相对路径：

```json
{
  "meta_desc_merge_mansion": {
    "provider": "应用商店公开元数据",
    "params": {
      "app_key": "appstore_1484442152",
      "scope": "英文商店描述与核心商店截图"
    },
    "observation_scope": "App Store 公开应用详情快照",
    "retrieved_at": "2026-11-24",
    "status": "ok",
    "raw_file": "research/raw/yl_app_desc_5eeb9aa938d7e54b44b09d79.json"
  }
}
```
