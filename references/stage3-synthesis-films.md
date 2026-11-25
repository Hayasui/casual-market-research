# Stage 3：供给差矩阵、立项机会卡与影视剧情借鉴规范

本阶段消费已冻结的 `research/data/market_result.json`、`product_roster.json` 与 `tagged_corpus.json`，产出三份结构化 JSON：`supply_matrix.json`、`opportunities.json`、`film_refs.json`。所有面向成稿的文案仅陈述研究定义与客观数据，绝不出现内部口径或来源缩写。

---

## 一、供给差热力矩阵（`research/data/supply_matrix.json`）

### 1.1 两类互补视图
1. **视图 A · Tag × 品类供给热力矩阵**（拆为「主题材轴」与「呈现轴」两段，段内按跨品类命中总数降序）：
   - **全量模式（`full`）列结构**：`Tag｜合成｜三消｜混变益智｜模拟经营｜合成供给差信号`。Tag 链接至附录词表。
   - **`full` 单格内容**：`命中产品数` + `该格产品 6 个月 ST 净收入合计`（白名单特例与下载增补位单注，不重复累加跨列产品）+ `1–2 个头部产品简称`（带产品卡锚点）。
   - **四档热力着色**：
     - `0 款` → `heat_level: "absent"`（虚线浅灰，标「缺席」）
     - `1 款` → `heat_level: "singleton"`（浅琥珀，标「孤例」）
     - `2–3 款` → `heat_level: "moderate"`（浅绿）
     - `≥4 款` → `heat_level: "crowded"`（深绿）
   - **`pilot` 单品类降级规则**：仅 `merge` 有打标数据。其余三列标 `status: "missing"`，渲染为 `data-status="missing"`，文案为「待四品类全量扩充」。试点的组合统计见视图 B。
2. **视图 B · 高频「主题材 × 呈现」二元组合对照表**：
   - `full` 用于观察同一题材在不同画风与渲染形态中的供给差。`pilot` 只列合成样本中组合的命中产品数与占比。

### 1.2 右侧「合成供给差信号」量化判据

| 信号徽章 | `signal_code` | 判定条件（`full` 四品类） |
|---|---|---|
| **外溢空白** | `spillover_gap` | 合成 `0 款`，其余三类合计 `≥2 款`，且其余三类命中产品 6 个月 ST 净收入合计 `≥ $20M` |
| **合成低供给** | `low_supply` | 合成仅 `1 款`，且其余三类合计 `≥2 款` |
| **合成红海** | `crowded` | 合成 `≥3 款` |
| **小众观察** | `niche_watch` | 以上三类信号均未命中（**严禁包装成蓝海推荐**） |

`pilot` 不输出信号列。「合成红海」「外溢空白」「合成低供给」三类写法一律禁入正文。矩阵只在合成列填实测供给，视图 B 的组合占比以已冻结合成打标产品总数为分母。

---

## 二、立项机会卡（`research/data/opportunities.json`）

- **`pilot` 占位**。占位屏只列三项，分别为判据、所需数据和启用阈值。判据沿用本章全量判据，所需数据包括四品类月序列、受控打标与每张机会卡的证据。完整机会卡启用阈值按 `configs/subgenres.json` 的 `opportunity_enable_conditions` 执行，四品类零缺月、盲检达标、低置信项全部裁决、每张卡都有证据四项须同时成立。该档允许 `opportunities.json` 的 `cards` 为空数组，例如 `{"preset": "pilot", "cards": []}`。
- **`full` 完整机会卡**。输出 **3–5 张**，每张填齐下表的 **6 栏**，缺栏时编译校验报错。样本榜单中的缺席只说明榜单现状。

| 序号 | JSON 字段 | 必填内容与写法要求 |
|---:|---|---|
| 1 | `opp_id`、`title`、`target_tag_ids`、`opportunity_type` | 唯一 ID（如 `opp_01`）、精炼方向名、2–3 个受控 `tag_id`、机会类型徽章（`外溢空白` / `呈现差异` / `融合升级` / `新品验证窗口`） |
| 2 | `supply_gap_metrics` | 合成当前供给读数与已验证品类的代表产品与 6 个月 ST 净收入；**所有 ST 数值以 `metric_ref` 对象指向 `market_result.json`**，编译时自动转为 `[[metric]]` 绑定 |
| 3 | `new_release_validation` | 关联 Stage 1 核验的近 12 个月新品（写明验证还是推翻该方向，附产品锚点与近 3 月绝对增量）；若无对应新品，**必须显式填 `status: "unvalidated_cross_category_inference"` 与文案「暂无近 12 月新品验证，属存量跨品类推断」** |
| 4 | `merge_fit_conditions` | 从合成核心体验出发写 2–3 条成立条件：棋盘 48px 图块辨识度、6–8 阶合成链自然度、订单与局外装修/剧情推进的咬合方式 |
| 5 | `counterexamples_and_costs` | **核心防偏栏**：必须正面回答「为何合成头部目前缺席或偏少」——点明美术/剧情产能成本、棋盘视觉同质风险、长线付费深度瓶颈或已知反例代价 |
| 6 | `min_validation_actions` & `linked_film_ids` | 不超过 3 条可落地的低成本立项前置验证动作（如买量题材图文 CTR 烟雾测试、核心合成链视觉辨识原型测试）；并绑定 1–2 部影视借鉴 `film_id` |

### `full` 的 `opportunities.json` 结构示例

```json
{
  "preset": "full",
  "cards": [
    {
      "opp_id": "opp_01",
      "title": "古董修复 × 治愈手绘",
      "opportunity_type": "呈现差异",
      "target_tag_ids": ["antique_restoration", "cozy_handdrawn"],
      "summary_line": "合成打标池有 1 款产品采用古董修复与治愈手绘的组合。",
      "supply_gap_metrics": {
        "merge_count": 1,
        "benchmark_summary": "示例：对比合成打标池与其他已核验品类中该组合的命中情况",
        "metric_refs": [
          {
            "label": "标杆产品近6月ST净收入",
            "result": "market",
            "json_pointer": "/totals/0",
            "match": {"entity_id": "actual_app_id", "metric": "revenue"},
            "digits": 2,
            "divisor": 1000000,
            "suffix": "M USD"
          }
        ]
      },
      "new_release_validation": {
        "status": "unvalidated_cross_category_inference",
        "linked_new_app_ids": [],
        "note": "近 12 个月高增新品中暂未出现同组合验证样本，当前属存量格局差异推断。"
      },
      "merge_fit_conditions": [
        "器物修复天然具备破损→清理→打磨→复原的多阶视觉递进，适配二合 6–8 阶合成链。",
        "单件器物背后附带独立小故事，可按单元剧节奏切分章节，降低长线主线编剧耦合度。"
      ],
      "counterexamples_and_costs": [
        "纯器物轮廓在棋盘缩略图下辨识度弱于食物与家具，需强色彩与高光区分品阶。",
        "温情治愈基调在冷启动买量 CTR 上通常弱于强冲突狗血剧情，前期获客成本需预留余量。"
      ],
      "min_validation_actions": [
        "制作 3 组对比买量静态图（古董修复轻悬疑 vs 传统豪宅翻修），测欧美女性受众 CTR 差值。",
        "搭 2 条 7 阶器物合成链做 48px 手机实屏盲测，核验无文字提示下的品阶可辨识率。"
      ],
      "linked_film_ids": ["film_01", "film_02"]
    }
  ]
}
```

---

## 三、影视剧情借鉴（`research/data/film_refs.json`）

### 3.1 选片地域与数量规则
- **`pilot`** 按命中产品数降序取前两个高频现状标签，每个标签配一部欧美剧影，共 **两部**。卡片挂接对应标签，带灰标「高频现状对照·非立项推荐」，可省略机会卡挂钩。
- **`full`** 按 `opportunities.json` 的机会卡分组，每张卡配 **1–2 部**，总计 **6–8 部**。优先选取欧美高知名度剧集与电影，机会卡明确指向亚洲文化、日系治愈或东亚生活题材时可纳入高口碑日韩作品。

### 3.2 来源深读要求（严禁只摘 IMDb 短梗概）
- **禁止**仅依据 IMDb 首页一两句简介或豆瓣一句话短评扩写剧情。
- 必须通过 `web-search` 或 `chromium-headless` 实际读取以下长剧情来源之一，并在每部作品下登记完整 `evidence_source_id` 与 URL（`provider` 统一写 `"公开维基与影评资料"`）：
  1. 英文 **Wikipedia `Plot` / `Premise` / `Synopsis`** 完整章节；
  2. **Fandom Wiki** 剧集/角色主线剧情页；
  3. 含完整中段转折与结局梳理的**长篇剧情影评或分集总览**。

### 3.3 硬格式红线（`compile_report.py` 静态拦截）
1. **完整剧情段落数**：`plot_paragraphs` 数组必须严格包含 **2–3 段完整叙事**（每段建议 120–260 字，禁止写成 3 句提纲或分点清单）：
   - **第 1 段**：故事背景设定、核心场景与主角初始困境/触发事件；
   - **第 2 段**：主线核心冲突、关键人物关系网与中段关键反转/升级；
   - **第 3 段**（或 2 段式后半段）：矛盾推进、高潮解决与结局/季末悬念（须给出真实结局或首季收束，不能停在「于是展开了一段奇妙旅程」）。
2. **可借用叙事钩子**：`narrative_hook` 限 **1 句话（≤40 字）**。`pilot` 写作品与高频标签的对照点，`full` 写可供立项借用的叙事结构点（如「单元委托串联主线秘密的双向解锁结构」）。
3. **片名、年份与禁填项**。每部必填 4 位整数 `release_year`（1900–2100）。渲染为《中文名》（年份），英文原名用 `<i>` 斜体。具体上映日期（月/日）、演员与主演名单、目标受众三类仍禁止，拦截项见 `configs/workspace.json` 的 `film_reference_policy.forbidden_film_fields`。

### `full` 的 `film_refs.json` 结构示例

```json
{
  "films": [
    {
      "film_id": "film_01",
      "title_zh": "大楼里只有谋杀",
      "title_orig": "Only Murders in the Building",
      "release_year": 2021,
      "region_group": "US",
      "linked_opportunity_id": "opp_01",
      "narrative_hook": "封闭复古公寓内的邻里探案与日常互助，天然适配分房间解锁的轻量悬疑章节。",
      "plot_paragraphs": [
        "故事发生在纽约上西区一座历史悠久的豪华公寓大楼内，三位年龄与性格迥异的住客因共同痴迷一档真实犯罪播客而偶然相识。大楼内一名年轻住户突然离奇身亡，警方迅速以自杀结案，但三人从现场遗留的零散物品与电梯监控盲区中察觉蹊跷，决定联手私下查明真相并同步录制自己的探案播客。",
        "随着调查逐层展开，三人拜访整栋大楼内形形色色的邻居，逐步拼凑出死者与大楼传奇珠宝劫案、过往住客恩怨之间的隐秘联系。他们在彼此试探与争执中建立起跨越代际的信任，同时不断发现看似友善的邻居各自隐藏着财务危机与陈年秘密，而真正的幕后黑手也在暗中监视着播客进展。",
        "在一次次误判嫌疑人并险遭灭口后，三人顺着一枚关键器物线索锁定了潜伏在身边的真凶，成功洗刷被诬陷住户的冤屈。首季案情告破之际，大楼天台却再度出现新的神秘事件与指向主角团的误会，为后续持续探索新楼层与新住客秘密留下了长线悬念。"
      ],
      "evidence_source_ids": ["web_plot_omitb_wiki"]
    }
  ],
  "sources": {
    "web_plot_omitb_wiki": {
      "provider": "公开维基与影评资料",
      "url": "https://en.wikipedia.org/wiki/Only_Murders_in_the_Building",
      "observation_scope": "Wikipedia 剧集主线与首季剧情梗概",
      "retrieved_at": "2026-11-24",
      "status": "ok",
      "text": "完整阅读 Wikipedia Premise 与 Season 1 剧情综述核实人物关系与结局走向。"
    }
  }
}
```

---

## 四、Stage 3 完成检查

三份 JSON 落盘后更新 `research/run_state.json` 为 `stage3_done`，无需人工停顿，直接进入 Stage 4 编译、校验与浏览器视觉目检。
