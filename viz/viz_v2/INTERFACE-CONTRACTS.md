# viz_v2 INTERFACE CONTRACTS

本文档定义 viz_v2 拆分后所有模块共享的「契约」。
任何违反本契约的代码即为违规。

---

## 1. `window.__viz` — 全局命名空间（唯一通信枢纽）

每个 JS 模块通过 IIFE 读写 `window.__viz.<namespace>.<fn>`。
**禁止**向 `window` 直接挂任何非 `__viz` / `__vizPerf` 的全局。

### 1.1 完整接口清单

```js
window.__viz = {
  // --- Wave 1: bootstrap.js ---
  NV: { ref, reactive, computed, watch, watchEffect, effect, batch,
        Dom, component, mount, Show, For, Switch, onMounted, onUnmounted },
  DomTags: { div, span, button, input, h2, h3, h4, p, ul, li, a,
             canvas, section, main, header },
  COLORS: { active, stale, archived, superseded, related, contradicts, auto_related },
  API:    string,   // e.g. "http://127.0.0.1:8787/api/v1"
  HEALTH: string,   // e.g. "http://127.0.0.1:8787/health"
  apiGet: async (url) => Promise<any>,

  // --- Wave 1: constants.js ---
  const: {
    TYPE_RANK: { contradicts: 3, supersedes: 2, related: 1 },
    MAX_GRAPH_NODES: 2000,
    PULSE_WINDOW_SECONDS: 5,
    PULSE_DURATION_MS: 800,
    PULSE_MAX_RADIUS: 26,
    PULSE_SEEN_CAP: 500,
    ALPHA_DECAY: 0.98,
    ALPHA_MIN: 0.001,
    ALPHA_RESTART: 1.0,
    FADE_IN_MS: 300,
    FADE_OUT_MS: 300,
    EDGE_FADE_IN_MS: 500,
    COLOR_TWEEN_MS: 300,
    HALO_DURATION_MS: 2000,
    PROJECT_HUES_3D: number[],
    EDGE_STYLES_3D: { related, auto_related, supersedes, contradicts },
  },

  // --- Wave 1: state.js ---
  state: Proxy<reactive_state>,  // 见 §2
  _graphMaps: {                   // 见 §3
    nodeIndex: Map<number, Node2D>,
    edgeIndex: Map<string, Edge2D>,
    matched: Set<number>,
  },
  _3dMaps: {                      // 见 §3
    labelEls: Map<number, HTMLElement>,
    nodesById: Map<number, Node3D>,
    searchMatchIds: Set<number>,
  },
  _pulseSeenEventIds: Set<number>,  // 见 §3

  // --- Wave 1: utils.js ---
  util: {
    escapeHtml: (s: string) => string,
    formatTimeAgo: (iso: string) => string,
    sliceIso: (s: string) => string,
    truncateTitle: (s: string, maxLen: number) => string,
    syncViewToUrl: () => void,
  },

  // --- Wave 2: data-loaders.js ---
  loader: {
    probeHealth: async () => void,     // 写 state.serverOnline
    loadStats:   async () => void,     // 写 state.stats
    loadKnowledge: async () => void,   // 写 state.knowledge + state.loaded=true
    loadRelations: async () => void,   // 写 state._rawRelations
  },

  // --- Wave 2: search.js ---
  search: {
    runSearch:     (q: string) => void,   // 立即发请求
    onSearchInput: (v: string) => void,   // debounced 220ms
    clearSearch:   () => void,
    triggerSearch: (q: string) => void,   // onSearchInput 的别名
  },

  // --- Wave 2: detail-actions.js ---
  detail: {
    openDetail:  async (id: number) => void,
    closeDetail: () => void,
  },

  // --- Wave 1+2: graph2d namespace ---
  g2d: {
    // Wave 1
    color: {
      parseColor:    (c: string) => [r,g,b],
      lerpColor:     (from, to, t) => string,
      brightenColor: (c, t) => string,
    },
    getGlowSprite: (color, nodeRadius, blurStrength) => HTMLCanvasElement,

    // Wave 2
    build: {
      buildTargetEdgeMap: (relationRows, idSet) => Map<string, {s,t,type}>,
      nodeFillFor: (k) => string,
      spawnPositionOutside: () => {x, y},
      buildGraph: async () => void,        // 写 state.graph.nodes/edges, _graphMaps.*
    },
    physics: {
      stepPhysics: () => void,             // 每帧调用
      markPhysicsActive: () => void,       // 交互触发重启 physics
    },
    render: {
      drawGraph: () => void,               // 每帧调用
    },

    // Wave 3
    loop: {
      startGraphLoop: () => void,
      stopGraphLoop:  () => void,
      ensureRAF:      () => void,
      markRenderDirty: () => void,
      markPhysicsActive: () => void,       // alias to physics.markPhysicsActive
    },
    diff: {
      computeDiff: (newKnowledge, newEdgeMap) => { addedNodes, removedNodes, addedEdges, removedEdges, ... },
      applyDiff:   (diff) => void,
      refreshGraphIncremental: async () => void,
    },
    interactions: {
      resizeCanvas: () => void,
      pickNode: (sx, sy) => Node2D | null,
      wireGraphInteractions: () => void,   // 幂等
    },
    pulses: {
      parseHitIdsFromSummary: (summary) => number[],
      spawnPulsesForNodes:    (ids) => void,
      pollSearchPulses:       async () => void,
    },
    updateSearchMatch: () => void,         // 写 _graphMaps.matched
  },

  // --- Wave 1+2+3: graph3d namespace ---
  g3d: {
    // Wave 1
    style: {
      projectColor3D: (name) => string|null,
      nodeColor3D:    (node) => string,
      edgeStyle3D:    (type) => {color, width},
      truncTitle3D:   (title) => string,
    },

    // Wave 2
    data: {
      build3DGraphData:      () => { nodes, links },
      diff3DGraphData:       () => { addedIds, removedIds, touched },
      recomputeEdgeCounts3D: () => void,
    },
    labels: {
      rebuildLabels:     (nodes) => void,
      updateLabelLayer:  (addedIds, removedIds) => void,
      updateLabels:      (G) => void,      // 每 tick
    },

    // Wave 3
    initGraph: () => ForceGraph3DInstance | null,
    lifecycle: {
      recolorNode:       (nodeId) => void,
      updateSearchMatch: () => void,
      activate:          () => void,
      deactivate:        () => void,
    },
  },

  // --- Wave 3: polling.js ---
  polling: {
    startPolling: () => void,
    stopPolling:  () => void,
  },

  // --- Wave 3+4: components ---
  comp: {
    Topbar:            () => NovaComponent,
    Dashboard:         () => NovaComponent,
    ToolChart:         () => NovaComponent,
    KnowledgeList:     () => NovaComponent,
    KnowledgeCard:     (k) => NovaComponent,
    DetailPanel:       () => NovaComponent,
    GraphLegend:       () => NovaComponent,
    GraphMetricsPanel: () => NovaComponent,
    GraphView:         () => NovaComponent,
    Graph3DView:       () => NovaComponent,
    App:               () => NovaComponent,
  },
};
```

### 1.2 性能观测命名空间（独立）

`window.__vizPerf`（loop.js 初始化）：
```js
window.__vizPerf = {
  frameTimes: number[],   // performance.now() 序列，窗口 700
  _lastActiveAt: number,  // 最近一次 rAF 时刻（空闲检测用）
};
```

---

## 2. `state` 字段清单（reactive 响应式对象）

```js
state = reactive({
  // 网络 / 整体
  serverOnline: false,
  stats: null,                    // GET /stats
  knowledge: [],                  // GET /knowledge?limit=500
  loaded: false,
  _rawRelations: [],              // GET /relations?limit=5000

  // 搜索
  searchQuery: '',
  searchResults: null,            // null=未搜索，[]=无结果，[...]=有命中
  searching: false,

  // 视图
  view: 'list',                   // 'list' | '2d' | '3d'

  // 详情
  selectedId: null,
  selectedDetail: null,
  selectedError: null,

  // polling
  pollTimer: null,
  pollIntervalMs: 5000,           // 初始 5000；_graph.js 扩展段会重置为 1000
  pollInFlight: false,
  pulseLastPollAt: 0,
  pulseSeenEventIds: null,        // 仅占位；真正的 Set 在 _pulseSeenEventIds 全局

  // 2D 图谱
  graph: {
    built: false,
    nodes: [],                    // Node2D[]，id/x/y/vx/vy/r/fixed/opacity/fill/...
    edges: [],                    // Edge2D[]，s/t/type/opacity/...
    edgeMap: {},                  // 已弃用，保留占位
    cam: { x: 0, y: 0, zoom: 1 },
    hover: null,                  // 当前 hover 的 node ref
    drag: null,                   // { node, dxOffset, dyOffset } | { pan, lastX, lastY }
    pulses: [],                   // { nodeId, startedAt }
    alpha: 0.8,
    renderDirty: true,
    version: 0,

    // _graph.js 扩展的字段（物理/RAF）
    physicsActive: true,
    camDirty: true,
    hoverDirty: false,
    rafId: null,
  },

  // 3D 图谱
  graph3d: {
    G: null,                      // ForceGraph3DInstance
    nodes: [],                    // 持久引用，engine 直接操作
    links: [],                    // 持久引用
    hoveredId: null,
    labelDistance: 120,
  },
});
```

### 2.1 字段所有权（哪些文件写）

| 字段 | 写入方（文件） | 读取方（文件） |
|---|---|---|
| `serverOnline` | data-loaders.js | topbar.js |
| `stats` | data-loaders.js | dashboard.js / tool-chart.js / graph-hud.js |
| `knowledge` | data-loaders.js | list.js / graph2d/build.js / graph3d/data.js |
| `_rawRelations` | data-loaders.js | graph2d/build.js / graph2d/diff.js / graph3d/data.js |
| `searchQuery / searchResults / searching` | search.js | topbar.js / list.js / graph2d/search-match.js / graph3d/lifecycle.js |
| `view` | topbar.js / app.js（URL init） / utils.js（popstate） | graph-view.js / graph2d/loop.js / graph2d/diff.js / graph3d/lifecycle.js / polling.js |
| `selectedId / selectedDetail / selectedError` | detail-actions.js | detail-panel.js / graph3d/lifecycle.js（recolor） |
| `pollTimer / pollIntervalMs / pollInFlight` | polling.js | — |
| `graph.*` | graph2d/build.js / diff.js / physics.js / loop.js / interactions.js / pulses.js | graph2d/render.js |
| `graph3d.*` | graph3d/data.js / engine.js / lifecycle.js | graph3d/labels.js |

---

## 3. `_graphMaps` / `_3dMaps` / `_pulseSeenEventIds` 接口

**重要**：Map 和 Set **不能**放进 `reactive()`，因为 Proxy 会打断原生 Map/Set 的方法接收者绑定（导致 `TypeError: receiver is not a Map`）。所以它们作为 `__viz` 的顶层属性存在，由多个模块共享。

### 3.1 `__viz._graphMaps`

```js
{
  nodeIndex: Map<nodeId:number, Node2D>,    // 2D 节点 id → 对象
  edgeIndex: Map<"min-max":string, Edge2D>, // 2D 边 key → 对象
  matched:   Set<nodeId:number>,             // 搜索命中的 2D 节点 id
}
```

- **写入方**：`graph2d/build.js`（初始化）、`graph2d/diff.js`（增量）、`graph2d/loop.js`（stepAnimations 里 splice 时同步删除）、`graph2d/search-match.js`（matched）。
- **读取方**：`graph2d/pulses.js`（判断 nodeId 是否存在）、`graph2d/render.js`（matched 上色）。

### 3.2 `__viz._3dMaps`

```js
{
  labelEls:       Map<nodeId:number, HTMLElement>,
  nodesById:      Map<nodeId:number, Node3D>,
  searchMatchIds: Set<nodeId:number>,
}
```

- **写入方**：`graph3d/data.js`（nodesById）、`graph3d/labels.js`（labelEls）、`graph3d/lifecycle.js`（searchMatchIds + updateSearchMatch）。
- **读取方**：`graph3d/engine.js`（nodesById 查 click node）、`graph3d/labels.js`（labelEls diff）、`graph3d/lifecycle.js`（recolorNode 查 nodesById）。

### 3.3 `__viz._pulseSeenEventIds`

```js
Set<eventId:number>   // 最近见过的 monitor_event.id，避免重复脉冲
```

- **写入方**：`graph2d/pulses.js`（pollSearchPulses 每次轮询 add，超 PULSE_SEEN_CAP 滑动淘汰）。
- **读取方**：`graph2d/pulses.js`（自身 dedupe）。

---

## 4. 文件模板（所有 JS 必须遵守）

```js
/**
 * <文件相对路径>
 *
 * 职责：<一句话>
 * 依赖：<读哪些 __viz.*>（用 ", " 分隔，如 "__viz.state, __viz.const, __viz.NV"）
 * 注册：<在 __viz.* 下注册了什么>（如 "__viz.loader.{probeHealth,loadStats,loadKnowledge,loadRelations}"）
 */
(function () {
  'use strict';

  const { ref, reactive, watch /*, ...only what you use*/ } = window.__viz.NV;
  const { div, span /*, ...*/ } = window.__viz.DomTags;
  const state = window.__viz.state;
  const { apiGet, API } = window.__viz;
  // 业务代码 ...

  // ---- 注册到 __viz 命名空间 ----
  window.__viz.loader = {
    probeHealth,
    loadStats,
    loadKnowledge,
    loadRelations,
  };
})();
```

### 4.1 解构规则

- **只解构会用到的 API**。不要 `const NV = window.__viz.NV;` 整体引用，可读性差且不便于追踪。
- **`state` / `apiGet` / `API` 例外**：这三者用得太频繁，每个文件里直接解构没问题。

### 4.2 注册规则

- **每个文件只注册一次**。注册代码放在文件尾部的 `// ---- 注册 ----` 段落。
- **严禁条件注册**（`if (!__viz.xxx) __viz.xxx = ...`）。每个命名空间由唯一文件拥有。
- **命名空间覆盖会报错**：如 `g2d.loop` 必须由 `graph2d/loop.js` 唯一注册；`graph2d/physics.js` 不得写 `__viz.g2d.loop = ...`。

---

## 5. 加载顺序约束

`index.html` 的 `<script>` 标签必须**严格按**以下顺序出现（上层的只依赖已加载的）：

```html
<!-- CDN 依赖 -->
<script src="/viz/static/nova-dom.umd.min.js"></script>
<script>if(typeof NovaView==='undefined'){document.write('<script src="nova-dom.umd.min.js"><\/script>');}</script>
<script src="https://unpkg.com/three@0.160.0/build/three.min.js"></script>
<script src="https://unpkg.com/3d-force-graph@1.73.4/dist/3d-force-graph.min.js"></script>

<!-- Wave 1: 零依赖层 -->
<script src="js/bootstrap.js"></script>          <!-- 必须第一个。挂 event patch + 解构 NV/DomTags + COLORS/API/apiGet -->
<script src="js/constants.js"></script>
<script src="js/state.js"></script>              <!-- 依赖 NV（reactive）+ const -->
<script src="js/utils.js"></script>              <!-- 依赖 state（syncViewToUrl） -->
<script src="js/graph2d/color.js"></script>
<script src="js/graph2d/sprite-cache.js"></script>
<script src="js/graph3d/style.js"></script>

<!-- Wave 2: 服务层 -->
<script src="js/data-loaders.js"></script>       <!-- 依赖 state, apiGet -->
<script src="js/search.js"></script>              <!-- 依赖 state, apiGet -->
<script src="js/detail-actions.js"></script>     <!-- 依赖 state, apiGet -->
<script src="js/graph2d/build.js"></script>      <!-- 依赖 state, _graphMaps, apiGet, g2d.color -->
<script src="js/graph2d/physics.js"></script>    <!-- 依赖 state -->
<script src="js/graph2d/render.js"></script>     <!-- 依赖 state, _graphMaps, g2d.color, g2d.getGlowSprite, util.truncateTitle -->
<script src="js/graph3d/data.js"></script>        <!-- 依赖 state, _3dMaps -->
<script src="js/graph3d/labels.js"></script>     <!-- 依赖 state, _3dMaps, g3d.style -->

<!-- Wave 3: loop / diff / interactions / 组件 -->
<script src="js/graph2d/loop.js"></script>        <!-- 依赖 state, const, g2d.physics, g2d.render -->
<script src="js/graph2d/diff.js"></script>        <!-- 依赖 state, _graphMaps, g2d.build, g2d.loop, g3d.data, g3d.lifecycle -->
<script src="js/graph2d/interactions.js"></script><!-- 依赖 state, g2d.loop, detail.openDetail -->
<script src="js/graph2d/pulses.js"></script>     <!-- 依赖 state, _graphMaps, _pulseSeenEventIds, const, apiGet, API, g2d.loop -->
<script src="js/graph2d/search-match.js"></script><!-- 依赖 state, _graphMaps, g2d.loop -->
<script src="js/graph3d/engine.js"></script>      <!-- 依赖 state, _3dMaps, g3d.style, g3d.labels, detail.openDetail, window.ForceGraph3D -->
<script src="js/graph3d/lifecycle.js"></script>  <!-- 依赖 state, _3dMaps, g3d.initGraph, g3d.data, g3d.labels, g3d.style, g2d.loop.stopGraphLoop -->
<script src="js/polling.js"></script>             <!-- 依赖 state, g2d.diff, loader -->

<script src="js/components/topbar.js"></script>
<script src="js/components/dashboard.js"></script>
<script src="js/components/tool-chart.js"></script>
<script src="js/components/list.js"></script>
<script src="js/components/detail-panel.js"></script>
<script src="js/components/graph-hud.js"></script>

<!-- Wave 4: 组合根 -->
<script src="js/components/graph-view.js"></script>
<script src="js/components/app.js"></script>

<!-- mount -->
<script>NovaView.mount(window.__viz.comp.App(), document.getElementById('app'));</script>
```

### 5.1 顺序的不变量（violation → 页面启动即崩）

1. `bootstrap.js` **必须**第一个加载；它挂 event 小写化 patch，晚加载会导致早期 addEventListener 注册的 'Click' 事件永远不触发。
2. `state.js` 必须在 `graph.js` 扩展字段 (`_graph.js` 段) 之前 — 现有做法是 `state.js` 初始化 `state.graph` 含基础字段，后续模块用 `if (state.graph.xxx == null) state.graph.xxx = ...` 幂等扩展。统一到 `state.js` 里一次性声明完。
3. `g2d/diff.js` 必须晚于 `g3d/lifecycle.js` 吗？ — **否**。两者互相引用：diff 调 `g3d.data.diff3DGraphData`，而 lifecycle 不调 diff。顺序以「被调用方先加载」为准：先 g3d.data / g3d.labels → 再 g3d.lifecycle → 再 g2d.diff。
4. `g3d/engine.js` 必须在 `window.ForceGraph3D` 可用后加载；`3d-force-graph.min.js` 的 CDN `<script>` 必须在其之前。
5. `components/app.js` 依赖所有 comp.* 已注册，必须倒数第二（只能在 `graph-view.js` 之后）。
6. `mount(App())` 只能出现一次，在最后一个 `<script>` 内联块。

---

## 6. 禁止事项（反模式清单）

| # | 反模式 | 原因 |
|---|---|---|
| 1 | 向 `state` 里塞 `new Map()` 或 `new Set()` | reactive() Proxy 打断原生 Map/Set 方法接收者，运行时 throw |
| 2 | 在同一 `__viz.<ns>` 下由多个文件分别覆盖注册 | 顺序敏感，先注册的被覆盖；每个命名空间唯一文件拥有 |
| 3 | 用 ES module `import/export` | 本项目要求零构建、浏览器直开 |
| 4 | 把业务函数挂到 `window.*`（而不是 `window.__viz.*`） | 污染全局、与 nova-dom 冲突风险；统一入口 |
| 5 | 在 Wave 1 文件里读 DOM（`document.getElementById`） | 加载时 body 已在，但 #graph-canvas 之类要等 component mount；Wave 1 保持无 DOM 副作用 |
| 6 | 在 `reactive()` 字段里放 function 或 rAF id | pollTimer / rafId 允许放（只是原始 number/id），但 Set/Map 不允许 |
| 7 | 用 `setInterval` 跑 graph tick | graphTick 必须用 requestAnimationFrame，否则失焦时仍跑，CPU 永不为 0 |
| 8 | 修改函数签名「顺便清理」 | 拆分阶段一字不改。清理放后续专项任务 |
| 9 | 合并两个函数或拆一个函数 | 同上。保持一对一映射 |
| 10 | 不加载 `bootstrap.js` 的 event patch 就 addEventListener | nova-dom 会注册成 `Click`（大写 C），事件永不触发 |
| 11 | 让 `Graph3DView` / `GraphView` 被 `Show()` 包裹 | `onMounted` 在 Show off 时不触发，init 永远不跑；必须常挂、用 CSS `display`（`.show` class）切换 |
| 12 | 在 `resume` 3D 之前不调 `stopGraphLoop` | 2D rAF + 3D 引擎同时跑会 CPU 爆；2D/3D 互斥 |
| 13 | 把 CDN 改版本（three / 3d-force-graph） | 已有 mnemo 知识：three 0.160 + 3d-force-graph 1.73.4 是唯一已验证组合；其他版本 UMD 404 或不兼容 |
| 14 | 把 `linkWidth` 设 > 0 | three-forcegraph 每边变独立 Mesh，1000 边 FPS 跌到 3。保持 0 |
| 15 | 用内联 `<style>`（拆分后） | CSS 全部外置，`<head>` 只有 `<link>` |

---

## 7. 快速自检清单（交付前必跑）

- [ ] 浏览器打开 `/viz_v2/index.html`，Console 无 `Uncaught` error / warning。
- [ ] `Object.keys(window.__viz)` 输出 20 项（NV, DomTags, COLORS, API, HEALTH, apiGet, const, state, _graphMaps, _3dMaps, _pulseSeenEventIds, util, loader, search, detail, g2d, g3d, polling, comp, + 自身扩展）。
- [ ] `window.__viz.state.view = '2d'` + 等 1s，`#graph-view.show` 存在。
- [ ] `window.__viz.state.view = '3d'` + 等 2s，`#graph-3d-view.show` 存在且 `__viz.state.graph3d.G` 非 null。
- [ ] 空闲 10s 后 `performance.now() - __vizPerf._lastActiveAt > 5000`（2D rAF 停了）。
- [ ] 每个模块文件 ≤ 200 行；`index.html` ≤ 70 行。
- [ ] `grep -rn "window\." js/` 只出现 `window.__viz.*` / `window.__vizPerf.*` / `window.addEventListener` / `window.devicePixelRatio` / `window.ForceGraph3D` / `window.location`，无其他全局污染。
