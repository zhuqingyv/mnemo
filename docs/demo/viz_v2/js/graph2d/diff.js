/**
 * js/graph2d/diff.js — 2D 图谱增量 diff (computeDiff / applyDiff / refreshGraphIncremental) + search match
 * 注册：__viz.g2d.diff.{computeDiff, applyDiff, refreshGraphIncremental} + __viz.g2d.updateSearchMatch
 * applyDiff 写入同时镜像到 _graphMaps.rawNodes/rawEdges 供热路径零-Proxy 读取。
 */
(function () {
  'use strict';
  const state = window.__viz.state;
  const _graphMaps = window.__viz._graphMaps;
  const { apiGet, API } = window.__viz;
  const MAX_GRAPH_NODES = window.__viz.const.MAX_GRAPH_NODES;

  function computeDiff(newKnowledge, newEdgeMap) {
    const g = state.graph;
    const addedNodes = [];
    const removedNodeIds = [];
    const changedNodes = [];
    const addedEdges = [];
    const removedEdgeKeys = [];
    const newById = new Map();
    for (const k of newKnowledge) newById.set(k.id, k);
    for (const k of newKnowledge) {
      const existing = _graphMaps.nodeIndex.get(k.id);
      if (!existing) {
        addedNodes.push(k);
      } else if (!existing.removing) {
        const aa = existing.k;
        if (
          aa.status !== k.status ||
          aa.title !== k.title ||
          aa.scope !== k.scope ||
          (aa.tags || []).length !== (k.tags || []).length
        ) {
          changedNodes.push({ node: existing, next: k });
        } else {
          existing.k = k;
        }
      }
    }
    for (const n of g.nodes) {
      if (n.removing) continue;
      if (!newById.has(n.id)) removedNodeIds.push(n.id);
    }

    for (const [key, e] of newEdgeMap) {
      const existing = _graphMaps.edgeIndex.get(key);
      if (!existing || existing.removing) addedEdges.push(e);
      else if (existing.type !== e.type) existing.type = e.type;
    }
    for (const [key, e] of _graphMaps.edgeIndex) {
      if (e.removing) continue;
      if (!newEdgeMap.has(key)) removedEdgeKeys.push(key);
    }

    return { addedNodes, removedNodeIds, changedNodes, addedEdges, removedEdgeKeys };
  }

  function applyDiff(diff) {
    const g = state.graph;
    const now = performance.now();
    const { nodeFillFor, spawnPositionOutside } = window.__viz.g2d.build;
    for (const k of diff.addedNodes) {
      const p = spawnPositionOutside();
      const n = {
        id: k.id,
        k,
        x: p.x, y: p.y, vx: 0, vy: 0,
        r: 6 + Math.min(5, (k.tags || []).length * 0.5),
        fixed: false,
        opacity: 0,
        enteredAt: now,
        removing: false,
        removedAt: 0,
        fill: nodeFillFor(k),
        targetFill: nodeFillFor(k),
        fillFromColor: null,
        fillStartAt: 0,
        _frozen: false,
        _slowFrames: 0,
      };
      g.nodes.push(n);
      _graphMaps.rawNodes.push(n);
      _graphMaps.nodeIndex.set(n.id, n);
    }

    for (const id of diff.removedNodeIds) {
      const n = _graphMaps.nodeIndex.get(id);
      if (!n || n.removing) continue;
      n.removing = true;
      n.removedAt = now;
    }
    for (const { node, next } of diff.changedNodes) {
      node.k = next;
      const newFill = nodeFillFor(next);
      if (newFill !== node.targetFill) {
        node.fillFromColor = node.fill;
        node.targetFill = newFill;
        node.fillStartAt = now;
      }
      node.r = 6 + Math.min(5, (next.tags || []).length * 0.5);
    }
    for (const e of diff.addedEdges) {
      const ne = { s: e.s, t: e.t, type: e.type, opacity: 0, enteredAt: now, removing: false, removedAt: 0 };
      g.edges.push(ne);
      _graphMaps.rawEdges.push(ne);
      _graphMaps.edgeIndex.set(e.s + '-' + e.t, ne);
      const a = _graphMaps.nodeIndex.get(e.s);
      const b = _graphMaps.nodeIndex.get(e.t);
      if (a) { a._frozen = false; a._slowFrames = 0; }
      if (b) { b._frozen = false; b._slowFrames = 0; }
    }
    for (const key of diff.removedEdgeKeys) {
      const e = _graphMaps.edgeIndex.get(key);
      if (!e || e.removing) continue;
      e.removing = true;
      e.removedAt = now;
      const a = _graphMaps.nodeIndex.get(e.s);
      const b = _graphMaps.nodeIndex.get(e.t);
      if (a) { a._frozen = false; a._slowFrames = 0; }
      if (b) { b._frozen = false; b._slowFrames = 0; }
    }
    g.version++;
    window.__viz.g2d.physics.markPhysicsActive();
  }

  async function refreshGraphIncremental() {
    const g = state.graph;
    if (state.pollInFlight) return;
    state.pollInFlight = true;
    try {
      const [list, rels, stats] = await Promise.all([
        apiGet(API + '/knowledge?limit=500'),
        apiGet(API + '/relations?limit=5000'),
        apiGet(API + '/stats').catch(() => null),
      ]);
      const rawItems = list.results || [];
      const items = rawItems.length > MAX_GRAPH_NODES ? rawItems.slice(0, MAX_GRAPH_NODES) : rawItems;
      let touched = 0;
      let relationsChanged = false;
      const nextRelations = rels.results || [];

      // 2D graph: incremental diff (only if 2D graph is built)
      if (g.built) {
        const { buildTargetEdgeMap } = window.__viz.g2d.build;
        const idSet = new Set(items.map(k => k.id));
        const edgeMap = buildTargetEdgeMap(nextRelations, idSet);
        const diff = computeDiff(items, edgeMap);
        touched =
          diff.addedNodes.length + diff.removedNodeIds.length +
          diff.changedNodes.length + diff.addedEdges.length + diff.removedEdgeKeys.length;
        relationsChanged = diff.addedEdges.length > 0 || diff.removedEdgeKeys.length > 0;
        if (touched > 0) {
          applyDiff(diff);
        }
      } else {
        // 2D not built; detect change at data level for 3D refresh trigger
        const prevIds = new Set(state.knowledge.map(k => k.id));
        for (const k of items) if (!prevIds.has(k.id)) { touched++; break; }
        if (!touched && items.length !== state.knowledge.length) touched = 1;
      }

      if (touched > 0 || relationsChanged || !g.built) {
        state.knowledge = items;
        state._rawRelations = nextRelations;
      }
      if (stats) state.stats = stats;
      // 3D: diff persistent nodes/links in place to preserve positions; re-read same-ref;
      // add labels only for new nodes; don't rebuild #label-layer.
      const _3dMaps = window.__viz._3dMaps;
      if (_3dMaps && _3dMaps.G && touched > 0) {
        const d3 = window.__viz.g3d.data.diff3DGraphData();
        if (d3.touched) {
          const G = _3dMaps.G;
          G.graphData({ nodes: _3dMaps.nodes, links: _3dMaps.links });
          window.__viz.g3d.labels.updateLabelLayer(d3.addedIds, d3.removedIds);
          // Only kick sim on structural change; onEngineStop settles alpha.
          if ((d3.addedIds.length || d3.removedIds.length) && G.resumeAnimation) {
            G.resumeAnimation();
          }
        }
      }
    } catch (_) {
      // Silent — polling must not corrupt the UI.
    } finally {
      state.pollInFlight = false;
    }
  }

  function updateGraphSearchMatch() {
    _graphMaps.matched.clear();
    if (state.searchResults && state.searchResults.length) {
      for (const r of state.searchResults) _graphMaps.matched.add(r.id);
    }
    window.__viz.g2d.loop.markRenderDirty();
  }

  // ---- 注册到 __viz 命名空间 ----
  window.__viz.g2d = window.__viz.g2d || {};
  window.__viz.g2d.diff = { computeDiff, applyDiff, refreshGraphIncremental };
  window.__viz.g2d.updateSearchMatch = updateGraphSearchMatch;
})();
