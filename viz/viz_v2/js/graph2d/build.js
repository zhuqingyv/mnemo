/**
 * js/graph2d/build.js
 *
 * 职责：2D 图谱节点/边构建 — TYPE_RANK、nodeFillFor、spawnPositionOutside、buildGraph
 * 依赖：__viz.state, __viz._graphMaps, __viz.const.MAX_GRAPH_NODES, __viz.API, __viz.apiGet,
 *       __viz.g2d.physics.markPhysicsActive, __viz.g2d.loop.startGraphLoop
 * 注册：__viz.g2d.build.{buildTargetEdgeMap, nodeFillFor, spawnPositionOutside, buildGraph}
 */
(function () {
  'use strict';

  const state = window.__viz.state;
  const _graphMaps = window.__viz._graphMaps;
  const { apiGet, API } = window.__viz;
  const MAX_GRAPH_NODES = window.__viz.const.MAX_GRAPH_NODES;

  // ============ node / edge construction ============
  const TYPE_RANK = { contradicts: 3, supersedes: 2, related: 1 };

  function buildTargetEdgeMap(relationRows, idSet) {
    const m = new Map();
    for (const row of (relationRows || [])) {
      const src = row.source_id;
      const tgt = row.target_id;
      if (!idSet.has(src) || !idSet.has(tgt)) continue;
      if (src === tgt) continue;
      const aa = Math.min(src, tgt);
      const bb = Math.max(src, tgt);
      const key = aa + '-' + bb;
      const type = row.relation_type || 'related';
      const existing = m.get(key);
      if (!existing || (TYPE_RANK[type] || 0) > (TYPE_RANK[existing.type] || 0)) {
        m.set(key, { s: aa, t: bb, type });
      }
    }
    return m;
  }

  function nodeFillFor(k) {
    const status = k.status || 'active';
    if (status === 'stale') return '#d29922';
    if (status === 'superseded' || status === 'archived') return '#6e7681';
    const scope = k.scope || 'global';
    return scope === 'project' ? '#3fb950' : scope === 'session' ? '#6e7681' : '#58a6ff';
  }

  function spawnPositionOutside() {
    const c = document.getElementById('graph-canvas');
    if (!c) return { x: 0, y: 0 };
    const w = c.clientWidth;
    const h = c.clientHeight;
    const cam = state.graph.cam;
    const edge = Math.floor(Math.random() * 4);
    const margin = 80;
    let sx, sy;
    if (edge === 0)      { sx = Math.random() * w; sy = -margin; }
    else if (edge === 1) { sx = w + margin; sy = Math.random() * h; }
    else if (edge === 2) { sx = Math.random() * w; sy = h + margin; }
    else                 { sx = -margin; sy = Math.random() * h; }
    return { x: (sx - cam.x) / cam.zoom, y: (sy - cam.y) / cam.zoom };
  }

  async function buildGraph() {
    const g = state.graph;
    const loading = document.getElementById('graph-loading');
    if (loading) {
      loading.classList.remove('hidden');
      loading.textContent = 'building graph…';
    }

    const rawItems = state.knowledge;
    const items = rawItems.length > MAX_GRAPH_NODES ? rawItems.slice(0, MAX_GRAPH_NODES) : rawItems;
    if (rawItems.length > MAX_GRAPH_NODES) {
      console.warn('[viz] truncated ' + rawItems.length + ' → ' + MAX_GRAPH_NODES + ' nodes (MAX_GRAPH_NODES cap)');
    }

    const c = document.getElementById('graph-canvas');
    const cx = c.clientWidth / 2;
    const cy = c.clientHeight / 2;
    const R = Math.min(cx, cy) * 0.6;
    const builtNodes = items.map((k, i) => {
      const angle = (i / Math.max(1, items.length)) * Math.PI * 2;
      const jitter = (Math.random() - 0.5) * 40;
      return {
        id: k.id,
        k,
        x: cx + Math.cos(angle) * R + jitter,
        y: cy + Math.sin(angle) * R + jitter,
        vx: 0,
        vy: 0,
        r: 6 + Math.min(5, (k.tags || []).length * 0.5),
        fixed: false,
        opacity: 1,
        enteredAt: 0,
        removing: false,
        removedAt: 0,
        fill: nodeFillFor(k),
        targetFill: nodeFillFor(k),
        fillFromColor: null,
        fillStartAt: 0,
      };
    });
    g.nodes = builtNodes;
    // raw mirror — same leaf refs, no Proxy on reads (hot path uses this)
    _graphMaps.rawNodes = builtNodes.slice();
    _graphMaps.nodeIndex = new Map(builtNodes.map(n => [n.id, n]));

    const idSet = new Set(g.nodes.map(n => n.id));
    let edgeMap = new Map();
    try {
      let relationRows;
      if (Array.isArray(state._rawRelations)) {
        relationRows = state._rawRelations;
      } else {
        const r = await apiGet(API + '/relations?limit=5000');
        relationRows = r.results || [];
        state._rawRelations = relationRows;
      }
      edgeMap = buildTargetEdgeMap(relationRows, idSet);
    } catch (e) {
      if (loading) loading.textContent = 'edges load failed: ' + e.message;
    }
    const builtEdges = [];
    _graphMaps.edgeIndex = new Map();
    for (const [key, e] of edgeMap) {
      const ne = { s: e.s, t: e.t, type: e.type, opacity: 1, enteredAt: 0, removing: false, removedAt: 0 };
      builtEdges.push(ne);
      _graphMaps.edgeIndex.set(key, ne);
    }
    g.edges = builtEdges;
    _graphMaps.rawEdges = builtEdges.slice();

    g.built = true;
    g.version++;
    if (loading) loading.classList.add('hidden');

    window.__viz.g2d.physics.markPhysicsActive();
    window.__viz.g2d.loop.startGraphLoop();
  }

  // ---- 注册到 __viz 命名空间 ----
  window.__viz.g2d = window.__viz.g2d || {};
  window.__viz.g2d.build = { buildTargetEdgeMap, nodeFillFor, spawnPositionOutside, buildGraph };
})();
