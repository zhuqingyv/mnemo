# viz_v2 模块拆分 TASK-LIST

本文档按 Wave 分组列出所有拆分任务。源文件：`viz/viz_v2/index.html`（3526 行）。
目标：拆成 ≤ 200 行 / 文件的 CSS + JS 模块，通过 `window.__viz` 命名空间通信。

> 所有 JS 文件采用 IIFE + 解构 + 注册模式，详见 `INTERFACE-CONTRACTS.md`。
> 所有完成判据中的「行数」为上限（软红线：≤ 200 行，硬红线：≤ 260 行）。
> **执行原则**：同 Wave 内任务可并行；跨 Wave 必须按顺序执行。

---

## 源文件行区地图（index.html）

| 区段 | 起止行 | 内容 |
|---|---|---|
| `<style>` 内联 CSS | 7–796 | CSS 变量 + 各 section 样式块 |
| head / body skeleton | 1–6, 797–803 | HTML 骨架 + CDN 脚本 |
| IIFE 序章（patch + 解构 + 常量 + API + state） | 804–955 | nova-dom 事件 patch、NovaView 解构、COLORS、API/HEALTH、apiGet、state、_graphMaps、_3dMaps、_pulseSeenEventIds、escapeHtml、_syncViewToUrl、popstate |
| Topbar/Dashboard/ToolChart/search | 956–1249 | runSearch/onSearchInput/clearSearch/triggerSearch + Topbar/Dashboard/ToolChart 组件 |
| KnowledgeList/Card | 1250–1336 | statusClass/scopeClass/KnowledgeCard/KnowledgeList |
| DetailPanel + helpers | 1338–1525 | formatTimeAgo/sliceIso/openDetail/closeDetail/FeedbackBar/RelItem/RelationList/MetaRow/MetaBlock/SupersededBanner/PanelBody/DetailPanel |
| graph2d 全套 | 1528–2816 | color utils / build / diff / physics / render / loop / interactions / pulses / polling / HUD |
| graph3d 全套 | 2817–3391 | state + 颜色/数据/label/engine/lifecycle + Graph3DView 组件 |
| GraphView + App + mount | 3393–3522 | 2D 容器组件 + App 根组件 + mount 入口 |

---

## Wave 1 — 零业务依赖层（可全部并行）

### CSS 拆分（6 件）

| ID | 文件 | 职责 | 源行 | 完成判据 |
|---|---|---|---|---|
| W1-01 | `css/tokens.css` | `:root` 设计变量 | 8–28 | 文件仅含 `:root { ... }`；含 `--bg-*`、`--text-*`、`--accent*`、`--green/yellow/red/purple/gray`、`--mono`、`--radius` 全部 token |
| W1-02 | `css/base.css` | 全局 reset | 30–36 | 含 `*{box-sizing}` + `html,body{margin/padding/bg/color/font}` + `button{font/cursor}` |
| W1-03 | `css/dashboard.css` | Topbar + metrics + tool-chart | 38–322 | 保留注释头 `_dashboard.css`；含 `.topbar/.brand/.api-state/.search-wrap/.view-switch/.btn`；`.metrics-grid/.metric/.bar-row/.bar-fill`；`.sec-head/.hint`；`.tool-chart*` |
| W1-04 | `css/list.css` | KnowledgeList + Card | 323–439 | 保留注释头 `_list.css`；含 `.list-wrap/.k-card/.k-card-head/.status-*/.scope-*/.claim-*/.k-summary/.k-tags/.k-foot` |
| W1-05 | `css/detail.css` | DetailPanel + FeedbackBar + RelationList | 440–558 | 保留注释头 `_detail.css`；含 `.detail-overlay/.detail-panel/.dp-head/.dp-body/.dp-meta/.dp-summary/.dp-content/.fb-bar/.rel-list/.rel-item/.sb-*` |
| W1-06 | `css/graph.css` | 2D canvas + 3D + HUD + tooltip | 559–795 | 保留注释头 `_graph.css`；含 `#graph-view/#graph-canvas/#graph-3d-view/#graph-3d-container/#label-layer/.graph-tooltip/.graph-loading/.graph-panel/.gp-*/.gpt-*/.fill-*` |

**完成判据（所有 CSS）**：
- 每文件 ≤ 200 行；超过时按注释二级标题继续拆（如 `dashboard.css` 过大则拆 `dashboard-topbar.css` / `dashboard-metrics.css`）。
- 所有选择器保留原样，不新增/修改/合并。
- `index.html` 里 `<link rel="stylesheet" href="css/xxx.css">` 按顺序加载：tokens → base → dashboard → list → detail → graph。
- 在 VSCode 里用 Color Picker 或截图对比 v2 → v2 拆分后，视觉 100% 一致。

**依赖**：无。

---

### JS 基础层（7 件）

| ID | 文件 | 职责 | 源行 | 完成判据 |
|---|---|---|---|---|
| W1-07 | `js/bootstrap.js` | 事件 patch + NovaView 解构 + COLORS + API 常量 + apiGet | 805–852 | 注册 `__viz.NV`（解构的 ref/reactive/computed/watch/watchEffect/effect/batch/Dom/component/mount/Show/For/Switch/onMounted/onUnmounted）、`__viz.DomTags`（div/span/button/input/h2/h3/h4/p/ul/li/a/canvas/section/main/header）、`__viz.COLORS`、`__viz.API`、`__viz.HEALTH`、`__viz.apiGet`；全局 patch `HTMLElement.prototype.addEventListener/removeEventListener` 小写化 |
| W1-08 | `js/constants.js` | 纯常量 | 分散：1582（TYPE_RANK）、1627（MAX_GRAPH_NODES）、1878–1881（PULSE_*）、1971–1973（ALPHA_*）、2100–2104（FADE/COLOR/HALO）、2849（PROJECT_HUES_3D）、2871–2876（EDGE_STYLES_3D） | 注册 `__viz.const` 对象含 TYPE_RANK、MAX_GRAPH_NODES、PULSE_WINDOW_SECONDS、PULSE_DURATION_MS、PULSE_MAX_RADIUS、PULSE_SEEN_CAP、ALPHA_DECAY、ALPHA_MIN、ALPHA_RESTART、FADE_IN_MS、FADE_OUT_MS、EDGE_FADE_IN_MS、COLOR_TWEEN_MS、HALO_DURATION_MS、PROJECT_HUES_3D、EDGE_STYLES_3D |
| W1-09 | `js/state.js` | reactive state + _graphMaps + _3dMaps + _pulseSeenEventIds + state.graph 扩展字段 + state.graph3d 初始化 | 854–898、1535–1546、2840–2846 | 注册 `__viz.state`、`__viz._graphMaps`、`__viz._3dMaps`、`__viz._pulseSeenEventIds`；字段清单与 INTERFACE-CONTRACTS 完全一致；Map/Set 必须是 _graphMaps/_3dMaps 的属性，不能放进 reactive() |
| W1-10 | `js/utils.js` | escapeHtml + formatTimeAgo + sliceIso + truncateTitle + _syncViewToUrl + popstate 挂载 | 938–955、1342–1355、2487–2490 | 注册 `__viz.util.escapeHtml/formatTimeAgo/sliceIso/truncateTitle/syncViewToUrl`；文件自身在加载时挂 `window.addEventListener('popstate', ...)` |
| W1-11 | `js/graph2d/color.js` | parseColor / lerpColor / brightenColor | 1549–1579 | 注册 `__viz.g2d.color = { parseColor, lerpColor, brightenColor }`；纯函数，不读 state |
| W1-12 | `js/graph2d/sprite-cache.js` | getGlowSprite + glowSpriteCache 私有 Map | 1976–2001 | 注册 `__viz.g2d.getGlowSprite(color, nodeRadius, blurStrength)`；内部缓存不暴露 |
| W1-13 | `js/graph3d/style.js` | projectColor3D / nodeColor3D / edgeStyle3D / truncTitle3D | 2848–2883 | 注册 `__viz.g3d.style = { projectColor3D, nodeColor3D, edgeStyle3D, truncTitle3D }`；内部维护 `_projectColorCache3D`、`_projectColorIdx3D` 作私有闭包 |

**完成判据（所有 JS 基础层）**：
- 每文件头部注释写明 `职责/依赖/注册项`（见 INTERFACE-CONTRACTS 的文件模板）。
- 严禁在此 Wave 内读任何 DOM 或依赖其他 `__viz.*` 属性（只允许读 `__viz.state / __viz.const`）。
- 加载顺序（index.html）：bootstrap.js → constants.js → state.js → utils.js → graph2d/color.js → graph2d/sprite-cache.js → graph3d/style.js。
- 浏览器打开 index.html，Console 无报错；`Object.keys(window.__viz)` 能看到 `NV / DomTags / COLORS / API / HEALTH / apiGet / const / state / _graphMaps / _3dMaps / _pulseSeenEventIds / util / g2d / g3d`。

**依赖**：无。

---

## Wave 2 — 业务服务层（可并行）

| ID | 文件 | 职责 | 源行 | 完成判据 |
|---|---|---|---|---|
| W2-01 | `js/data-loaders.js` | probeHealth / loadStats / loadKnowledge / loadRelations | 901–935 | 注册 `__viz.loader = { probeHealth, loadStats, loadKnowledge, loadRelations }`；内部用 `__viz.apiGet`、读写 `__viz.state`；失败路径 console.warn 不抛 |
| W2-02 | `js/search.js` | runSearch / onSearchInput / clearSearch / triggerSearch + _searchDebounceTimer | 962–1010 | 注册 `__viz.search = { runSearch, onSearchInput, clearSearch, triggerSearch }`；debounce 220ms 保留；query mismatch 保护保留 |
| W2-03 | `js/detail-actions.js` | openDetail / closeDetail | 1357–1399 | 注册 `__viz.detail = { openDetail, closeDetail }`；`openDetail(id)` 必须走 `apiGet(API + '/knowledge/' + id)`、`API + '/relations?id=' + id` 两路、处理 loading/error；closeDetail 清空 `selectedId/selectedDetail/selectedError` |
| W2-04 | `js/graph2d/build.js` | buildTargetEdgeMap / nodeFillFor / spawnPositionOutside / buildGraph | 1581–1695 | 注册 `__viz.g2d.build = { buildTargetEdgeMap, nodeFillFor, spawnPositionOutside, buildGraph }`；buildGraph 环形初始化 + jitter + 用 `_graphMaps.nodeIndex/edgeIndex` 填充；MAX_GRAPH_NODES 截断保留 |
| W2-05 | `js/graph2d/physics.js` | QuadTree + stepPhysics + markPhysicsActive | 2003–2008、2159–2298 | 注册 `__viz.g2d.physics = { stepPhysics, markPhysicsActive }`；QuadTree Barnes-Hut θ=0.8 KR=5000、弹簧/阻尼/向心/fixed 语义保留；alpha 管理与 Wave 3 loop 对齐 |
| W2-06 | `js/graph2d/render.js` | drawGraph（全 2D canvas 绘制） | 2301–2485 | 注册 `__viz.g2d.render = { drawGraph }`；读 `__viz.state.graph`、`__viz.g2d.color/getGlowSprite`、`__viz.util.truncateTitle`；保持当前每帧绘制逻辑（edges → glow sprite → nodes → labels → pulses → hover halo） |
| W2-07 | `js/graph3d/data.js` | _newNode3D / _collect3DLinks / build3DGraphData / _recomputeEdgeCounts3D / diff3DGraphData | 2885–3036 | 注册 `__viz.g3d.data = { build3DGraphData, diff3DGraphData, recomputeEdgeCounts3D }`；内部用 `__viz.state._rawRelations/knowledge`、`__viz._3dMaps.nodesById`；diff 返回 `{addedIds, removedIds, touched}` |
| W2-08 | `js/graph3d/labels.js` | rebuild3DLabels / updateLabelLayer3D / updateLabels3D | 3041–3155 | 注册 `__viz.g3d.labels = { rebuildLabels, updateLabelLayer, updateLabels }`；用 `document.getElementById('label-layer')`；`_3dMaps.labelEls` 为唯一 DOM 索引；`_hidden` 缓存保留 |

**完成判据（整 Wave）**：
- 打开 index.html，Console 无报错。
- `window.__viz.loader.probeHealth()` 能把 `state.serverOnline` 翻为 true（后端跑在 127.0.0.1:8787）。
- `window.__viz.search.runSearch('mnemo')` 能把 `state.searchResults` 填为数组。
- `window.__viz.g2d.build.buildGraph()` 不报错（在 knowledge 非空时）。
- `window.__viz.g3d.data.build3DGraphData()` 返回 `{nodes, links}` 且 nodes.length === knowledge.length。

**依赖**：Wave 1 全部。

---

## Wave 3 — 组件 + 生命周期层（可并行）

### graph2d 收口 + pulses + polling

| ID | 文件 | 职责 | 源行 | 完成判据 |
|---|---|---|---|---|
| W3-01 | `js/graph2d/loop.js` | markRenderDirty / hasLiveAnimations / ensureRAF / graphTick / startGraphLoop / stopGraphLoop / stepAnimations | 2002–2156、1963–1973（VIZ_SMART_PAUSE 常量）、`__vizPerf` 全局初始化（若源有） | 注册 `__viz.g2d.loop = { startGraphLoop, stopGraphLoop, ensureRAF, markRenderDirty, markPhysicsActive }`；`__vizPerf.frameTimes` 逻辑保留（窗口 700）；ALPHA_DECAY/ALPHA_MIN 从 constants 读 |
| W3-02 | `js/graph2d/diff.js` | computeDiff / applyDiff / refreshGraphIncremental | 1697–1875 | 注册 `__viz.g2d.diff = { computeDiff, applyDiff, refreshGraphIncremental }`；refreshGraphIncremental 在 view==='3d' 时分派到 3D diff，在 view==='2d' 且 built 时走 2D applyDiff |
| W3-03 | `js/graph2d/interactions.js` | screenToWorld / pickNode / resizeCanvas / wireGraphInteractions | 1950–1961、2493–2644 | 注册 `__viz.g2d.interactions = { resizeCanvas, pickNode, wireGraphInteractions }`；`_graphInteractionsWired` 幂等；mouseup 不走 open 时误判需用 moved>4 保护保留 |
| W3-04 | `js/graph2d/pulses.js` | parseHitIdsFromSummary / spawnPulsesForNodes / pollSearchPulses | 1883–1927 | 注册 `__viz.g2d.pulses = { parseHitIdsFromSummary, spawnPulsesForNodes, pollSearchPulses }`；`_pulseSeenEventIds` 上限 PULSE_SEEN_CAP 的滑动窗口保留 |
| W3-05 | `js/graph2d/search-match.js` | updateGraphSearchMatch | 2646–2651 | 注册 `__viz.g2d.updateSearchMatch`；单函数小文件，可合并到 interactions.js 尾部但不要合并到 diff.js |
| W3-06 | `js/polling.js` | startPolling / stopPolling | 1929–1947 | 注册 `__viz.polling = { startPolling, stopPolling }`；按 view 分派到 `g2d.diff.refreshGraphIncremental` 或 fallback 到 `loader.loadStats/loadKnowledge/loadRelations` |

### graph3d 收口

| ID | 文件 | 职责 | 源行 | 完成判据 |
|---|---|---|---|---|
| W3-07 | `js/graph3d/engine.js` | init3DGraph（ForceGraph3D 配置 + onNodeClick + onEngineStop + pauseAnimation） | 3158–3251 | 注册 `__viz.g3d.initGraph`；CDN 依赖 `window.ForceGraph3D`；cooldownTicks 60、onEngineStop → pauseAnimation 保留；nodeClick → `__viz.detail.openDetail(n.id)` |
| W3-08 | `js/graph3d/lifecycle.js` | _recolorNode3D / update3DSearchMatch / activate3DGraph / deactivate3DGraph | 3253–3348 | 注册 `__viz.g3d.lifecycle = { recolorNode, updateSearchMatch, activate, deactivate }`；activate 懒初始化（首次 init + graphData + labels），已建则 re-measure + updateLabels；deactivate 调 `pauseAnimation()` |

### 业务组件（UI 层）

| ID | 文件 | 职责 | 源行 | 完成判据 |
|---|---|---|---|---|
| W3-09 | `js/components/topbar.js` | Topbar 组件（brand / api-state / search input / view-switch / refresh） | 1016–1081 | 注册 `__viz.comp.Topbar`；view-switch 点击 → `state.view = 'list'|'2d'|'3d'` + `util.syncViewToUrl()`；刷新按钮调 `loader.loadStats/loadKnowledge/loadRelations` |
| W3-10 | `js/components/dashboard.js` | Dashboard 组件 + subCell/barRow/pulsingValue 辅助 | 1083–1212 | 注册 `__viz.comp.Dashboard`；所有 getter 防空链 `(state.stats && state.stats.xxx) \|\| 0` 保留 |
| W3-11 | `js/components/tool-chart.js` | ToolChart 组件 | 1214–1248 | 注册 `__viz.comp.ToolChart`；Object.entries 降序取 top N 保留 |
| W3-12 | `js/components/list.js` | statusClass / scopeClass / KnowledgeCard / KnowledgeList | 1254–1336 | 注册 `__viz.comp.KnowledgeList` + `__viz.comp.KnowledgeCard`；卡片 click → `detail.openDetail(k.id)` |
| W3-13 | `js/components/detail-panel.js` | FeedbackBar/RelItem/RelationList/MetaRow/MetaBlock/SupersededBanner/PanelBody/DetailPanel | 1401–1525 | 注册 `__viz.comp.DetailPanel`；overlay 点击关闭、panel 内点击冒泡阻止；Show() 按 `state.selectedId != null` 分派 |
| W3-14 | `js/components/graph-hud.js` | LegendSwatch / GraphLegend / MetricRow / MetricSub / GraphMetricsPanel | 2655–2815 | 注册 `__viz.comp.GraphLegend` + `__viz.comp.GraphMetricsPanel`；collapsed ref、折叠态点击整体展开保留 |

**完成判据（整 Wave）**：
- Console 无报错，`window.__viz.comp` 含 7 项（Topbar/Dashboard/ToolChart/KnowledgeList/KnowledgeCard/DetailPanel/GraphLegend/GraphMetricsPanel）。
- `window.__viz.polling.startPolling()` 不报错，5 秒后 `state.stats` 有值。
- 在 Console 手动调 `state.view = '2d'`，看到 canvas 显示；`state.view = '3d'` 看到 3D 球；`state.view = 'list'` 回到列表。

**依赖**：Wave 1 + Wave 2 全部。

---

## Wave 4 — 根组件 + 入口（串行）

| ID | 文件 | 职责 | 源行 | 完成判据 |
|---|---|---|---|---|
| W4-01 | `js/components/graph-view.js` | GraphView（2D 容器） + Graph3DView（3D 容器） | 3352–3456 | 注册 `__viz.comp.GraphView` + `__viz.comp.Graph3DView`；2D 的 onMounted 调 resizeCanvas + wireGraphInteractions；2D 的 view watcher 在进入 2d 时先 deactivate3DGraph；3D 的 watcher 在进入 3d 时 activate、离开时 deactivate |
| W4-02 | `js/components/app.js` | App 根组件（URL 参数解析 + 初始化 probeHealth / loadStats / loadKnowledge / loadRelations / startPolling） | 3461–3520 | 注册 `__viz.comp.App`；onMounted await probeHealth → Promise.all 三 loader → startPolling → 读 url params `view` / `q`；Show(list) + GraphView + Graph3DView + DetailPanel 都挂载根下 |
| W4-03 | `index.html`（骨架重写） | ≤ 70 行；含 `<head>` + 6 个 CSS link + body 骨架 + 3 个 CDN script + 20+ 个 js 模块 script + mount 调用 | 整文件重写 | 文件 ≤ 70 行；`<div id="app"></div>` 保留；`<script>` 加载顺序见 INTERFACE-CONTRACTS 约束；最后一行 `<script>NovaView.mount(window.__viz.comp.App(), document.getElementById('app'));</script>`；严禁内联业务代码 |

**完成判据（整 Wave）**：
- `wc -l index.html` ≤ 70。
- 浏览器打开 `/viz_v2/index.html`（后端挂 /viz/static 静态），看到与拆分前视觉 100% 一致：
  - Topbar 显示 `● mnemo /viz · v2 | online | [搜索框] | List/2D/3D | 刷新`
  - List 页显示 Dashboard + ToolChart + KnowledgeList
  - 切 2D 看到 canvas 图谱 + GraphLegend + GraphMetricsPanel
  - 切 3D 看到 3D 球 + DOM 标签
  - 点任何卡片/节点弹 DetailPanel
- Console 无 warning/error（允许 nova-dom 的无害 info log）。

**依赖**：Wave 1 + 2 + 3 全部。

---

## Wave 5 — 验证（CDP 烟测）

| ID | 任务 | 完成判据 |
|---|---|---|
| W5-01 | 启动后端 + 打开页面 | `cd /Users/zhuqingyu/project/mnemo && MNEMO_HYBRID=1 .venv/bin/python -m mnemo.cli serve --port 8787 &`；用 `scripts/cdp_screenshot.py http://127.0.0.1:8787/viz/static/viz_v2/index.html out.png`，看到 Topbar + Dashboard + KnowledgeList |
| W5-02 | List → 2D 切换烟测 | CDP 点击 `.view-switch button:nth-child(2)`，等 1s，断言 `#graph-view.show` 存在 + `canvas#graph-canvas` 可见 + `window.__vizPerf.frameTimes.length > 0` |
| W5-03 | 2D → 3D 切换烟测 | CDP 点击 `.view-switch button:nth-child(3)`，等 2s，断言 `#graph-3d-view.show` 存在 + `#graph-3d-container canvas` 存在 + `window.__viz.state.graph3d.G` 非 null |
| W5-04 | 搜索 + 详情烟测 | CDP 向 `.search-wrap input` 派 input 事件值 `mnemo`，等 500ms，断言 `state.searchResults.length > 0`；点第一个 `.k-card`，断言 `.detail-overlay` 可见 |
| W5-05 | 空闲性能烟测 | 切到 2D + 等 10s，断言 `window.__vizPerf._lastActiveAt` 与 `performance.now()` 差值 > 5000（rAF 已停，空闲 CPU 0%）|
| W5-06 | 行数验证 | 对每个 .css / .js 跑 `wc -l`，所有文件 ≤ 200 行；`index.html` ≤ 70 行；列表写进验证报告 |
| W5-07 | 交付报告 | 写 `viz/viz_v2/SPLIT-REPORT.md`：(1) 所有文件 wc -l 表；(2) CDP 截图 4 张（list/2d/3d/detail）；(3) Console 无 error 的证据（screenshot of DevTools）；(4) 手动切换 200 次不崩的证据 |

**完成判据（整 Wave）**：所有 W5-0x 通过；生成 SPLIT-REPORT.md 并 commit。

**依赖**：Wave 4 全部。

---

## 总文件清单（最终交付）

| # | 路径 | 行数上限 | Wave |
|---|---|---|---|
| 1 | `index.html` | 70 | W4 |
| 2 | `css/tokens.css` | 30 | W1 |
| 3 | `css/base.css` | 20 | W1 |
| 4 | `css/dashboard.css` | 200 | W1 |
| 5 | `css/list.css` | 130 | W1 |
| 6 | `css/detail.css` | 130 | W1 |
| 7 | `css/graph.css` | 200 | W1 |
| 8 | `js/bootstrap.js` | 80 | W1 |
| 9 | `js/constants.js` | 60 | W1 |
| 10 | `js/state.js` | 80 | W1 |
| 11 | `js/utils.js` | 60 | W1 |
| 12 | `js/data-loaders.js` | 60 | W2 |
| 13 | `js/search.js` | 80 | W2 |
| 14 | `js/detail-actions.js` | 70 | W2 |
| 15 | `js/polling.js` | 50 | W3 |
| 16 | `js/graph2d/color.js` | 50 | W1 |
| 17 | `js/graph2d/sprite-cache.js` | 50 | W1 |
| 18 | `js/graph2d/build.js` | 120 | W2 |
| 19 | `js/graph2d/physics.js` | 180 | W2 |
| 20 | `js/graph2d/render.js` | 200 | W2 |
| 21 | `js/graph2d/loop.js` | 120 | W3 |
| 22 | `js/graph2d/diff.js` | 200 | W3 |
| 23 | `js/graph2d/interactions.js` | 180 | W3 |
| 24 | `js/graph2d/pulses.js` | 60 | W3 |
| 25 | `js/graph2d/search-match.js` | 20 | W3 |
| 26 | `js/graph3d/style.js` | 60 | W1 |
| 27 | `js/graph3d/data.js` | 160 | W2 |
| 28 | `js/graph3d/labels.js` | 130 | W2 |
| 29 | `js/graph3d/engine.js` | 120 | W3 |
| 30 | `js/graph3d/lifecycle.js` | 120 | W3 |
| 31 | `js/components/topbar.js` | 80 | W3 |
| 32 | `js/components/dashboard.js` | 160 | W3 |
| 33 | `js/components/tool-chart.js` | 50 | W3 |
| 34 | `js/components/list.js` | 100 | W3 |
| 35 | `js/components/detail-panel.js` | 160 | W3 |
| 36 | `js/components/graph-hud.js` | 180 | W3 |
| 37 | `js/components/graph-view.js` | 130 | W4 |
| 38 | `js/components/app.js` | 70 | W4 |

合计：38 个产物 + 1 个 `SPLIT-REPORT.md` 验证报告。

---

## 通用红线（所有 Wave 必须遵守）

1. **不改逻辑**：源代码的函数体一字不改。只改「怎么暴露」。
2. **不修 bug**：即使看到 bug 也不修，留给下一个任务。
3. **不加抽象**：不要创造新 helper、不合并相似逻辑、不加配置项。
4. **每文件头部 5–10 行注释**：职责 / 依赖 / 注册项（模板见 INTERFACE-CONTRACTS）。
5. **加载顺序严格按 INTERFACE-CONTRACTS 的"加载顺序约束"**，乱序即违规。
6. **不向 `state` 里塞 Map/Set**：reactive() 不兼容。必须放到 `_graphMaps` / `_3dMaps` / `_pulseSeenEventIds`。
7. **不用 ES module**：所有 JS 是传统 `<script>`。IIFE 注册 `window.__viz.*`，互相通过 `window.__viz` 读。
8. **不用构建工具**：无 bundler、无 sourcemap、无 TS。浏览器直开。

