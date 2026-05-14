# viz v2 架构方案

**目标**：把 `viz/viz_v1_live.html`（单文件 2487 行）用 nova-dom 重构为多文件组件化结构 `viz/viz_v2/`，每个代码文件 ≤ 200 行。

**分界**：所有 `innerHTML` 拼接的 UI 走 nova-dom；Canvas 2D 绘图、物理引擎、RAF 动画循环、拖拽/滚轮交互、颜色工具保持原生 JS（框架无感知，Canvas API 本来就不是 DOM）。

---

## 1. 目录树

```
viz/viz_v2/
├── index.html                       ~60 行   入口 HTML，只挂 <div id="app">、引 main.js
├── styles/
│   ├── tokens.css                   ~40 行   CSS 变量（颜色/字体/radius）
│   ├── base.css                     ~30 行   html/body/按钮全局样式
│   ├── topbar.css                   ~50 行   顶栏+搜索框+视图切换
│   ├── dashboard.css                ~80 行   metric 卡 + tool-chart
│   ├── knowledge.css                ~80 行   卡片网格 + pill + 状态色
│   ├── detail-panel.css             ~80 行   抽屉面板
│   ├── graph.css                    ~80 行   canvas/hud/tooltip/loading
│   ├── graph-panel.css              ~80 行   浮动 HUD（Live Metrics + tool bars）
│   └── states.css                   ~25 行   spinner/skeleton/err
├── main.js                          ~80 行   boot：loadAll + wireUI + mount
├── lib/
│   └── nova-dom.js                  外部     框架本体（由独立任务提供，不在本次范围）
├── core/
│   ├── state.js                     ~90 行   reactive 全局 state（含 graph 子状态）
│   ├── constants.js                 ~40 行   MAX_GRAPH_NODES / PULSE_* / FADE_* / TYPE_RANK
│   ├── api.js                       ~40 行   apiGet + API/HEALTH 常量 + probeHealth
│   ├── polling.js                   ~60 行   startPolling/stopPolling + refreshGraphIncremental 调度
│   └── utils.js                     ~50 行   escapeHtml/escapeAttr/formatTimeAgo/truncateTitle/parseHitIdsFromSummary
├── components/
│   ├── App.js                       ~80 行   根组件：Topbar + Show(view, {graph|list}) + DetailPanel
│   ├── Topbar.js                    ~90 行   brand + ApiState + SearchBar + ViewSwitch + 刷新
│   ├── SearchBar.js                 ~70 行   input + clear + debounce + Esc/⌘K
│   ├── ApiState.js                  ~40 行   健康探针状态点
│   ├── ViewSwitch.js                ~40 行   Graph/List 切换按钮组
│   ├── Dashboard.js                 ~120 行  4 张 metric 卡（结构 + bar-row）
│   ├── MetricCard.js                ~80 行   单张 metric 卡 + animateNumber tween
│   ├── ToolChart.js                 ~60 行   tool_call 横向柱状
│   ├── KnowledgeList.js             ~90 行   For(items) + KnowledgeCard
│   ├── KnowledgeCard.js             ~90 行   单卡：pills + title + summary + foot + onClick
│   ├── DetailPanel.js               ~140 行  抽屉 + overlay + 关闭 + 加载态
│   ├── DetailMeta.js                ~70 行   id/status/scope/version/time 列表
│   ├── FeedbackBar.js               ~40 行   3 个反馈 pill
│   ├── RelationList.js              ~70 行   关系列表（badge + 箭头 + peer）
│   ├── GraphView.js                 ~140 行  canvas 容器 + HUD + tooltip + loading + GraphPanel
│   ├── GraphHUD.js                  ~60 行   顶部 legend（nodes/edges + 图例）
│   ├── GraphPanel.js                ~150 行  右侧浮动 Live Metrics + tool bars + 折叠
│   └── GraphPanelRow.js             ~50 行   一行 metric（icon + label + animated value）
└── graph/
    ├── build.js                     ~120 行  buildGraph + buildTargetEdgeMap + nodeFillFor + spawnPositionOutside
    ├── diff.js                      ~140 行  computeDiff + applyDiff + refreshGraphIncremental（数据拉取+应用）
    ├── physics.js                   ~90 行   stepPhysics（n² 斥力 + 弹簧 + 向心 + damping）
    ├── render.js                    ~190 行  drawGraph（edges → nodes → pulses），纯 Canvas 2D
    ├── sprite-cache.js              ~50 行   getGlowSprite（离屏 canvas 缓存，替代 shadowBlur）
    ├── color.js                     ~60 行   parseColor/lerpColor/brightenColor
    ├── loop.js                      ~130 行  graphTick/startGraphLoop/stopGraphLoop/stepAnimations/markRenderDirty/markPhysicsActive/ensureRAF/hasLiveAnimations
    ├── interactions.js              ~170 行  wireGraphInteractions（mousedown/move/up/wheel/resize）+ pickNode/screenToWorld/resizeCanvas
    └── pulses.js                    ~70 行   pollSearchPulses + spawnPulsesForNodes（search pulse 专属数据通路）
```

**统计**：23 个 JS + 9 个 CSS + 1 个 HTML = 33 个文件。最大文件 `graph/render.js` 约 190 行，全部 ≤ 200 行。

---

## 2. 每文件职责一句话

### 入口层
| 文件 | 职责 | 行数 |
|---|---|---|
| `index.html` | 只放 `<div id="app">` 和 `<script type="module" src="./main.js">`，引所有 CSS | ~60 |
| `main.js` | `loadAll()` + `wireUI()` 非组件级键盘 + `mount(App, '#app')` | ~80 |

### styles/（纯 CSS，照搬 v1 规则，按功能拆）
每个文件 25–80 行，对应一个 UI 模块，规则无改动。`tokens.css` 是 `:root{--bg-*...}` 变量集中地。

### core/（无 UI 的基础设施）
| 文件 | 职责 | 行数 |
|---|---|---|
| `state.js` | `reactive({...})` 包装全局 state；导出 `state` 单例 + graph 子对象 | ~90 |
| `constants.js` | 所有魔法数字（MAX_GRAPH_NODES / ALPHA_* / PULSE_* / FADE_* / TYPE_RANK / HALO_DURATION_MS）、`VIZ_SMART_PAUSE` 解析 `?smartPause=0` | ~40 |
| `api.js` | `_origin` 解析（file:// fallback 到 127.0.0.1:8787）、`API` / `HEALTH` 常量、`apiGet(url)`、`probeHealth()` | ~40 |
| `polling.js` | `startPolling/stopPolling`，setInterval 调度 `refreshGraphIncremental` + `pollSearchPulses`，管理 `pollInFlight` | ~60 |
| `utils.js` | `escapeHtml / escapeAttr / formatTimeAgo / truncateTitle / parseHitIdsFromSummary` | ~50 |

### components/（nova-dom 组件）
| 文件 | 职责 | 行数 |
|---|---|---|
| `App.js` | 根：Topbar + `Show(state.view === 'graph', {when: GraphView, fallback: MainView})` + DetailPanel | ~80 |
| `Topbar.js` | 粘性顶栏容器，组合 ApiState / SearchBar / ViewSwitch / 刷新按钮 | ~90 |
| `SearchBar.js` | input + clear + debounce，触发 `runSearch` / `updateGraphSearchMatch` | ~70 |
| `ApiState.js` | 监听 `state.apiStatus`（ok/err/connecting）染色 + 文本 | ~40 |
| `ViewSwitch.js` | Graph/List 切换，点击调 `setView(v)` | ~40 |
| `Dashboard.js` | 4 张 MetricCard 的网格容器（list 视图下方） | ~120 |
| `MetricCard.js` | 单卡模板 + `animateNumber(el, val, 500)` tween | ~80 |
| `ToolChart.js` | `For(state.stats.tool_calls)` 渲染横向柱 | ~60 |
| `KnowledgeList.js` | `For(searchResults ?? knowledge, {key: k.id, children: KnowledgeCard})` + 空/加载态 | ~90 |
| `KnowledgeCard.js` | 单卡 UI + 点击 `openDetail(id)` | ~90 |
| `DetailPanel.js` | overlay + 抽屉，监听 `state.selectedId` 驱动打开，ESC 关闭 | ~140 |
| `DetailMeta.js` | 元信息表格（id/status/scope/...） | ~70 |
| `FeedbackBar.js` | helpful/misleading/outdated 3 个 pill | ~40 |
| `RelationList.js` | 关系列表，点 peer 调 `openDetail(peer_id)` | ~70 |
| `GraphView.js` | canvas + HUD + tooltip + loading + GraphPanel 的容器，`onMount` 调 `wireGraphInteractions()` | ~140 |
| `GraphHUD.js` | 顶部 legend + nodes/edges 计数（响应 `graph.nodes.length / graph.edges.length`） | ~60 |
| `GraphPanel.js` | 右侧浮动 Live Metrics（复用 stats） + tool bars + 折叠/展开 | ~150 |
| `GraphPanelRow.js` | 单行 metric（icon/label/animated value） | ~50 |

### graph/（Canvas + 物理 + 动画，纯原生 JS）
| 文件 | 职责 | 行数 |
|---|---|---|
| `build.js` | `buildGraph`：首次布局（圆形 + jitter）、调 `apiGet /relations`；`buildTargetEdgeMap` 去重 + `TYPE_RANK`；`nodeFillFor` 按 status/scope 定色；`spawnPositionOutside` 画布外出生点 | ~120 |
| `diff.js` | `computeDiff` 对比新旧 knowledge/edgeMap；`applyDiff` 应用增删改 + fade + 色 tween；`refreshGraphIncremental` 串起拉取 → diff → apply | ~140 |
| `physics.js` | `stepPhysics`：斥力（n² + 软化）+ 弹簧 + 向心 + damping + 速度 clamp；`aScale = alpha` 驱动整套 | ~90 |
| `render.js` | `drawGraph`：clear → 设 camera → edges（3 类 type + 发光/虚线/hover boost/search dim）→ nodes（halo/match/brighten/label）→ pulses 黄环；**最长文件** | ~190 |
| `sprite-cache.js` | `getGlowSprite(color, r, blur)` 缓存到 Map，避开 shadowBlur | ~50 |
| `color.js` | `parseColor / lerpColor / brightenColor` | ~60 |
| `loop.js` | `graphTick`（RAF 主循环）+ `stepAnimations`（fade/color-tween/pulse 过期）+ `ensureRAF / markRenderDirty / markPhysicsActive / hasLiveAnimations / startGraphLoop / stopGraphLoop`；`__vizPerf` 调试探针 | ~130 |
| `interactions.js` | `wireGraphInteractions`：mousemove（hover/tooltip/drag）、mousedown（抓 node 或 pan）、mouseup（click vs drag 判断）、wheel（zoom 围绕光标）、resize。含 `pickNode / screenToWorld / resizeCanvas` | ~170 |
| `pulses.js` | `pollSearchPulses` 拉 `/events/recent`，`parseHitIdsFromSummary` 提取 id，`spawnPulsesForNodes` 推入 `graph.pulses`；管理 `pulseSeenEventIds` 去重 + cap | ~70 |

---

## 3. 组件拆分表（nova-dom vs 原生）

### 用 nova-dom 组件（所有 innerHTML 拼接的 UI）
| 原 v1 区域 | v1 行号 | 新组件 |
|---|---|---|
| 顶栏（brand/api-state/search/view-switch/refresh） | 577-590 | `Topbar + ApiState + SearchBar + ViewSwitch` |
| Dashboard metrics-grid（4 张卡） | 840-965 | `Dashboard + MetricCard` |
| tool-chart | 1066-1087 | `ToolChart` |
| k-grid（卡片列表） | 1090-1135 | `KnowledgeList + KnowledgeCard` |
| Detail panel（抽屉 + feedback + relations） | 1137-1223 | `DetailPanel + DetailMeta + FeedbackBar + RelationList` |
| 浮动 HUD（顶部 legend） | 595-607 | `GraphHUD` |
| graph-panel（右侧 Live Metrics） | 613-661, 1000-1063 | `GraphPanel + GraphPanelRow` |
| tooltip（hover 节点信息） | 608, 2275-2295 | nova-dom 绑定 `state.graph.hover`，渲染在 `GraphView` 内 |

**关键技术点**：`animateNumber / animateGraphPanelNumber` 的 tween 逻辑移入 `MetricCard` / `GraphPanelRow` 内部 effect，监听 reactive value 变化时对 DOM 文本节点做 rAF 缓动 — nova-dom 的 ref 变更触发重渲后，组件 setup 内启动 tween 回调。

### 保持原生 JS（Canvas 2D / 帧循环 / 拖拽）
| 原 v1 区域 | v1 行号 | 新位置 |
|---|---|---|
| `stepPhysics`（n² 斥力 + 弹簧） | 1932-1997 | `graph/physics.js` |
| `drawGraph`（Canvas 绘制主干） | 1999-2213 | `graph/render.js` |
| `graphTick / stepAnimations / hasLiveAnimations / ensureRAF / markRenderDirty / markPhysicsActive` | 1720-1889 | `graph/loop.js` |
| `wireGraphInteractions`（mouse/wheel 事件） | 2238-2377 | `graph/interactions.js` |
| `getGlowSprite`（离屏 canvas 缓存） | 1692-1718 | `graph/sprite-cache.js` |
| `parseColor / lerpColor / brightenColor` | 1891-1923 | `graph/color.js` |
| `pickNode / screenToWorld / resizeCanvas / truncateTitle` | 2215-2236, 1651-1661 | `graph/interactions.js` + `core/utils.js` |
| `computeDiff / applyDiff / refreshGraphIncremental` | 1429-1577 | `graph/diff.js` |
| `buildGraph / buildTargetEdgeMap / nodeFillFor / spawnPositionOutside` | 1306-1421 | `graph/build.js` |
| `pollSearchPulses / parseHitIdsFromSummary / spawnPulsesForNodes` | 1579-1638 | `graph/pulses.js` + `core/utils.js` |

**规则**：Canvas API（`ctx.fillRect` / `arc` / `stroke` / `drawImage`）从不穿过 nova-dom。`GraphView` 组件 `onMount` 后拿到真实 `<canvas>` 元素，把它交给 `graph/interactions.js` 和 `graph/loop.js` 初始化；RAF 循环不走框架调度，直接 `requestAnimationFrame`。

---

## 4. 全局状态设计（reactive 改造）

v1 的 `state` 是裸对象，直接突变；v2 用 `reactive()` 包装使变化驱动重渲。

```js
// core/state.js
import { reactive, ref } from '../lib/nova-dom.js';

export const state = reactive({
  // 健康探针
  apiStatus: 'connecting',  // 'connecting' | 'ok' | 'err'
  apiStatusText: '',

  // 数据
  stats: null,
  knowledge: [],

  // 搜索
  searchQuery: '',
  searchResults: null,      // null = 未搜索；[] = 搜索无结果
  searching: false,

  // 详情
  selectedId: null,
  selectedDetail: null,

  // 视图
  view: 'graph',            // 'graph' | 'list'

  // 图
  graph: reactive({
    nodes: [],              // 注意：nodes/edges 数组内元素被物理循环每帧突变
    edges: [],              // → 不做细粒度响应式,只在 length 变化或 diff 完成后通过
    nodeIndex: new Map(),   //   手动 version ref++ 触发 HUD 重渲
    edgeIndex: new Map(),
    cam: { x: 0, y: 0, zoom: 1 },
    hover: null,            // 改 hover 要触发 tooltip 重渲
    drag: null,
    built: false,
    rafId: null,
    matched: new Set(),     // 搜索命中节点 id
    pulses: [],             // 搜索脉冲
    alpha: 1.0,
    physicsActive: true,
    renderDirty: true,
    camDirty: true,
    hoverDirty: false,
    version: 0,             // 手动 bump 触发 GraphHUD 重渲（count、legend）
  }),

  // 轮询
  pollTimer: null,
  pollIntervalMs: 1000,
  pollInFlight: false,

  pulseSeenEventIds: new Set(),
  pulseLastPollAt: 0,
});
```

**响应式边界**：
- `state.stats` / `state.knowledge` / `state.searchResults` / `state.selectedDetail` / `state.apiStatus` — **nova-dom 管**，变更驱动重渲。
- `state.graph.nodes[i].x` / `.y` / `.vx` — **物理循环每帧突变，绝不做细粒度响应**。图节点计数、legend 数字等浅层统计通过 `graph.version++` 手动触发 `GraphHUD` 重渲。
- `state.graph.hover` — 改了手动 `graph.hoverDirty = true; ensureRAF()` 触发 canvas 重绘；tooltip 组件也通过 `ref(hover)` 响应变化。
- `state.graph.matched` / `state.graph.pulses` — 同上，通过 ensureRAF 驱动 canvas；UI 层不直接读。

**原则**：UI 状态走响应式；物理/渲染状态走命令式 + ensureRAF。两者在 `GraphView onMount` 处桥接（搜索 ref 变化 → `updateGraphSearchMatch()` + `ensureRAF()`）。

---

## 5. 数据流图

```
┌─────────────── core/api.js ───────────────┐
│ apiGet(url) ──→ /stats /knowledge         │
│                 /relations /search        │
│                 /events/recent            │
└──────────────────────┬─────────────────────┘
                       │
              ┌────────┴─────────┐
              ▼                  ▼
    core/polling.js        main.js loadAll()
    (每 1s 拉取)           (首次加载)
              │                  │
              ▼                  ▼
    ┌────────────────── core/state.js ──────────────────┐
    │  reactive state: stats / knowledge / searchResults│
    │  graph subtree: nodes / edges / hover / matched   │
    └──┬───────────────────────────┬────────────────────┘
       │                           │
       │ 响应式订阅                  │ 命令式读写 + ensureRAF
       ▼                           ▼
 ┌──────────────┐           ┌────────────────┐
 │ nova-dom UI  │           │ graph/ (canvas)│
 │──────────────│           │────────────────│
 │ Topbar       │           │ physics.js     │
 │ Dashboard    │           │ render.js      │
 │ ToolChart    │           │ loop.js (RAF)  │
 │ KnowledgeList│           │ interactions.js│
 │ DetailPanel  │           │ diff.js        │
 │ GraphHUD     │           │ pulses.js      │
 │ GraphPanel   │           │ build.js       │
 └──────────────┘           └────────────────┘
       ▲                           ▲
       │                           │
       └── 用户交互（点击/输入）──────┘
              ↓
       触发 mutation → state 变 → 重渲 / ensureRAF
```

**组件间通信**：
- **单一数据源**：所有组件直接从 `core/state.js` import 同一个 reactive `state`，不用 props 透传。
- **动作（action）**：跨组件的动作（`openDetail` / `closeDetail` / `runSearch` / `setView` / `toggleGraphPanel`）写成导出函数，放 `components/` 旁的 `actions.js` 或就近放在最相关的组件文件里，直接被调用。
- **不搞 event bus**：nova-dom 没虚拟 DOM，订阅靠 reactive；动作是普通函数调用，最短路径。
- **Canvas 与 UI 的桥**：`state.graph.hover` 是唯一中继 — 组件读它显示 tooltip，canvas 读它画高亮描边/连线加粗。

---

## 6. 迁移策略

按 **先基建 → 再静态 UI → 最后动态 Canvas** 的顺序做，每步可独立验证、可回退。

### Step 0：环境准备
- 把 `lib/nova-dom.js` 放就位（独立任务产出，需等）。
- 写 `index.html` 骨架 + `main.js` 空壳：`mount(() => Dom.div().text('hello'), '#app')` 跑通，确认模块加载、CSS 生效。
- 跑起 `mnemo serve --port 8787`，确认 `/api/v1/stats`、`/api/v1/knowledge`、`/api/v1/relations`、`/api/v1/events/recent` 四个端点可用。

### Step 1：core 基建（无 UI）
1. `core/constants.js`（纯复制常量）
2. `core/utils.js`（escapeHtml、formatTimeAgo 等纯函数）
3. `core/api.js`（apiGet + probeHealth）
4. `core/state.js`（reactive state 定义）
5. 在 console 里手动 `apiGet(API + '/stats')` 验证可用。

### Step 2：静态 UI 组件（list 视图先跑通）
顺序：Topbar → Dashboard → ToolChart → KnowledgeList → DetailPanel。
每加一个组件就 `mount` 到 `#app` 并手动点击/搜索验证。DetailPanel 这一步就把 openDetail/closeDetail + 键盘 ESC 跑通。

**验收**：list 视图（`state.view = 'list'`）下完全等价 v1 — 视觉 diff、点击、搜索、详情抽屉全 work。

### Step 3：graph 模块 Canvas 基建
1. `graph/color.js` + `graph/sprite-cache.js`（纯工具）
2. `graph/build.js`（首次画图）
3. `graph/physics.js`（静态一帧验证力场）
4. `graph/render.js`（能画出节点+边就行）
5. `graph/loop.js`（RAF 跑起来，alpha-decay 生效）
6. `graph/interactions.js`（拖拽/缩放/hover）

**验收**：`state.view = 'graph'` 下能画出 v1 完整力导向图，拖拽、缩放、hover tooltip、alpha-decay 暂停 都正常，`__vizPerf.framesLast5s()` 在静止状态下趋近 0。

### Step 4：动态数据通路
1. `graph/diff.js`（computeDiff + applyDiff）
2. `core/polling.js`（1s 轮询）
3. `graph/pulses.js`（search pulse 从 `/events/recent` 驱动）
4. Dashboard / GraphPanel 的 animateNumber 衔接

**验收**：与 v1 行为完全一致 — 新建 knowledge 有 halo 飘入，删除有 fade out，状态变色有 tween，另一个 agent 调 search 有黄环脉冲。

### Step 5：收尾
1. 视图切换 `setView` 连通
2. `?q=xxx` URL 预填、`?smartPause=0` 禁用 alpha-decay
3. 键盘：`⌘K` 聚焦搜索、`ESC` 关详情/清搜索
4. 响应式边界对照 v1 逐项跑一遍（拖窗、切视图、网络离线）

### 回退策略
每步完成立即 git commit。任何一步发现 nova-dom 与 Canvas 桥接有坑，可停在 Step 2（list 视图 nova-dom + graph 视图保留 v1 单文件里的 canvas 逻辑 iframe 嵌入）作为中间稳定点。

---

## 7. 风险与决策

| 风险 | 处置 |
|---|---|
| nova-dom 无 vDOM，节点数组/Map 每帧突变会触发大量重渲 | `state.graph.nodes/edges` 不做深层响应，只暴露 `version++` 让 UI 层按需重渲 HUD。Canvas 走 ensureRAF 而非框架调度。 |
| `animateNumber` 在 MetricCard 内部维持 rAF 与 nova-dom 响应重渲冲突 | tween 只改 textNode 的 nodeValue，不触发组件 setup 重跑；组件 setup 通过 effect 订阅源值，源值到 tween 函数去驱动 DOM。 |
| `For(items)` 与 KnowledgeCard 的 `onClick(openDetail)` 在列表重渲时闭包失效 | 传 `k.id` 作为 key；onClick 用 id 从 state 按需拿，而非闭包 k。 |
| Graph 视图切回 List 时 RAF 不停 | `setView('list')` 调 `stopGraphLoop()`；`GraphView` 组件 onUnmount 兜底再调一次。 |
| CSS 跨文件层叠顺序依赖 | `index.html` 固定导入顺序：tokens → base → topbar → dashboard → knowledge → detail-panel → graph → graph-panel → states。全部用 class，不用 id 选择器，避开层叠坑。 |
| `lib/nova-dom.js` 还没到位 | Step 0 拿不到就不开工；架构文档先定，实现等框架。 |

---

## 8. 文件规模复核

全部 ≤ 200 行红线：

- 最长：`graph/render.js` ~190（drawGraph 主体 3 段：edges/nodes/pulses）
- 次长：`graph/interactions.js` ~170（五个事件 handler + 三个工具函数）
- 第三长：`graph/loop.js` ~130（RAF + stepAnimations + dirty 标记族）
- 其余组件/core/graph 文件多在 40–140 行区间
- CSS 全部 25–80 行

如果 `graph/render.js` 实装超限，立刻按 `drawEdges / drawNodes / drawPulses` 三拆。
