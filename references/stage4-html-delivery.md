# Stage 4：看板编译、脱敏验收与交付规范

本阶段目标：由 `compile_report.py` 与 `research_tools.py` 将前三阶段的结构化 JSON 编译为**「一屏概览 + 按品类分章 + `<details>` 折叠深读」的离线双版看板 HTML**，过完脱敏扫描与无头浏览器中文视觉目检，归档至知识库并附固定下载说明。

**严禁手工编写长篇 HTML**；任何正文或数据改动均须先改上游 JSON，再重新执行本页四步构建链。

---

## 一、看板七区布局规范（防退化成一堆宽表）

看板以「卡片 + 视觉条 + 色标胶囊」为主视图，长依据、原始对比宽表与长段剧情统一收进 `<details>` 折叠层或锚点附录：

| 区块 | 导航锚点 | 主视图必显内容（一眼扫完） | `<details>` 展开层 / 附录 |
|---|---|---|---|
| **左侧粘性目录** | 固定 220px（窄屏折顶栏） | 0–6 七大区块锚点 + 四品类快捷跳转 | 打印态（`@media print`）隐藏导航、自动展开全部折叠区 |
| **0. 执行概览（首屏一屏看完）** | `#sec-overview` | 顶栏元信息条（标题、品类范围、6 月窗口、采集日、ST 净收入声明）+ 4 联 KPI 卡（宏观休闲 6 月净收入、宏观下载、合成 6 月净收入、最高增速赛道）+ 3 行定性总结 + `full` 的机会摘要卡（Tag 胶囊 + 信号徽章 + 跳转详卡）或 `pilot` 的占位屏入口 | — |
| **1. 市场盘面看板** | `#sec-market` | 双轴宏观 SVG 图 + 四品类 KPI 横比条（6 月净收入、下载、近 3 月环比、CR10、中位近似 RPD） | `<details>` 正交维度声明（混变益智不可与其余三类玩法品类（合成、三消、模拟经营）相加）与 ST 估算统计规则 |
| **2. 品类分章看板** | `#sec-subgenres` | 纯 CSS Radio Tab 切换（`合成｜三消｜混变益智｜模拟经营｜全部展开`。`pilot` 首显合成，其余三类标 `data-status="missing"`，文案为「待四品类全量扩充」）。每品类内含：A. 3 项 KPI + 3 句头部格局；B. Top10 收入水平条形图；C. **2 列紧凑产品卡栅格**（48px 图标、榜位/特例/增补徽章、名称与发行商、3 条迷你 KPI、近似 RPD、2–3 个可点 Tag 胶囊、叙事强度及按联动规则填写的母题（显示为「叙事类型」）、≤35 字定性） | 每张产品卡内 `<details>`：各 Tag 逐字原文依据及完整 `narrative_evidence`，逐条带 `[[cite]]`、次选排除理由、买量 slogan（标 `[买量素材·仅辅证]`）、ST 生效首发日与商店发行日、ST 子类与纯国服 iOS 地区注记；品类末尾 `<details>`：完整等宽数字对比表 |
| **3. 高增新品观察** | `#sec-new-apps` | 筛选口径徽章（`Release Date (WW)` 近 12 月 + `6014` + ST 生效首发日核验）+ 每品类定额五席加边界席标注，横卡显示近 3 月绝对增量、Tag 胶囊、叙事强度及按联动规则填写的母题。`full` 可挂验证或推翻机会的锚点 | 每张新品卡的 `<details>` 展开完整 `narrative_evidence`，逐条带 `[[cite]]`。区末为 `<details>` 新品审计表（剔除原因与触发边界席时的第 6 名实数） |
| **4. Tag 供给差矩阵与机会详卡** | `#sec-matrix` | 视图 A：`full` 展示 Tag × 四品类供给热力矩阵（主题材、呈现两段，4 档热力底色及合成信号徽章），`pilot` 仅合成列有实测供给，其余三列标「待四品类全量扩充」，无信号列。视图 C：`full` 展示 3–5 张机会详卡（供给差 `[[metric]]` 读数与新品验证状态），`pilot` 展示占位屏（判据、所需数据、启用阈值） | 视图 B `<details>`：高频「主题材 × 呈现」二元组合表；每张 `full` 机会卡 `<details>`：合成适配条件、反例与代价、最小验证动作、跳转影视锚点 |
| **5. 影视借鉴卡片墙** | `#sec-films` | `pilot` 为两部现状对照，按两个高频标签挂接，卡片带灰标「高频现状对照·非立项推荐」。`full` 按机会卡分组展示 6–8 部。两档均展示《中文名》（年份）、`<i>` 斜体英文原名、对应徽章、≤40 字对照点或叙事钩子与剧情首段预览 | `<details>` 展开完整 2–3 段剧情介绍 + 外部长来源 `[[cite]]`（**严禁具体上映日期（月/日）、演员与主演名单、目标受众**） |
| **6. 受控词表与数据口径附录** | `#sec-appendix` | ① 受控词表卡片索引（`#tag-<tag_id>`，全篇胶囊落点：定义、纳入例、排除例、全部命中产品反链）；② 独立上下文盲态重测一致率表（披露实际重测通道与分歧终裁）；③ 统计口径与估算边界声明卡（ST 净收入、全球含大陆 iOS、不含广告变现、近似 RPD 非同期群 LTV、与毛流水口径存在平台分成差异）；④ `[[sources]]` 脱敏来源卡 + 缺项登记表（`research/data/gap_register.json`） | — |

### 主题材胶囊与矩阵分组

- 主榜与新品卡的主题材胶囊显示「一级 › 二级」。胶囊的 `href`、`data-tag-id` 与产品卡的 `data-tags` 只绑定二级 `tag_id`。
- 矩阵主题材段按 `parent_domain` 插入一级分组小标题，标题行跨满当前矩阵列。一级合计若有展示，须按 `unified_app_id` 去重，并单列在汇总区。

### 词表签字表

沿用 [Stage 2 人工签字格式](stage2-tagging.md)。受控词表草稿在 `tag_id` 后加 `一级` 列，主题材填 `parent_domain` 对应的中文名，其余轴留空。

### 缺项登记与发布闸门

缺项落盘为 `research/data/gap_register.json`。每条含 `gap_id`、`impact_level`、`missing_evidence`、`affected_tables`、`affected_claim_types`、`allowed_release_scope`、`release_condition` 和 `status`。

| 等级 | 缺口 | 处理 |
|---|---|---|
| `L1` | 池子与必选槽缺口 | 签字前拦截，缺项补齐后放行。 |
| `L2` | 排序与判断缺口 | 编译前拦截受影响内容，自动停用受影响的信号与机会卡。未受影响的内容按 `allowed_release_scope` 编译与交付。 |
| `L3` | 辅证与外推缺口 | 不阻断，自动渲染进附录与范围声明。 |

### 1.4 图表与呈现七条渲染验收规则

1. 图题居上居中、图号自动递增
2. 图注居下居中且不小于 12px，图标不输出可见图注
3. 轴单位写在轴顶端，刻度用 1／2／2.5／5／10 整洁数
4. SVG 字号不小于 12
5. 排行图用水平条形图并按词边界截断，不切断英文单词
6. 图例水平居中并带色块或线点标记，双轴固定左轴在前
7. 窄屏允许折行，820px 与 540px 两档断点不出现页面级横向滚动

查看 SVG 的 `font-size` 与实际渲染字号。宽表可在容器内横向滚动。

---

## 二、零 JS 与全量版内联脚本白名单

脚本模式由 `configs/subgenres.json` 的 `presets.<mode>.html_script_mode` 与 `configs/workspace.json` 的 `delivery_policy.script_modes` 联合控制：

1. **`pilot` 模式（`--script-mode zero_js`，硬锁零脚本）**：
   - `html_delivery.py` 硬性禁止页面出现任何 `<script>` 标签；
   - 全部浏览交互由纯 HTML + CSS 实现（左侧 `position: sticky` 导航、`<input type="radio">` 品类 Tab、`<details>` 展开折叠、`:target` 词表高亮、横向滚动表格）。
2. **`full` 四品类全量版（`--script-mode whitelisted_inline_js`）**：
   - `html_delivery.py` 仅放行**至多 1 个**带有属性 `data-approved-inline="dashboard-ui"` 的内联 `<script>` 块，且同时通过五项静态门禁：
     1. 有效代码行数 **≤ 150 行**；
     2. 无 `src` 属性、零外部脚本/CDN 依赖；
     3. 零 `eval`、`new Function`、`fetch`、`XMLHttpRequest`、`WebSocket`、`document.write`；
     4. 仅提供三项纯本地 DOM 交互：点击 Tag 胶囊跨品类高亮/筛选命中产品、点击数值表头升/降序重排、一键全展/全折 `<details>`；
     5. **禁脚本降级可读**：在浏览器禁用 JS 时，纯 CSS Radio Tab、锚点跳转与原生 `<details>` 仍保证 100% 内容完整可读。

---

## 三、标准编译、校验与脱敏命令顺序

在 `<报告目录>` 全部上游 JSON 与 SVG 图表就绪后，按以下固定顺序执行 **4 条命令**（全部退出码须为 `0`）：

```text
# 1. 编译模板与交付清单（检查缺项闸门、full 机会卡 6 栏或 pilot 占位屏、影视 2–3 段、年份及禁填项，预处理来源脱敏）
python <Skill目录>/scripts/compile_report.py build \
  --out-dir <报告目录> \
  --preset <pilot|full>

# 2. 生成双版 HTML（<报告名>.html + <报告名>.base64.html + image/）
python <Skill目录>/scripts/research_tools.py html \
  --input <报告目录>/research/delivery-input.json \
  --output <报告目录> \
  --metadata <报告目录>/research/delivery.json \
  --script-mode <zero_js|whitelisted_inline_js>

# 3. 校验双版 HTML 内容一致性、[[metric]]/[[cite]]/内部锚点与脚本白名单
python <Skill目录>/scripts/research_tools.py validate \
  --input <报告目录>/research/delivery-input.json \
  --output <报告目录> \
  --script-mode <zero_js|whitelisted_inline_js>

# 4. 终稿全文脱敏扫描 + 单文件体积校验（< 3 MB）+ 打分体源码包 _bundle.zip
python <Skill目录>/scripts/compile_report.py pack-bundle \
  --out-dir <报告目录>
```

### 脱敏扫描硬红线（第 4 步自动执行）
`compile_report.py pack-bundle` 扫描 `<报告名>.html` 与 `<报告名>.base64.html` 全文（含所有可见文字、`title` 属性与 `[[sources]]` 卡片），对照 `configs/workspace.json` 的 `sanitization_policy.forbidden_public_patterns`，要求命中数为 **0**：
- 严禁出现服务器绝对路径、MCP 工具前缀、内部平台名称与模型运行时名；
- 严禁出现任何内部口径/归属措辞（如「运营月报」「运营口径」「按运营」「运营分类」「月报」）；
- `[[sources]]` 卡片仅保留客观 `provider`、清洗后的统计 `params`、`observation_scope`、`url`、`retrieved_at` 与相对报告目录的 `raw_file`。

---

## 四、无头浏览器视觉目检与无截图硬降级声明

### 4.1 标准视觉目检（默认必过）
使用当前工作区的 `chromium-headless` 对本地 `<报告名>.base64.html` 截取关键视口 PNG（保存至 `<报告目录>/research/screenshots/`），再逐张用 `Read` 工具目检：

1. `01_overview_1440.png`（1440×900 首屏）。核对顶栏元信息、4 联 KPI 卡、机会摘要卡或占位屏入口及中文字体。按规则 1 核对首屏露出的图题。首张图的起始图号在市场图截图确认。
2. `02_market_svg.png`。核对双轴 SVG 与四品类 KPI 条无标签重叠。按规则 1–4、6 核对图题图号、图注位置与字号、轴顶单位与刻度、SVG 字号、图例标记与左轴优先次序。
3. `03_subgenre_cards.png`。核对两列产品卡对齐、48px 图标正常、Tag 胶囊留在卡内。按规则 1、2、5 核对排行图图号接续、产品图标无可见图注、条形横排且英文按词截断。
4. `04_details_expanded.png`。核对 Tag 与叙事证据、来源编号和完整数值表。按规则 2 复查展开产品卡的图标无可见图注，按规则 7 核对宽表可在容器内滚动。
5. `05_matrix_opps.png`。核对热力矩阵四档底色、`full` 机会卡六栏、`pilot` 无信号列且占位屏三项齐全。按规则 7 核对 820px 下矩阵宽表留在容器内，机会卡内容能折行，页面无横向滚动。
6. `06_films_appendix.png`。核对片名与年份格式、展开后的 2–3 段剧情及附录来源脱敏。按规则 7 核对 540px 下片名、正文和来源文字可换行，页面无横向滚动。
7. `07_narrow_or_print.png`（窄屏，540px）。按规则 7 核对侧栏收至顶栏、页面无横向滚动，并与第 5 张的 820px 结果交叉核对。另检查打印预览可展开已交付品类与折叠内容。

全部核验无误后，更新 `research/delivery.json` 中 `browser_render_verified: true` 及 `run_state.json` 为 `delivered`。

### 4.2 无截图硬降级声明
若运行环境缺少可用浏览器或截图失败，**严禁宣称「完整交付通过」**，必须在交付回执首段粘贴以下固定降级声明：

> **【视觉目检降级声明】** 本次报告仅完成结构与数据绑定校验（`validate == checked`、脱敏扫描零命中），未完成无头浏览器中文渲染视觉目检；宽表横向滚动、SVG 标注重叠与中文断行请在本地 Chrome/Edge 浏览器打开 `.base64.html` 后人工复核。

---

## 五、知识库归档与固定短链打开说明

1. **上传对象**：同步上传至知识库个人根目录 `~`：
   - `<报告名>.base64.html`（单文件自包含版，硬控 **< 3.0 MB**）；
   - `<报告名>_bundle.zip`（含 `<报告名>.html`、`image/` 与关键 JSON 快照）。
2. **上传流程**：`storage_upload_prepare` → 运行生成的直传脚本 → `storage_upload_commit` → `storage_get_by_path(with_page_url=true)` 回查确认存在 → **立即删除临时 `upload.sh`**。
3. **交付回执首部固定说明模板**（每次交付原样附于回执顶部，替换尖括号占位符）：

> **【交付物与本地打开方式】** 本次看板已归档至知识库个人根目录 `~` 及任务输出目录：
> 1. **直接阅读推荐**：`<报告名>.base64.html`（单文件自包含、体积 `<X.XX>` MB，内联样式、SVG 图表与产品图标）。
> 2. **完整分体包**：`<报告名>_bundle.zip`（含非自包含 HTML 与 `image/` 资源目录）。
> 3. **短链使用须知**：知识库生成的短链受门户鉴权限制，**不提供静态网页在线托管**；请通过短链或知识库页面将 `.base64.html` **下载到本地**，使用 Chrome / Edge 等浏览器直接双击打开阅读。

---

## 六、完整交付 10 项验收读数清单

以下 10 项全部通过，方可声明完整交付（`pilot` 按合成单品类口径核验，通过后即可扩至 `full`）：

1. **大盘零缺月**。宏观及启用品类近 6 个完整自然月的收入与下载 0 缺月、0 冲突。合成为 ST `Match Merge 2` 与 `Match Merge 3` 同月合并值，拆分审计齐全。`full` 覆盖合成、三消、混变益智、模拟经营。`research_tools.py market` 退出码 `0`。
2. **榜单、增补位与新品洁净**：主榜 10/10、近似 RPD 齐备；白名单特例与下载增补位均未计入 CR10 的主榜 Top10 净收入合计；新品每品类定额五席，边界席已标，全部入选新品通过 `Release Date (WW)` 近 12 月 + `6014` + `custom_fields_include_unified_apps: false` + ST 生效首发日复核，0 个老品翻红或非游戏应用混入。
3. **双源接口纪律零违规**：禁用独立详情工具调用次数 = `0`；主榜使用 `include_app_details=true`，边缘标签核查仅用 `get-sub-app-details(US)`；ST → 元数据 `app_key` 匹配率 ≥ 90%，0 处未处置静默伪空或父级品类错引，0 处非 ST 数值混入计算链。
4. **词表签字与 Tag 全追溯**：词表经对话人工批复（`pilot` 12–20 词、`full` 25–40 词）；100% 入围产品绑定 2–3 个受控 Tag 且看过前 2–3 张核心截图，0 个黑名单泛词，0 个纯买量单证 Tag。产品卡与新品卡叙事强度、联动母题与证据齐全。所有 Tag 胶囊可跳附录词表，每条依据带 `[[cite]]` 可跳来源卡。
5. **独立上下文盲检达标且如实披露**。`pilot` 抽 5 款，`full` 抽 12 款（每品类 3 款）。`tag_qc.py` 核验主题材一致率 ≥ 80%、`presentation_agreement >= 80%`、主槽平均 Jaccard 重叠率 ≥ 75%。四轴分歧均已显式裁决，未裁决低置信项为 0，附录披露实际重测通道。
6. **机会卡与影视卡合红线**：`full` 机会卡 6 栏齐全（必含适配条件与反例代价），影视按机会卡分组六到八部，欧美优先。`pilot` 允许零实卡，判据、所需数据与启用阈值在占位屏列齐即通过机会卡验收，两部影视按高频现状标签挂接并带灰标。两档每部均含 2–3 段完整剧情与长来源。必填 4 位整数年份 `release_year`，显示《中文名》（年份）及 `<i>` 斜体英文原名。具体上映日期（月/日）、演员与主演名单、目标受众均为 0 处。
7. **看板信息层次与绑定达标**：首屏 1 屏可见 KPI 带与对应档位的机会摘要或占位屏入口；Top10 以 2 列产品卡呈现、长依据与完整数值表折进 `<details>`；`[[metric]]` 绑定 > 0，0 个未替换占位符与断链锚点。
8. **脚本治理、脱敏与体积合规**：`research_tools.py validate` 返回 `checked`；`pilot` 含 0 个 `<script>`、`full` 内联脚本 ≤150 行且禁脚本可读；脱敏扫描 0 命中；`.base64.html` 体积 < 3.0 MB 且 `_bundle.zip` 完整。
9. **无头浏览器中文视觉目检通过**：按 4.1 核对七张截图与 1.4 七条渲染规则，零方块字、零文字重叠裁切、零破图（无法截图时已挂硬降级声明）。
10. **知识库归档与说明到位**：`.base64.html` 与 `_bundle.zip` 已上传 `~` 并回查存在，临时 `upload.sh` 已删除，回执首部附固定短链下载打开说明。
