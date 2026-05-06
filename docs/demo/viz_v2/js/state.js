/**
 * js/state.js
 *
 * 职责：reactive state（含 graph 全部字段）+ _graphMaps + _3dMaps + _pulseSeenEventIds
 * 依赖：__viz.NV (reactive)
 * 注册：__viz.state, __viz._graphMaps, __viz._3dMaps, __viz._pulseSeenEventIds
 *
 * 注意：Map/Set/TypedArray 不能放进 reactive()，因为 Proxy 会打断原生方法的 receiver 绑定。
 *       3D 的 G/nodes/links 也不能放进 reactive —— 3d-force-graph 内部在 tick 时会
 *       操作节点上挂的 Float32Array（d3-force 的位置向量），Proxy 包裹会导致
 *       "Method get TypedArray.prototype.length called on incompatible receiver"。
 *       所以整个 3D 数据层放在 __viz._3dMaps 非响应式容器里。
 */
(function () {
  'use strict';

  const { reactive } = window.__viz.NV;

  // ---------- 全局响应式状态 ----------
  const state = reactive({
    // 网络 / 整体
    serverOnline: false,
    mnemoVersion: null,
    stats: null,
    knowledge: [],
    loaded: false,
    _rawRelations: null,

    // 搜索
    searchQuery: '',
    searchResults: null,
    searching: false,

    // 视图
    view: 'list',                   // 'list' | '2d' | '3d'

    // 详情
    selectedId: null,
    selectedDetail: null,
    selectedError: null,

    // polling — 5s interval balances "live" feel vs server CPU load.
    // Previous 1s interval caused ~20 req/s with multiple tabs, pinning CPU.
    pollTimer: null,
    pollIntervalMs: 5000,
    pollInFlight: false,
    pulseLastPollAt: 0,
    pulseSeenEventIds: null,        // 仅占位；真正的 Set 在 _pulseSeenEventIds 全局

    // 2D 图谱
    graph: {
      built: false,
      nodes: [],
      edges: [],
      edgeMap: {},
      cam: { x: 0, y: 0, zoom: 1 },
      hover: null,
      drag: null,
      pulses: [],
      alpha: 0.8,
      renderDirty: true,
      version: 0,

      // _graph.js 扩展字段（物理 / RAF）
      physicsActive: true,
      camDirty: true,
      hoverDirty: false,
      rafId: null,
    },

  });

  // ---------- Map/Set 放在 reactive 之外 ----------
  // rawNodes/rawEdges 是 g.nodes/g.edges 的非响应式镜像：持有相同 leaf 引用，
  // 但通过 raw 路径读取可完全绕过 Proxy trap（~22x 快）。热路径
  // (stepPhysics / drawGraph / stepAnimations) 必须从这里读，写入时同步。
  var _graphMaps = {
    nodeIndex: new Map(),
    edgeIndex: new Map(),
    matched: new Set(),
    rawNodes: [],
    rawEdges: [],
  };
  var _pulseSeenEventIds = new Set();
  // 3D 图谱数据层（非 reactive）— 见文件头注释
  var _3dMaps = {
    G: null,
    nodes: [],
    links: [],
    hoveredId: null,
    labelDistance: 120,
    labelEls: new Map(),
    nodesById: new Map(),
    searchMatchIds: new Set(),
  };

  // ---- 注册到 __viz ----
  window.__viz = window.__viz || {};
  window.__viz.state = state;
  window.__viz._graphMaps = _graphMaps;
  window.__viz._3dMaps = _3dMaps;
  window.__viz._pulseSeenEventIds = _pulseSeenEventIds;
})();
